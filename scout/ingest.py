"""Multi-modal ingestion engine for SNP Memory System V2.

Core ingestion logic for PostgreSQL 16 + pgvector with Anthropic Contextual Chunking.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from scout.capabilities import capability_fingerprint
from scout.chunker import (
    ContextualChunker,
    Embedder,
    EmbeddingError,
    LiteLLMBatchEmbedder,
)
from scout.config import postgres_settings
from scout.parsers import ParsedDocument, parse_file
from scout.policy import PolicyValidationError, validate_document_acl

# Optional instrumentation hook used by the live sync-job. The ingestion layer
# reports facts it alone can observe, while the caller owns correlation and log
# formatting. Keeping this synchronous makes observation unable to add network
# or scheduling work between the measured stages.
IngestStageObserver = Callable[[str, Mapping[str, object]], None]


def _observe_ingest_stage(
    observer: IngestStageObserver | None,
    stage: str,
    **details: object,
) -> None:
    """Report one bounded ingestion stage when an observer was supplied."""
    if observer is not None:
        observer(stage, details)


def validate_allowed_depts(values: list[str]) -> list[str]:
    """Return a stable, unique document ACL list or fail before any I/O."""
    if not values:
        raise ValueError("allowed departments must not be empty")
    normalized: list[str] = []
    for value in values:
        try:
            department = validate_document_acl(value)
        except PolicyValidationError as exc:
            raise ValueError(str(exc)) from exc
        if department not in normalized:
            normalized.append(department)
    return normalized


#: Name of the checked-in document ACL map, read relative to the corpus root.
DEFAULT_ACL_FILENAME = ".acl.yaml"
#: Schema version this loader understands. A map must declare it explicitly so a
#: future format change cannot be misread as a grant.
ACL_SCHEMA_VERSION = 1


class CapabilityMismatchError(RuntimeError):
    """This environment would build a different corpus than the recorded one.

    Not a compatibility problem to negotiate: the corpus and the runtime must
    agree about what the parser can do, or evidence appears and disappears
    depending on where ingestion happened to run.
    """


class AclPolicyError(ValueError):
    """Raised when the document ACL map is absent, malformed, or non-canonical."""


@dataclass(frozen=True, slots=True)
class AclRule:
    """One path glob and the document ACL that matching files receive."""

    pattern: str
    departments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentAclMap:
    """First-match-wins ``path`` → ``allowed_depts`` map for one corpus root.

    The map is the *only* authority on a document's ACL. There is deliberately
    no implicit default: a file that matches no rule is denied — it is not
    ingested at all — rather than silently published as ``all``, the public
    document ACL. A genuinely public corpus is still expressible, but it has to
    be typed out as a trailing ``path: "**"`` rule granting ``[all]``.

    Attributes:
        base_dir: Corpus root that rule patterns are relative to.
        rules: Ordered rules; the first whose pattern matches wins.
        source_path: The file the map was loaded from, for diagnostics.
    """

    base_dir: Path
    rules: tuple[AclRule, ...]
    source_path: Path

    @classmethod
    def from_file(cls, path: Path, base_dir: Path | None = None) -> DocumentAclMap:
        """Load and validate an ACL map, failing closed on anything unusable.

        Args:
            path: The YAML map to read.
            base_dir: Corpus root the patterns address. Defaults to the
                directory holding the map, which is the checked-in layout
                (``raw/.acl.yaml`` governs ``raw/``).

        Raises:
            AclPolicyError: The file is unreadable, is not valid YAML, declares
                an unknown schema version, or carries a rule whose departments
                are not canonical document ACL values.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AclPolicyError(f"document ACL map is unreadable: {path}") from exc
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise AclPolicyError(f"document ACL map is not valid YAML: {path}") from exc
        if not isinstance(document, dict):
            raise AclPolicyError(f"document ACL map must be a mapping: {path}")
        if document.get("version") != ACL_SCHEMA_VERSION:
            raise AclPolicyError(
                f"document ACL map must declare version: {ACL_SCHEMA_VERSION}"
            )
        raw_rules = document.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise AclPolicyError("document ACL map must carry a nonempty rules list")

        rules: list[AclRule] = []
        for index, entry in enumerate(raw_rules):
            if not isinstance(entry, dict):
                raise AclPolicyError(f"ACL rule {index} must be a mapping")
            pattern = entry.get("path")
            if not isinstance(pattern, str) or not pattern.strip():
                raise AclPolicyError(f"ACL rule {index} needs a nonempty path pattern")
            departments = entry.get("departments")
            if not isinstance(departments, list) or not all(
                isinstance(item, str) for item in departments
            ):
                raise AclPolicyError(
                    f"ACL rule {index} needs a departments list of strings"
                )
            try:
                validated = validate_allowed_depts(list(departments))
            except ValueError as exc:
                raise AclPolicyError(f"ACL rule {index}: {exc}") from exc
            rules.append(AclRule(pattern=pattern.strip(), departments=tuple(validated)))

        root = base_dir if base_dir is not None else path.parent
        return cls(base_dir=root.resolve(), rules=tuple(rules), source_path=path)

    def departments_for(self, file_path: Path) -> list[str] | None:
        """Return one file's document ACL, or ``None`` when no rule grants it.

        ``None`` is a denial, never a hint to fall back to a default. A path
        outside ``base_dir`` — including one reached through a symlink — is
        denied for the same reason: no rule can address it.
        """
        try:
            relative = file_path.resolve().relative_to(self.base_dir)
        except (OSError, ValueError):
            return None
        parts = relative.parts
        for rule in self.rules:
            if _pattern_matches(rule.pattern, parts):
                return list(rule.departments)
        return None


def _pattern_matches(pattern: str, parts: tuple[str, ...]) -> bool:
    """Match a POSIX-style glob against an already-split relative path.

    ``**`` spans zero or more path segments; every other wildcard is confined to
    a single segment, so ``architecture/*`` cannot reach a nested directory.
    """
    segments = tuple(segment for segment in pattern.strip("/").split("/") if segment)
    return _match_segments(segments, parts)


def _match_segments(pattern: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    """Recursively match pattern segments against path segments."""
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(
            _match_segments(rest, parts[index:]) for index in range(len(parts) + 1)
        )
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], head) and _match_segments(rest, parts[1:])


def _is_control_metadata(path: Path, root: Path) -> bool:
    """Return whether a scanned path is hidden corpus metadata rather than evidence.

    ``raw/.acl.yaml`` is policy about the corpus, not part of it; ingesting it
    would publish the access-control map as a retrievable document.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


async def get_pg_connection(env: Mapping[str, str] | None = None) -> asyncpg.Connection:
    """Create an ingest connection, from `env` when supplied.

    `env` exists because the CLI already resolved configuration — including the
    project `.env` — before this is reached, and reading `os.environ` here threw
    that away: `snpmemory ingest` failed with `POSTGRES_HOST is missing` unless
    the operator had exported `.env` by hand. The same defect was fixed for
    `fetch` in `PgVectorRlsBackend._get_pool`; this is the place it was missed.
    """
    settings = postgres_settings("ingest", env=env)

    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


class ParseCache:
    """Parsed documents reused across the retries of one indexing cycle.

    `sync_once` retries a failed index up to three times, and the failure that
    actually occurs is at the **embed** step — the gateway is down, or the
    provider rate-limits. Re-parsing every document before each retry costs the
    whole corpus's parse work to reach the one call that failed, and it scales
    with the corpus rather than with the failure.

    Entries are keyed by identity, not by path: a file whose `mtime_ns` or size
    changed is parsed again. That makes a stale reuse impossible rather than
    unlikely.

    Memory is bounded by one corpus, and the cycle clears it once an index
    succeeds — a cache that outlived its cycle would hold a corpus-worth of text
    for the life of the process to save work nobody was going to repeat.
    """

    __slots__ = ("_entries", "parses")

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[int, int, ParsedDocument]] = {}
        #: How many real parses happened. Tests assert on this.
        self.parses = 0

    def parsed(
        self, file_path: Path, *, base_dir: Path | None = None
    ) -> ParsedDocument:
        try:
            stat = file_path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            # Cannot establish identity, so cannot safely reuse. Parse it.
            self.parses += 1
            return parse_file(file_path, base_dir=base_dir)

        cached = self._entries.get(file_path)
        if cached is not None and (cached[0], cached[1]) == key:
            return cached[2]

        self.parses += 1
        document = parse_file(file_path, base_dir=base_dir)
        self._entries[file_path] = (key[0], key[1], document)
        return document

    def clear(self) -> None:
        self._entries.clear()


def embedder_model_stamp(embedder: Embedder) -> str:
    """Name the vector space an embedder produces, for the chunk `model` stamp.

    Shared with the ingest short-circuit in `wiki_ingest`, which decides whether
    stored vectors can be reused. If that decision and this stamp were derived
    differently they could disagree, and the index would quietly end up holding
    two vector spaces -- the F-2 failure the stamp exists to make impossible.
    """
    model = getattr(embedder, "model", None)
    if isinstance(model, str) and model.strip():
        return model
    return type(embedder).__name__


async def ingest_document(
    file_path: Path,
    allowed_depts: list[str],
    conn: asyncpg.Connection | None = None,
    chunker: ContextualChunker | None = None,
    embedder: Embedder | None = None,
    base_dir: Path | None = None,
    dry_run: bool = False,
    parse_cache: ParseCache | None = None,
    document_transform: Callable[[ParsedDocument], ParsedDocument] | None = None,
    stage_observer: IngestStageObserver | None = None,
) -> dict[str, Any]:
    """Parses, chunks, embeds, and ingests a single document into PostgreSQL."""
    allowed_depts = validate_allowed_depts(allowed_depts)
    chunker = chunker or ContextualChunker()
    embedder = embedder or LiteLLMBatchEmbedder()

    # 1. Parse document. A retry after a failed embed must not re-parse a corpus
    # that has not changed — see `ParseCache`.
    if parse_cache is None:
        parsed_doc = parse_file(file_path, base_dir=base_dir)
    else:
        parsed_doc = parse_cache.parsed(file_path, base_dir=base_dir)
    if document_transform is not None:
        parsed_doc = document_transform(parsed_doc)
    chunks = chunker.chunk_document(parsed_doc)
    _observe_ingest_stage(
        stage_observer,
        "chunk_complete",
        source_uri=parsed_doc.source_uri,
        chunk_count=len(chunks),
    )

    if dry_run:
        return {
            "source_uri": parsed_doc.source_uri,
            "title": parsed_doc.title,
            "chunks_count": len(chunks),
            "allowed_depts": allowed_depts,
            "status": "dry_run_ok",
        }

    if not chunks:
        # A source that yields no text must not keep whatever it published on a
        # previous run. Leaving the old rows in place is how the fabricated
        # image descriptions of audit finding B3 survived a re-ingest, still
        # carrying their earlier (possibly public) ACL. No evidence is the
        # honest outcome; retrieval then reports `no_source` for the address.
        purged = False
        if conn is not None:
            async with conn.transaction():
                deleted = await conn.execute(
                    "DELETE FROM rag_documents WHERE source_uri = $1;",
                    parsed_doc.source_uri,
                )
            purged = deleted.rsplit(" ", 1)[-1] not in {"0", ""}
        return {
            "source_uri": parsed_doc.source_uri,
            "title": parsed_doc.title,
            "chunks_count": 0,
            "status": "purged_empty" if purged else "skipped_empty",
        }

    # 2. Batch generate dense embeddings
    texts_to_embed = [c.contextual_text for c in chunks]
    _observe_ingest_stage(
        stage_observer,
        "embed_request_sent",
        source_uri=parsed_doc.source_uri,
        chunk_count=len(chunks),
    )
    embeddings = embedder.embed_texts(texts_to_embed)
    _observe_ingest_stage(
        stage_observer,
        "embed_response_received",
        source_uri=parsed_doc.source_uri,
        chunk_count=len(embeddings),
    )
    if len(embeddings) != len(chunks):
        raise EmbeddingError("embedding batch cardinality mismatch during ingestion")
    for c, emb in zip(chunks, embeddings, strict=True):
        if len(emb) != 1024:
            raise EmbeddingError("embedding dimension mismatch during ingestion")
        c.embedding = emb
        c.metadata["model"] = embedder_model_stamp(embedder)
        c.metadata["dim"] = len(emb)

    # 3. Transactional Upsert into PostgreSQL
    close_conn = False
    if conn is None:
        conn = await get_pg_connection()
        close_conn = True

    try:
        async with conn.transaction():
            # Upsert document record
            row = await conn.fetchrow(
                """
                INSERT INTO rag_documents
                    (source_uri, allowed_depts, title, ingested_at,
                     capability_fingerprint)
                VALUES ($1, $2, $3, now(), $4)
                ON CONFLICT (source_uri) DO UPDATE
                SET allowed_depts = EXCLUDED.allowed_depts,
                    title = EXCLUDED.title,
                    ingested_at = EXCLUDED.ingested_at,
                    capability_fingerprint = EXCLUDED.capability_fingerprint
                RETURNING doc_id;
                """,
                parsed_doc.source_uri,
                allowed_depts,
                parsed_doc.title,
                json.dumps(capability_fingerprint()),
            )
            if not row:
                raise RuntimeError(f"Failed to upsert document {parsed_doc.source_uri}")

            doc_id = row["doc_id"]

            # Idempotent: delete previous chunks for this document
            await conn.execute("DELETE FROM rag_chunks WHERE doc_id = $1;", doc_id)

            # Insert all chunks
            for c in chunks:
                emb_str = (
                    f"[{','.join(str(x) for x in c.embedding)}]"
                    if c.embedding
                    else None
                )
                # PyYAML decodes ISO frontmatter dates to ``datetime.date``.
                # Preserve those values canonically instead of making every
                # wiki page with ``updated: YYYY-MM-DD`` fail at the SQL seam.
                meta_json = json.dumps({"loc": c.loc, **c.metadata}, default=str)

                await conn.execute(
                    """
                    INSERT INTO rag_chunks (
                        doc_id, chunk_index, chunk_text, context_prefix, embedding, metadata
                    ) VALUES (
                        $1, $2, $3, $4, $5::vector, $6::jsonb
                    );
                    """,
                    doc_id,
                    c.chunk_index,
                    c.chunk_text,
                    c.context_prefix,
                    emb_str,
                    meta_json,
                )

        # Leaving the transaction context is PostgreSQL's commit acknowledgement.
        # Under READ COMMITTED the newly committed rows are visible to subsequent
        # statements at this point; the live acceptance probe separately records
        # when the served wiki_search surface first returns the page.
        _observe_ingest_stage(
            stage_observer,
            "postgres_commit",
            source_uri=parsed_doc.source_uri,
            chunk_count=len(chunks),
            doc_id=str(doc_id),
        )
        _observe_ingest_stage(
            stage_observer,
            "row_visible",
            source_uri=parsed_doc.source_uri,
            chunk_count=len(chunks),
            doc_id=str(doc_id),
        )

        return {
            "source_uri": parsed_doc.source_uri,
            "title": parsed_doc.title,
            "doc_id": str(doc_id),
            "chunks_count": len(chunks),
            "status": "ingested_ok",
        }
    finally:
        if close_conn:
            await conn.close()


#: Selects the documents belonging to one corpus tier. A document qualifies
#: only when **every** chunk it owns carries the tier's `corpus` stamp, so a
#: document is never claimed by two tiers and a cross-tier purge is impossible.
#: `$1` is the stamp: `'wiki'` for the vault, SQL `NULL` for the untiered raw
#: corpus, which is written without a `corpus` key at all.
_CORPUS_TIER_SQL = """
    SELECT d.doc_id, d.source_uri
    FROM rag_documents d
    WHERE EXISTS (
            SELECT 1 FROM rag_chunks c
            WHERE c.doc_id = d.doc_id
              AND c.metadata->>'corpus' IS NOT DISTINCT FROM $1::text
          )
      AND NOT EXISTS (
            SELECT 1 FROM rag_chunks c
            WHERE c.doc_id = d.doc_id
              AND c.metadata->>'corpus' IS DISTINCT FROM $1::text
          );
"""


async def reconcile_deletions(
    dir_path: Path,
    conn: asyncpg.Connection | None = None,
    dry_run: bool = False,
    acl: DocumentAclMap | None = None,
    corpus: str | None = None,
) -> list[str]:
    """Finds and deletes database documents no longer part of the authorized corpus.

    A document qualifies when its file is gone from disk or — once an `acl` map
    governs the tree — when the file is still present but no longer matches any
    rule. Revoking a rule must actually revoke access, so a stale row from an
    earlier, broader policy is purged rather than left readable.

    `corpus` scopes the sweep to one tier and defaults to the untiered raw
    corpus. Without it this function claimed every row whose `source_uri`
    happened to start with the watched directory's name, and the vault has its
    own top-level `raw/` folder: reconciling the repository's `raw/` flagged
    **101 vault pages** for deletion because no file backed them *there*. The
    two corpora share one flat `source_uri` namespace, so directory name alone
    cannot say who owns a row; the corpus stamp can.
    """
    close_conn = False
    if conn is None:
        conn = await get_pg_connection()
        close_conn = True

    deleted_uris: list[str] = []
    try:
        rows = await conn.fetch(_CORPUS_TIER_SQL, corpus)
        dir_path_resolved = dir_path.resolve()  # noqa: ASYNC240
        base_parent = dir_path_resolved.parent
        dir_name = dir_path_resolved.name

        # Collect existing relative URIs in this directory tree
        existing_disk_uris = {
            p.relative_to(base_parent).as_posix()
            for p in dir_path_resolved.rglob("*")  # noqa: ASYNC240
            if p.is_file()
        }

        for row in rows:
            uri = row["source_uri"]
            # Target documents scoped under this directory
            if uri.startswith(f"{dir_name}/"):
                on_disk_path = base_parent / uri
                missing = uri not in existing_disk_uris and not on_disk_path.exists()
                unmapped = (
                    acl is not None
                    and not missing
                    and acl.departments_for(on_disk_path) is None
                )
                if missing or unmapped:
                    deleted_uris.append(uri)
                    if not dry_run:
                        async with conn.transaction():
                            await conn.execute(
                                "DELETE FROM rag_documents WHERE doc_id = $1;",
                                row["doc_id"],
                            )
        return deleted_uris
    finally:
        if close_conn:
            await conn.close()


def _scanned_uri(file_path: Path, base_dir: Path) -> str:
    """Name a skipped file the way `parse_file` would have named it.

    Used only for reporting; a skipped file is never written to the database, so
    this is a diagnostic label rather than a stored identity.
    """
    if file_path.is_relative_to(base_dir):
        return str(file_path.relative_to(base_dir))
    return str(file_path)


#: The fingerprint of every document in one corpus tier under one path prefix.
#: The tier predicate is not optional: the two corpora share one flat
#: `source_uri` namespace and the vault has its own top-level `raw/` folder, so
#: a prefix match alone answers this question with 101 rows belonging to the
#: other corpus.
_TIER_FINGERPRINT_SQL = """
    SELECT d.source_uri, d.capability_fingerprint
    FROM rag_documents d
    WHERE d.capability_fingerprint IS NOT NULL
      AND d.source_uri LIKE $1
      AND EXISTS (
            SELECT 1 FROM rag_chunks c
            WHERE c.doc_id = d.doc_id
              AND c.metadata->>'corpus' IS NOT DISTINCT FROM $2::text
          )
      AND NOT EXISTS (
            SELECT 1 FROM rag_chunks c
            WHERE c.doc_id = d.doc_id
              AND c.metadata->>'corpus' IS DISTINCT FROM $2::text
          );
"""


async def corpus_fingerprint_mismatch(
    conn: asyncpg.Connection,
    dir_path: Path,
    base_dir: Path,
    corpus: str | None = None,
) -> str:
    """Describe how this process differs from what the corpus was built with.

    Returns `""` when they agree **or when nothing recorded one** — a corpus
    indexed before fingerprints existed must not be un-ingestable, or an upgrade
    bricks a working deployment.

    `corpus` scopes the question to one tier, and defaults to the untiered raw
    corpus. Without it, checking `raw/` reported a difference against a vault
    page: the vault's own `raw/articles/` pages carry the same prefix, were
    ingested by a different process, and are not this pipeline's to reproduce.
    """
    from scout.capabilities import (
        capability_fingerprint,
        describe_fingerprint_difference,
    )

    prefix = _scanned_uri(dir_path, base_dir)
    rows = await conn.fetch(_TIER_FINGERPRINT_SQL, f"{prefix}%", corpus)
    if not rows:
        return ""

    current = capability_fingerprint()
    for row in rows:
        recorded = row["capability_fingerprint"]
        if isinstance(recorded, str):
            recorded = json.loads(recorded)
        difference = describe_fingerprint_difference(recorded or {}, current)
        if difference:
            return f"{row['source_uri']}: {difference}"
    return ""


def _normalise_acknowledgement(text: str) -> str:
    """Compare on content, not on how a shell mangled the quoting."""
    return " ".join(text.split()).strip().strip("'\"")


def _require_acknowledgement(mismatch: str, acknowledgement: str | None) -> None:
    """The override must name the difference it is overriding.

    `--confirm` is already required for every write, so requiring it a second
    time acknowledges nothing: it is a keystroke the caller types without
    reading. An acknowledgement that must equal the mismatch text can only be
    produced by having read that specific mismatch — and a *stale* one, copied
    from an earlier refusal about a different difference, is rejected, which a
    boolean flag can never do.
    """
    if acknowledgement is None or not acknowledgement.strip():
        raise CapabilityMismatchError(
            "allow_capability_change was given without an acknowledgement. "
            "Rebuilding the corpus with different parser capabilities is not a "
            "flag; name the difference being accepted:\n"
            f"  --acknowledge-capability-change={mismatch!r}"
        )
    if _normalise_acknowledgement(acknowledgement) != _normalise_acknowledgement(
        mismatch
    ):
        raise CapabilityMismatchError(
            "the acknowledgement does not match the difference this process "
            "found, so it cannot be an acknowledgement of it — a stale one "
            "copied from an earlier refusal is the case this rejects.\n"
            f"  found        {mismatch!r}\n"
            f"  acknowledged {acknowledgement!r}"
        )


async def record_capability_override(
    conn: asyncpg.Connection,
    *,
    mismatch: str,
    acknowledgement: str,
    actor_hint: str | None,
    dir_path: Path,
    base_dir: Path,
) -> None:
    """Append one audit row before the corpus is rebuilt.

    Written *before* the rebuild on purpose: an audit row that only appears
    after a successful ingest is missing for exactly the runs that went wrong.

    `actor` is not passed — PostgreSQL fills it from `session_user`, the
    authenticated identity. `actor_hint` carries caller-supplied context and is
    recorded as unverified. See `005_ingest_events.sql`.
    """
    from scout.capabilities import capability_fingerprint

    source_uri = mismatch.split(":", 1)[0].strip() or _scanned_uri(dir_path, base_dir)
    recorded = await conn.fetchval(
        "SELECT capability_fingerprint FROM rag_documents WHERE source_uri = $1;",
        source_uri,
    )
    if isinstance(recorded, str):
        recorded = json.loads(recorded)

    await conn.execute(
        """
        INSERT INTO ingest_events (
            source_uri, event_type, old_fingerprint, new_fingerprint,
            acknowledgement, actor_hint
        ) VALUES ($1, 'capability_override', $2::jsonb, $3::jsonb, $4, $5);
        """,
        source_uri,
        json.dumps(recorded) if recorded else None,
        json.dumps(capability_fingerprint()),
        acknowledgement,
        actor_hint,
    )


async def ingest_directory(
    dir_path: Path,
    allowed_depts: list[str] | None = None,
    dry_run: bool = False,
    reconcile: bool = True,
    embedder: Embedder | None = None,
    acl: DocumentAclMap | None = None,
    parse_cache: ParseCache | None = None,
    allow_capability_change: bool = False,
    acknowledge_capability_change: str | None = None,
    actor_hint: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Recursively ingests a directory under one explicit document ACL source.

    Exactly one authority must be named: `allowed_depts` applies a single ACL to
    every file in the tree, while `acl` resolves each file separately and skips
    the ones no rule matches. Naming neither is an error — there is no implicit
    department, so nothing can become publicly readable by omission.

    Raises:
        AclPolicyError: Neither or both ACL sources were supplied.
    """
    if (allowed_depts is None) == (acl is None):
        raise AclPolicyError(
            "ingest_directory needs exactly one document ACL source: "
            "an explicit allowed_depts list or an acl map"
        )
    fixed_depts = (
        validate_allowed_depts(allowed_depts) if allowed_depts is not None else None
    )
    supported_exts = {
        ".md",
        ".txt",
        ".markdown",
        ".pdf",
        ".csv",
        ".tsv",
        ".py",
        ".sh",
        ".json",
        ".yaml",
        ".yml",
        ".sql",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
    }

    files = sorted(
        p
        for p in dir_path.rglob("*")  # noqa: ASYNC240
        if p.is_file()
        and p.suffix.lower() in supported_exts
        and not _is_control_metadata(p, dir_path)
    )

    results: list[dict[str, Any]] = []
    chunker = ContextualChunker()
    if embedder is None:
        # Same reason as the connection above: the caller may have resolved
        # configuration already, and reading `os.environ` here would discard it.
        # A `.env` the CLI loaded is not visible to a bare `os.environ` lookup.
        settings = dict(env or {})
        embedder = LiteLLMBatchEmbedder(
            base_url=settings.get("LITELLM_BASE_URL"),
            api_key=settings.get("LITELLM_MASTER_KEY"),
            # Deliberately not `LITELLM_EMBED_MODEL`. That variable configures
            # the *gateway* -- `config/litellm/config.yaml` resolves the
            # `snp-embed` route through it -- and sending its value as the
            # client's model name bypasses the route, and with it the
            # `dimensions: 1024` the route pins. It also stamps chunks with a
            # second name for one vector space, which is exactly what the
            # startup guard reads to decide whether the index is coherent.
        )

    conn = None if dry_run else await get_pg_connection(env)
    try:
        if conn is not None:
            # Computed on both paths. The override has to be checked *against
            # this specific difference*, so it cannot be evaluated before the
            # difference is known.
            mismatch = await corpus_fingerprint_mismatch(
                # This pipeline stamps no corpus, so the rows it must be able
                # to reproduce are the untiered ones.
                conn,
                dir_path,
                dir_path.parent,
                corpus=None,
            )
            if mismatch and not allow_capability_change:
                # Strict refusal, not a compatibility matrix. Silent churn is
                # worse than a blocked command: this process would emit evidence
                # the runtime cannot reproduce, and the next sync would drop it
                # without saying so. Nothing is written and nothing is deleted —
                # reconciliation in particular must not run, or rows are purged
                # for a corpus this process has just said it cannot rebuild.
                raise CapabilityMismatchError(
                    "this environment parses differently from the one that built "
                    f"the corpus ({mismatch}). Re-ingest through the image that "
                    "built it, or acknowledge this exact difference to rebuild "
                    "the corpus with this environment's capabilities:\n"
                    f"  --acknowledge-capability-change={mismatch!r}"
                )
            if mismatch and allow_capability_change:
                _require_acknowledgement(mismatch, acknowledge_capability_change)
                await record_capability_override(
                    conn,
                    mismatch=mismatch,
                    acknowledgement=acknowledge_capability_change or "",
                    actor_hint=actor_hint,
                    dir_path=dir_path,
                    base_dir=dir_path.parent,
                )

        async def ingest_batch() -> None:
            for file_path in files:
                file_depts = (
                    fixed_depts if acl is None else acl.departments_for(file_path)
                )
                if file_depts is None:
                    # Fail closed: no rule grants this file, so it is not
                    # published at all. Recorded, never silently skipped.
                    results.append(
                        {
                            "source_uri": _scanned_uri(file_path, dir_path.parent),
                            "chunks_count": 0,
                            "status": "skipped_unmapped_acl",
                        }
                    )
                    continue
                res = await ingest_document(
                    file_path=file_path,
                    allowed_depts=file_depts,
                    conn=conn,
                    chunker=chunker,
                    embedder=embedder,
                    base_dir=dir_path.parent,
                    dry_run=dry_run,
                    parse_cache=parse_cache,
                )
                results.append(res)

            if reconcile and conn is not None:
                deleted = await reconcile_deletions(
                    dir_path=dir_path,
                    conn=conn,
                    dry_run=dry_run,
                    acl=acl,
                    # This pipeline never stamps a corpus, so the rows it owns
                    # are the untiered ones. Naming the tier explicitly keeps a
                    # raw sweep off vault rows that share the `raw/` prefix.
                    corpus=None,
                )
                for d in deleted:
                    results.append({"source_uri": d, "status": "purged_deleted"})

        if conn is None:
            await ingest_batch()
        else:
            # A watched directory is one publication batch. Nested per-document
            # transactions become savepoints; any later parse/embed/insert or
            # reconciliation failure rolls the entire batch back to last-good.
            async with conn.transaction():
                await ingest_batch()
    finally:
        if conn is not None:
            await conn.close()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SNP V2 RAG Ingestion Pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--path", type=str, help="Path to a single file to ingest")
    group.add_argument("--dir", type=str, help="Directory to recursively ingest")

    # No default: the caller must name an ACL authority. Ingestion never
    # invents a department, so nothing becomes publicly readable by omission.
    acl_source = parser.add_mutually_exclusive_group(required=True)
    acl_source.add_argument(
        "--allowed-depts",
        "--dept",
        dest="allowed_depts",
        type=str,
        help=(
            "Comma-separated document ACL applied to every ingested file "
            "(e.g. redteam,blueteam or all)"
        ),
    )
    acl_source.add_argument(
        "--acl-file",
        dest="acl_file",
        type=str,
        help=(
            f"Path to a document ACL map (e.g. raw/{DEFAULT_ACL_FILENAME}) that "
            "resolves each file's departments; an unmatched file is not ingested"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk without writing to PostgreSQL",
    )

    args = parser.parse_args()
    depts: list[str] | None = None
    acl: DocumentAclMap | None = None
    if args.allowed_depts is not None:
        try:
            depts = validate_allowed_depts(
                [d.strip() for d in args.allowed_depts.split(",") if d.strip()]
            )
        except ValueError as exc:
            parser.error(str(exc))
    else:
        try:
            acl = DocumentAclMap.from_file(Path(args.acl_file))
        except AclPolicyError as exc:
            parser.error(str(exc))

    if args.path:
        target = Path(args.path)
        if not target.is_file():
            print(f"Error: File not found: {target}", file=sys.stderr)
            sys.exit(1)
        file_depts = depts if acl is None else acl.departments_for(target)
        if file_depts is None:
            print(
                f"Error: no rule in {args.acl_file} grants {target}; refusing to "
                "ingest an unmapped file.",
                file=sys.stderr,
            )
            sys.exit(1)
        res = asyncio.run(
            ingest_document(
                file_path=target,
                allowed_depts=file_depts,
                base_dir=Path.cwd(),
                dry_run=args.dry_run,
            )
        )
        print(json.dumps(res, indent=2))
    elif args.dir:
        target_dir = Path(args.dir)
        if not target_dir.is_dir():
            print(f"Error: Directory not found: {target_dir}", file=sys.stderr)
            sys.exit(1)
        results = asyncio.run(
            ingest_directory(
                dir_path=target_dir,
                allowed_depts=depts,
                acl=acl,
                dry_run=args.dry_run,
            )
        )
        for r in results:
            print(
                f"[{r['status']}] {r['source_uri']} ({r.get('chunks_count', 0)} chunks)"
            )
        print(f"\nCompleted ingestion for {len(results)} files.")


if __name__ == "__main__":
    main()
