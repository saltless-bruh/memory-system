"""V3 wiki-corpus preparation and ingestion.

The vault remains the source of truth.  This module adapts its heterogeneous
Markdown pages to the existing parser, contextual chunker, and transactional
``ingest_document`` path without requiring a frontmatter migration.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

import asyncpg

from scout import vault
from scout.capabilities import (
    capability_fingerprint,
    describe_fingerprint_difference,
)
from scout.chunker import ContextualChunker, Embedder, LiteLLMBatchEmbedder
from scout.faults import is_file_fault, skipped_file
from scout.ingest import (
    IngestStageObserver,
    embedder_model_stamp,
    get_pg_connection,
    ingest_document,
)
from scout.parsers import ParsedDocument, ParsedSection

WIKI_ALLOWED_DEPARTMENTS = ("redteam", "blueteam", "ai_eng", "infra")
#: The corpus tier stamped on every chunk this module writes. It is the only
#: thing that distinguishes a vault row from a raw-corpus row: both live in one
#: flat `source_uri` namespace and the vault has its own top-level `raw/`
#: folder, so the path prefix cannot say who owns a row.
WIKI_CORPUS = "wiki"
#: Vault files that are authored for people, not for retrieval. `index.md` is
#: the hand-written catalogue this module reads descriptions from, and `log.md`
#: is the vault's changelog: chunking either puts a table of contents and a
#: diary into the corpus, competing with the pages that actually answer a
#: question. `load_pages` already drops `index.md`; `ingest_wiki` drops
#: `log.md`. Reconciliation removes both, because a file that is present on
#: disk is never reached by a missing-file sweep.
WIKI_CONTROL_DOCUMENTS = ("index.md", "log.md")
WIKI_TARGET_CHUNK_TOKENS = 350
WIKI_MAX_CHUNK_CHARS = 1400
#: Stamped on every chunk so the ingest short-circuit can tell whether stored
#: chunks were cut under the rules this process would use. Same bytes cut at
#: different boundaries retrieve differently, and nothing else records it:
#: `capability_fingerprint` covers the parser, not the chunker.
#: Bumped whenever the rules for preparing a section change -- which headings
#: count as link sections, what is stripped, how the TL;DR is resolved. The
#: page's bytes do not move when those rules do, so without this in the stamp
#: the ingest short-circuit would skip exactly the pages a fix was written for.
#: 2: fold diacritics in `_heading_key`, so `## Liên quan` is recognised as the
#:    link section `_LINK_SECTION_NAMES` always claimed to cover.
WIKI_PREPARE_REVISION = 2
WIKI_CHUNK_POLICY = (
    f"chars={WIKI_MAX_CHUNK_CHARS};tokens={WIKI_TARGET_CHUNK_TOKENS}"
    f";rev={WIKI_PREPARE_REVISION}"
)

_HEADING_LINE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_MARKDOWN_INDEX_ENTRY = re.compile(
    r"^\s*[-*+]\s+\[([^\]]+)\]\(([^)]+)\)\s*(?:—|–|-)\s*(\S.*)$"
)
_WIKILINK_INDEX_ENTRY = re.compile(
    r"^\s*[-*+]\s+\[\[([^\]]+)\]\]\s*(?:—|–|-)\s*(\S.*)$"
)
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENTENCE = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.DOTALL)

_LINK_SECTION_NAMES = {
    "crossreferences",
    "related",
    "seealso",
    "links",
    "lienquan",
}
_TYPE_BY_DIRECTORY = {
    "concepts": "concept",
    "entities": "entity",
    "comparisons": "comparison",
    "queries": "query",
    "summaries": "summary",
    "schemas": "schema",
    "raw": "raw",
}


class WikiIngestError(ValueError):
    """A wiki page cannot produce honest body-backed retrieval chunks."""


class VaultRootError(RuntimeError):
    """The configured vault is not there: absent, dangling, or not a directory.

    Distinct from an empty vault and from a bad page. `host-sync` publishes
    through `current -> snapshots/<commit>`, and a lenient `Path.resolve()`
    turns a dangling `current` into a path that simply does not exist, which
    `load_pages` answers with `[]`. Read that way, a missing vault became an
    empty one: the cycle reported success, reconciliation found no file for any
    row, and every wiki-tier document was deleted -- recoverable only by
    re-embedding the whole vault. Waiting does not produce a vault, so the
    watcher treats this as permanent.
    """


class ReconcileRefusedError(RuntimeError):
    """A deletion sweep that would empty the vault tier was not performed.

    Kept separate from `VaultRootError` on purpose: this guard sits at the
    deletion site and does not trust that the root check ran, or that a root
    which exists holds what it should. A publication with no page in it while
    the index holds pages is far likelier to be a broken publication than an
    intended deletion of the entire vault, and the cost of being wrong is not
    symmetric -- a refused sweep leaves stale rows a human can purge, a
    performed one throws away every embedding.
    """


def _vault_root(wiki_dir: Path) -> Path:
    """Resolve `wiki_dir` strictly to the directory that must hold the vault."""
    try:
        root = wiki_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        # RuntimeError is how `resolve` reported a symlink loop before 3.13.
        raise VaultRootError(f"vault root cannot be resolved: {wiki_dir}") from exc
    if not root.is_dir():
        raise VaultRootError(f"vault root is not a directory: {wiki_dir}")
    return root


def _as_date(value: object) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def _path_keys(value: str) -> tuple[str, ...]:
    """Return stable catalogue lookup keys for a vault-relative link target."""
    cleaned = unquote(value.strip().strip("<>")).replace("\\", "/")
    cleaned = cleaned.split("#", 1)[0].split("?", 1)[0].removeprefix("./")
    if cleaned.lower().endswith(".md"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip("/").casefold()
    if not cleaned:
        return ()
    stem = PurePosixPath(cleaned).name
    return (cleaned,) if stem == cleaned else (cleaned, stem)


@dataclass(frozen=True, slots=True)
class IndexCatalog:
    """Descriptions harvested from the vault's authored ``index.md``."""

    updated: dt.date | None
    descriptions: Mapping[str, str]

    @classmethod
    def empty(cls) -> IndexCatalog:
        return cls(updated=None, descriptions={})

    def description_for(self, source_uri: str, title: str) -> str | None:
        candidates = [*_path_keys(source_uri), *_path_keys(title)]
        for candidate in candidates:
            description = self.descriptions.get(candidate)
            if description:
                return description
        return None


def load_index_catalog(index_path: Path) -> IndexCatalog:
    """Parse authored Markdown-link and wikilink catalogue entries."""
    if not index_path.is_file():
        return IndexCatalog.empty()

    page = vault.parse_page(index_path)
    descriptions: dict[str, str] = {}
    for raw_line in page.body.splitlines():
        markdown_match = _MARKDOWN_INDEX_ENTRY.match(raw_line)
        wikilink_match = _WIKILINK_INDEX_ENTRY.match(raw_line)
        if markdown_match is not None:
            _title, target, description = markdown_match.groups()
        elif wikilink_match is not None:
            target_with_alias, description = wikilink_match.groups()
            target = target_with_alias.split("|", 1)[0]
        else:
            continue
        description = description.strip()
        if not description:
            continue
        for key in _path_keys(target):
            descriptions.setdefault(key, description)

    return IndexCatalog(
        updated=_as_date(page.frontmatter.get("updated")),
        descriptions=descriptions,
    )


def _heading(section: ParsedSection) -> tuple[int, str] | None:
    first_line = section.text.splitlines()[0] if section.text.splitlines() else ""
    match = _HEADING_LINE.match(first_line)
    if match is None:
        return None
    return len(match.group(1)), match.group(2).strip()


def _heading_key(value: str) -> str:
    """Normalise a heading to the key its section role is looked up by.

    Diacritics are folded, so `## Liên quan` and `## Lien quan` reach the same
    entry. Without that, `_LINK_SECTION_NAMES` contained `lienquan` and matched
    nothing at all: `\w` under `re.UNICODE` keeps `ê`, so the real heading
    normalised to `liênquan` and missed by one character. Nobody puts
    `lienquan` in a set of link-section names by accident, so the normaliser is
    made to produce what the entry describes rather than a second spelling
    added beside it.

    Folding is decomposition-based rather than a substitution table, so it
    covers every accented form the vault uses instead of the handful someone
    remembered.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", "", without_marks, flags=re.UNICODE).casefold()


def _section_body(section: ParsedSection) -> str:
    lines = section.text.splitlines()
    if lines and _HEADING_LINE.match(lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _wikilink_target(raw: str) -> str:
    return raw.split("|", 1)[0].split("#", 1)[0].strip()


def _is_wikilink_only_line(line: str) -> bool:
    candidate = line.strip()
    candidate = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", candidate)
    if _WIKILINK.search(candidate) is None:
        return False
    remainder = _WIKILINK.sub("", candidate)
    return re.sub(r"[\s,;·|/]+", "", remainder) == ""


def _without_pure_wikilink_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not _is_wikilink_only_line(line)
    ).strip()


def _plain_body_text(doc: ParsedDocument) -> str:
    lines: list[str] = []
    for section in doc.sections:
        for line in section.text.splitlines():
            if _HEADING_LINE.match(line) or _is_wikilink_only_line(line):
                continue
            lines.append(line)
    return "\n".join(lines).strip()


def _explicit_tldr(doc: ParsedDocument) -> str | None:
    for section in doc.sections:
        heading = _heading(section)
        if heading is None or _heading_key(heading[1]) != "tldr":
            continue
        body = _section_body(section)
        if body:
            return body
    return None


def _lead_paragraph(doc: ParsedDocument) -> str | None:
    preface: list[str] = []
    for section in doc.sections:
        heading = _heading(section)
        if heading is not None and heading[0] >= 2:
            break
        preface.append(_section_body(section))
    text = "\n\n".join(part for part in preface if part).strip()
    for paragraph in re.split(r"\n\s*\n", text):
        candidate = " ".join(paragraph.split()).strip()
        if candidate:
            return candidate
    return None


def _first_two_sentences(doc: ParsedDocument) -> str | None:
    text = " ".join(_plain_body_text(doc).split())
    if not text:
        return None
    sentences = [match.group(0).strip() for match in _SENTENCE.finditer(text)]
    selected = [sentence for sentence in sentences if sentence][:2]
    return " ".join(selected) if selected else None


def _resolve_tldr(
    doc: ParsedDocument, catalog: IndexCatalog, description: str | None
) -> tuple[str, str]:
    explicit = _explicit_tldr(doc)
    if explicit:
        return explicit, "section"

    lead = _lead_paragraph(doc)
    if lead:
        return lead, "lead"

    page_updated = _as_date(doc.metadata.get("updated"))
    index_is_fresh = (
        catalog.updated is not None
        and page_updated is not None
        and catalog.updated >= page_updated
    )
    if description and index_is_fresh:
        return description, "index"

    inferred = _first_two_sentences(doc)
    if inferred:
        return inferred, "inferred"
    raise WikiIngestError(
        f"{doc.source_uri}: page has no body text from which to resolve a TLDR"
    )


def _page_type(doc: ParsedDocument) -> str:
    declared = doc.metadata.get("type")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    directory = PurePosixPath(doc.source_uri.replace("\\", "/")).parent.name
    return _TYPE_BY_DIRECTORY.get(directory.casefold(), "unknown")


def _token_count(text: str) -> int:
    return len(_TOKEN.findall(text))


def _outline(doc: ParsedDocument) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for section in doc.sections:
        heading = _heading(section)
        if heading is None or heading[0] == 1:
            continue
        result.append(
            {"heading": heading[1], "tokens": _token_count(_section_body(section))}
        )
    return result


def _wikilinks(doc: ParsedDocument) -> list[str]:
    found: list[str] = []
    for match in _WIKILINK.finditer(doc.full_text):
        target = _wikilink_target(match.group(1))
        if target and target not in found:
            found.append(target)
    return found


def _content_hash(doc: ParsedDocument) -> str:
    payload = json.dumps(
        doc.metadata,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(f"{payload}\0{doc.full_text}".encode()).hexdigest()


def _prepared_body_sections(doc: ParsedDocument) -> list[ParsedSection]:
    result: list[ParsedSection] = []
    for section in doc.sections:
        heading = _heading(section)
        heading_name = heading[1] if heading is not None else "Intro"
        heading_key = _heading_key(heading_name)
        if heading_key == "tldr":
            continue

        role = "provenance" if heading_key == "provenance" else "section"
        text = section.text.strip()
        if heading_key in _LINK_SECTION_NAMES:
            text = _without_pure_wikilink_lines(text)
        if not _section_body(
            ParsedSection(loc=section.loc, text=text, metadata=section.metadata)
        ):
            continue

        metadata = dict(section.metadata)
        metadata["heading"] = heading_name
        metadata["role"] = role
        result.append(ParsedSection(loc=section.loc, text=text, metadata=metadata))
    return result


def _pack_sections(sections: list[ParsedSection]) -> list[ParsedSection]:
    """Pack adjacent ordinary sections up to the V3 target token count."""
    packed: list[ParsedSection] = []
    pending: list[ParsedSection] = []
    pending_tokens = 0

    def flush() -> None:
        nonlocal pending_tokens
        if not pending:
            return
        headings = [str(item.metadata.get("heading", "Section")) for item in pending]
        metadata = dict(pending[0].metadata)
        metadata["heading"] = " > ".join(headings)
        metadata["heading_path"] = headings
        packed.append(
            ParsedSection(
                loc="; ".join(item.loc for item in pending),
                text="\n\n".join(item.text for item in pending),
                metadata=metadata,
            )
        )
        pending.clear()
        pending_tokens = 0

    for section in sections:
        role = section.metadata.get("role")
        tokens = _token_count(section.text)
        if role != "section" or tokens > WIKI_TARGET_CHUNK_TOKENS:
            flush()
            packed.append(section)
            continue
        if pending and pending_tokens + tokens > WIKI_TARGET_CHUNK_TOKENS:
            flush()
        pending.append(section)
        pending_tokens += tokens
    flush()
    return packed


def prepare_wiki_document(
    doc: ParsedDocument,
    catalog: IndexCatalog,
    *,
    content_hash: str | None = None,
) -> ParsedDocument:
    """Normalize one parsed page into body-backed, metadata-rich sections."""
    if not _plain_body_text(doc):
        raise WikiIngestError(
            f"{doc.source_uri}: page contains no retrievable body text"
        )

    description = catalog.description_for(doc.source_uri, doc.title)
    tldr, tldr_source = _resolve_tldr(doc, catalog, description)
    metadata = dict(doc.metadata)
    metadata.update(
        {
            "chunk_policy": WIKI_CHUNK_POLICY,
            "content_hash": content_hash or _content_hash(doc),
            "corpus": WIKI_CORPUS,
            "outline": _outline(doc),
            "tldr": tldr,
            "tldr_source": tldr_source,
            "title": doc.title,
            "type": _page_type(doc),
            "wikilinks": _wikilinks(doc),
        }
    )
    if description:
        metadata["summary"] = description

    tldr_section = ParsedSection(
        loc="Section TL;DR",
        text=f"## TL;DR\n\n{tldr}",
        metadata={"heading": "TL;DR", "role": "tldr"},
    )
    body_sections = _pack_sections(_prepared_body_sections(doc))
    return ParsedDocument(
        source_uri=doc.source_uri,
        title=doc.title,
        sections=[tldr_section, *body_sections],
        metadata=metadata,
    )


#: What a previous ingest left behind, per document, for the corpus tier this
#: module owns. Aggregated rather than per-chunk so that a document whose chunks
#: disagree -- a half-written upsert -- is visibly inconsistent and is rebuilt
#: instead of trusted.
_INDEXED_SIGNATURE_SQL = """
    SELECT d.source_uri,
           d.capability_fingerprint,
           count(*)                                        AS chunks,
           count(DISTINCT c.metadata->>'content_hash')      AS hashes,
           max(c.metadata->>'content_hash')                 AS content_hash,
           count(DISTINCT c.metadata->>'model')             AS models,
           max(c.metadata->>'model')                        AS model,
           count(DISTINCT c.metadata->>'chunk_policy')      AS policies,
           max(c.metadata->>'chunk_policy')                 AS chunk_policy
    FROM rag_documents d
    JOIN rag_chunks c ON c.doc_id = d.doc_id
    WHERE c.metadata->>'corpus' = $1
    GROUP BY d.source_uri, d.capability_fingerprint;
"""


@dataclass(frozen=True, slots=True)
class _IndexedSignature:
    """What one document's stored chunks agree on, or fail to agree on.

    The counts exist so that a document whose chunks disagree -- a half-written
    upsert -- is visibly inconsistent rather than represented by whichever value
    the aggregate happened to pick.
    """

    chunks: int
    hashes: int
    content_hash: str | None
    models: int
    model: str | None
    policies: int
    chunk_policy: str | None
    capability_fingerprint: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> _IndexedSignature:
        def text(key: str) -> str | None:
            value = row[key]
            return value if isinstance(value, str) else None

        def count(key: str) -> int:
            value = row[key]
            return value if isinstance(value, int) else 0

        return cls(
            chunks=count("chunks"),
            hashes=count("hashes"),
            content_hash=text("content_hash"),
            models=count("models"),
            model=text("model"),
            policies=count("policies"),
            chunk_policy=text("chunk_policy"),
            capability_fingerprint=text("capability_fingerprint"),
        )

    def matches(
        self, *, content_hash: str, model: str, fingerprint: Mapping[str, Any]
    ) -> bool:
        """True when stored chunks were built from these bytes, this way.

        Every input that changes what would be written has to be compared, or
        the short-circuit silently serves stale vectors:

        * `content_hash` -- the page's own bytes.
        * `model` -- reusing vectors from another model puts two spaces in one
          index, which is F-2, the failure that justified deleting
          basic-memory.
        * `chunk_policy` -- the same bytes cut at different boundaries retrieve
          differently, and nothing else records the chunker's settings.
        * `capability_fingerprint` -- a parser revision or a missing extractor
          changes the sections the chunks are cut from.
        """
        if self.chunks == 0:
            return False
        if (self.hashes, self.models, self.policies) != (1, 1, 1):
            return False
        if self.content_hash != content_hash:
            return False
        if self.model != model:
            return False
        if self.chunk_policy != WIKI_CHUNK_POLICY:
            return False
        if self.capability_fingerprint is None:
            return False
        recorded = json.loads(self.capability_fingerprint)
        if not isinstance(recorded, dict):
            return False
        return not describe_fingerprint_difference(recorded, dict(fingerprint))


#: Withdraw one vault document. The tier predicate is not decoration: the two
#: corpora share one flat `source_uri` namespace, and the vault has its own
#: top-level `raw/` folder, so an address alone cannot say whose row it is.
_PURGE_WIKI_DOCUMENT_SQL = """
    DELETE FROM rag_documents d
    WHERE d.source_uri = $1
      AND NOT EXISTS (
            SELECT 1 FROM rag_chunks c
            WHERE c.doc_id = d.doc_id
              AND c.metadata->>'corpus' IS DISTINCT FROM $2::text
          );
"""


async def _purge_wiki_document(conn: asyncpg.Connection, source_uri: str) -> bool:
    """Delete a vault page's rows; report whether there were any to delete."""
    async with conn.transaction():
        status = await conn.execute(_PURGE_WIKI_DOCUMENT_SQL, source_uri, WIKI_CORPUS)
    return str(status).rsplit(" ", 1)[-1] not in {"0", ""}


async def _indexed_signatures(
    conn: asyncpg.Connection,
) -> dict[str, _IndexedSignature]:
    """Read back what the index already holds for each vault document."""
    rows = await conn.fetch(_INDEXED_SIGNATURE_SQL, WIKI_CORPUS)
    return {str(row["source_uri"]): _IndexedSignature.from_row(row) for row in rows}


async def ingest_wiki(
    wiki_dir: Path,
    *,
    conn: asyncpg.Connection | None = None,
    embedder: Embedder | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    stage_observer: IngestStageObserver | None = None,
) -> list[dict[str, object]]:
    """Ingest every vault page through the existing idempotent document upsert.

    Excludes control documents (index.md, log.md) from chunk ingestion, though they
    remain readable through wiki_read for direct queries.
    """
    # Resolve wiki_dir to absolute path to match vault.load_pages behavior.
    # `root` is then the only vault path used from here on. It has to be: the
    # replica publishes through `current -> snapshots/<commit>`, so the
    # configured `wiki_dir` is a path *through* a symlink while `load_pages`
    # returns resolved page paths. `Path.is_relative_to` is lexical, so an
    # unresolved base is never a prefix of them and `parse_file` falls back to
    # storing each page's absolute snapshot path as its `source_uri` -- an
    # identity that changes on every push.
    #
    # Strictly, though: a lenient resolve of a dangling `current` is a path
    # that does not exist, and that must never read as an empty vault.
    root = _vault_root(wiki_dir)
    unreadable: list[vault.SkippedFile] = []
    pages = vault.load_pages(root, skipped=unreadable)
    # Exclude control documents from chunk ingestion (index.md is already excluded
    # by vault.load_pages, but log.md is authored and must stay readable for wiki_read).
    # Match against the root-level control document only, not any file with that name.
    # Use resolved path to match vault.load_pages which returns absolute paths.
    control_documents = {root / name for name in WIKI_CONTROL_DOCUMENTS}
    pages = [p for p in pages if p.path not in control_documents]
    catalog = load_index_catalog(root / "index.md")
    settings = os.environ if env is None else env
    selected_embedder = embedder or LiteLLMBatchEmbedder(
        base_url=settings.get("LITELLM_BASE_URL"),
        api_key=settings.get("LITELLM_MASTER_KEY"),
        # See `scout.ingest`: the route, not the provider model behind it.
    )
    chunker = ContextualChunker(
        max_chunk_chars=WIKI_MAX_CHUNK_CHARS,
        overlap_chars=0,
    )

    owns_connection = conn is None and not dry_run
    active_connection = conn
    if owns_connection:
        active_connection = await get_pg_connection(env)

    # A file the walk could not turn into a page is reported in the same list
    # as every page it could, so the caller reads one account of the vault.
    results: list[dict[str, object]] = [
        skipped_file(
            item.path.relative_to(root).as_posix(),
            error=item.error,
            reason=item.reason,
        )
        for item in unreadable
    ]
    try:
        # What the index already holds. A dry run has no connection to ask, so
        # it reports what a real run would consider rather than what it would
        # skip.
        signatures: dict[str, _IndexedSignature] = {}
        if active_connection is not None:
            signatures = await _indexed_signatures(active_connection)
        model_stamp = embedder_model_stamp(selected_embedder)
        fingerprint = capability_fingerprint()

        for page in pages:
            source_hash = hashlib.sha256(page.path.read_bytes()).hexdigest()
            source_uri = page.path.relative_to(root).as_posix()
            signature = signatures.get(source_uri)
            if signature is not None and signature.matches(
                content_hash=source_hash,
                model=model_stamp,
                fingerprint=fingerprint,
            ):
                # Nothing about this page or this pipeline has changed, so the
                # stored chunks are exactly what a rebuild would produce. The
                # watcher fires on every publication and re-embedding is the
                # expensive half of a cycle.
                results.append(
                    {
                        "source_uri": source_uri,
                        "title": page.title,
                        "chunks_count": signature.chunks,
                        "status": "unchanged",
                    }
                )
                continue

            def prepare(
                document: ParsedDocument, digest: str = source_hash
            ) -> ParsedDocument:
                return prepare_wiki_document(
                    document,
                    catalog,
                    content_hash=digest,
                )

            try:
                result = await ingest_document(
                    file_path=page.path,
                    allowed_depts=list(WIKI_ALLOWED_DEPARTMENTS),
                    conn=active_connection,
                    chunker=chunker,
                    embedder=selected_embedder,
                    base_dir=root,
                    dry_run=dry_run,
                    document_transform=prepare,
                    stage_observer=stage_observer,
                )
            except WikiIngestError as exc:
                # A single unwritable page must not abort the corpus. The
                # reference vault carries at least one zero-byte stub, and
                # Obsidian creates one on every "new note". Report it and
                # continue.
                #
                # But a page that *had* a body and was edited down to a stub
                # must not keep what it used to say. The refusal is raised
                # inside `ingest_document` before its own purge branch, so the
                # purge is repeated here: without it the old chunks stayed
                # searchable for as long as the stub stayed on disk, and every
                # cycle re-attempted and re-skipped the page. No evidence is
                # the honest outcome, as it is for a raw source with no text.
                # A dry run may still hold a connection -- the ingest policy
                # gate passes one to read the manifest -- and it deletes
                # nothing: it reports what a real run would skip.
                purged = False
                if (
                    signature is not None
                    and active_connection is not None
                    and not dry_run
                ):
                    purged = await _purge_wiki_document(active_connection, source_uri)
                results.append(
                    {
                        "source_uri": source_uri,
                        "title": page.title,
                        "chunks_count": 0,
                        "status": "purged_empty" if purged else "skipped_no_body",
                        "reason": str(exc),
                    }
                )
                continue
            except Exception as exc:
                # The same rule for every other fault the page itself causes:
                # it costs this page, whose previous rows stay served because
                # the file is still on disk and reconciliation keeps them. A
                # fault that is not the page's -- a gateway or database that is
                # down -- is re-raised, because every later page would fail the
                # same way and the cycle has to say so.
                if not is_file_fault(exc):
                    raise
                results.append(
                    skipped_file(
                        source_uri,
                        error=type(exc).__name__,
                        reason=str(exc),
                        title=page.title,
                    )
                )
                continue
            results.append(result)
    finally:
        if owns_connection and active_connection is not None:
            await active_connection.close()
    return results


async def reconcile_wiki_deletions(
    wiki_dir: Path,
    *,
    conn: asyncpg.Connection | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Purge index rows for vault pages whose file is gone from `wiki_dir`.

    `ingest.reconcile_deletions` cannot do this job. It scopes a sweep by the
    watched directory's own *name*, expecting every row it owns to carry that
    name as a `source_uri` prefix. Vault rows carry no prefix at all —
    `ingest_wiki` passes `base_dir=wiki_dir`, so a page is stored as
    `Entities/OpenMontage.md`, not `wiki/Entities/OpenMontage.md`. Pointed at
    the vault, that function matches nothing and reports a clean sweep it never
    performed.

    Scoping here is by corpus tier: a candidate is a document every chunk of
    which is stamped `corpus = WIKI_CORPUS`, and its file is looked for at
    `wiki_dir / source_uri`. The raw corpus is never stamped, so a vault sweep
    cannot reach it.

    Returns the `source_uri` of every purged document, in sorted order.
    """
    from scout.ingest import _CORPUS_TIER_SQL

    # Strict for the same reason as `ingest_wiki`, and here it matters most:
    # this is the sweep that read a missing vault as an empty one and deleted
    # every row.
    root = _vault_root(wiki_dir)
    active = conn
    owns_connection = conn is None
    if active is None:
        active = await get_pg_connection(env)
    deleted: list[str] = []
    try:
        on_disk = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")  # noqa: ASYNC240
            if path.is_file()
        }
        rows = await active.fetch(_CORPUS_TIER_SQL, WIKI_CORPUS)
        indexed_pages = [
            str(row["source_uri"])
            for row in rows
            if str(row["source_uri"]) not in WIKI_CONTROL_DOCUMENTS
        ]
        # Pages are counted the way `ingest_wiki` counts them -- a `.md` that is
        # not a root control document -- not as every file under the root. The
        # vault skeleton ships `<category>/.gitkeep`, and attachments are files
        # too: counted as files, a publication holding only those passed for a
        # vault with pages, and the sweep below purged every row.
        pages_on_disk = {
            uri
            for uri in on_disk
            if uri.endswith(".md") and uri not in WIKI_CONTROL_DOCUMENTS
        }
        if indexed_pages and not pages_on_disk:
            # Control documents are left out on both sides: purging them is
            # this sweep's ordinary job, and a tree holding only them is still
            # a tree holding no page.
            raise ReconcileRefusedError(
                f"vault at {wiki_dir} holds no page while the index holds "
                f"{len(indexed_pages)}; refusing to purge every one of them"
            )
        for row in rows:
            uri = str(row["source_uri"])
            present = uri in on_disk or (root / uri).exists()
            if present and uri not in WIKI_CONTROL_DOCUMENTS:
                continue
            deleted.append(uri)
            if not dry_run:
                async with active.transaction():
                    await active.execute(
                        "DELETE FROM rag_documents WHERE doc_id = $1;",
                        row["doc_id"],
                    )
    finally:
        if owns_connection and active is not None:
            await active.close()
    return sorted(deleted)
