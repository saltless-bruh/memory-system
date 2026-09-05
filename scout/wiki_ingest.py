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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import asyncpg

from scout import vault
from scout.chunker import ContextualChunker, Embedder, LiteLLMBatchEmbedder
from scout.ingest import get_pg_connection, ingest_document
from scout.parsers import ParsedDocument, ParsedSection

WIKI_ALLOWED_DEPARTMENTS = ("redteam", "blueteam", "ai_eng", "infra")
#: The corpus tier stamped on every chunk this module writes. It is the only
#: thing that distinguishes a vault row from a raw-corpus row: both live in one
#: flat `source_uri` namespace and the vault has its own top-level `raw/`
#: folder, so the path prefix cannot say who owns a row.
WIKI_CORPUS = "wiki"
WIKI_TARGET_CHUNK_TOKENS = 350
WIKI_MAX_CHUNK_CHARS = 1400

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
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


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


async def ingest_wiki(
    wiki_dir: Path,
    *,
    conn: asyncpg.Connection | None = None,
    embedder: Embedder | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
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
    root = wiki_dir.resolve()  # noqa: ASYNC240
    pages = vault.load_pages(root)
    # Exclude control documents from chunk ingestion (index.md is already excluded
    # by vault.load_pages, but log.md is authored and must stay readable for wiki_read).
    # Match against the root-level control document only, not any file with that name.
    # Use resolved path to match vault.load_pages which returns absolute paths.
    control_log = root / "log.md"
    pages = [p for p in pages if p.path != control_log]
    catalog = load_index_catalog(root / "index.md")
    settings = os.environ if env is None else env
    selected_embedder = embedder or LiteLLMBatchEmbedder(
        base_url=settings.get("LITELLM_BASE_URL"),
        api_key=settings.get("LITELLM_MASTER_KEY"),
        model=settings.get("LITELLM_EMBED_MODEL"),
    )
    chunker = ContextualChunker(
        max_chunk_chars=WIKI_MAX_CHUNK_CHARS,
        overlap_chars=0,
    )

    owns_connection = conn is None and not dry_run
    active_connection = conn
    if owns_connection:
        active_connection = await get_pg_connection(env)

    results: list[dict[str, object]] = []
    try:
        for page in pages:
            source_hash = hashlib.sha256(page.path.read_bytes()).hexdigest()

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
                )
            except WikiIngestError as exc:
                # A single unwritable page must not abort the corpus. The
                # reference vault carries at least one zero-byte stub, and
                # Obsidian creates one on every "new note". Report it and
                # continue, matching ingest_document's own skipped_empty
                # vocabulary for sources that yield no text.
                results.append(
                    {
                        "source_uri": str(page.path.relative_to(root)),
                        "title": page.title,
                        "chunks_count": 0,
                        "status": "skipped_no_body",
                        "reason": str(exc),
                    }
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

    root = wiki_dir.resolve()  # noqa: ASYNC240
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
        for row in rows:
            uri = str(row["source_uri"])
            if uri in on_disk or (root / uri).exists():
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
