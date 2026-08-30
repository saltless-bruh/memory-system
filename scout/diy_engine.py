"""V3 wiki retrieval engine backed by the shared PostgreSQL index.

``wiki_search`` is an authenticated, page-level view over ``RagBackend``.
``wiki_read`` deliberately bypasses that index and normalises the current
Markdown file from disk.  The vault therefore remains the source of truth and
there is no derived SQLite, FTS, or vector-cache copy beside it.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol, cast, runtime_checkable

from scout import vault
from scout.chunker import AsyncEmbedder, LiteLLMBatchEmbedder
from scout.core import normalize_path
from scout.parsers import parse_markdown
from scout.types import RagBackend, RagChunk, Scope
from scout.wiki_ingest import (
    IndexCatalog,
    load_index_catalog,
    prepare_wiki_document,
)

# One production embedding implementation and one async method name are used by
# ingestion, retrieval, and this engine.  Keep the historic imports as aliases
# while callers migrate; neither alias introduces another implementation.
Embedder = AsyncEmbedder
LiteLLMEmbedder = LiteLLMBatchEmbedder

_WORD = re.compile(r"\S+")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_MAX_SNIPPET_WORDS = 40
_READ_MODES = frozenset({"full", "tldr", "outline"})


@dataclass(frozen=True, slots=True)
class WikiHit:
    """One page-level search result.

    ``page_id`` and ``summary`` preserve the original engine's Python contract;
    ``snippet`` exposes the V3 name without duplicating content.
    """

    page_id: str
    path: str
    score: float
    summary: str
    type: str = "unknown"
    title: str = ""
    content_hash: str = ""
    seen: bool = False
    degraded: bool = False
    reason: str | None = None

    @property
    def snippet(self) -> str:
        """The bounded V3 snippet, empty for a previously seen page."""
        return "" if self.seen else self.summary

    def canonical(self) -> dict[str, object]:
        """Return the bounded agent-facing search shape."""
        if self.seen:
            return {"path": self.path, "title": self.title, "seen": True}
        result: dict[str, object] = {
            "path": self.path,
            "type": self.type,
            "score": self.score,
            "snippet": self.snippet,
            "seen": False,
            "degraded": self.degraded,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True, slots=True)
class WikiPage:
    """A disk-backed page plus its canonical V3 read envelope.

    ``frontmatter`` and ``body`` are retained for the internal workflow adapter;
    tools publish :meth:`canonical`, not those compatibility fields.
    """

    frontmatter: Mapping[str, object] = field(default_factory=dict, repr=False)
    body: str = field(default="", repr=False)
    path: str = ""
    title: str = ""
    type: str = "unknown"
    updated: str | None = None
    tldr: str = ""
    outline: tuple[Mapping[str, object], ...] = ()
    sections: Mapping[str, str] = field(default_factory=dict)
    sources: tuple[object, ...] = ()
    links: tuple[str, ...] = ()
    content_hash: str = ""
    mode: str = "full"

    def canonical(self) -> dict[str, object]:
        """Return only fields permitted by the selected read granularity."""
        base: dict[str, object] = {
            "path": self.path,
            "title": self.title,
            "type": self.type,
            "tldr": self.tldr,
            "content_hash": self.content_hash,
        }
        if self.mode == "tldr":
            return base
        if self.mode == "outline":
            return {**base, "outline": list(self.outline)}
        if self.mode == "section":
            return {**base, "sections": dict(self.sections)}
        return {
            **base,
            "updated": self.updated,
            "outline": list(self.outline),
            "sections": dict(self.sections),
            "sources": list(self.sources),
            "links": list(self.links),
        }


@runtime_checkable
class PageLike(Protocol):
    """Structural subset shared by real and synthetic vault pages."""

    frontmatter: Mapping[str, object]
    body: str
    slug: str
    rel: str


PageSupplier = Sequence[PageLike] | Callable[[], Sequence[PageLike]]


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _bounded_snippet(text: str) -> str:
    return " ".join(_WORD.findall(text)[:_MAX_SNIPPET_WORDS])


def _page_id(path: str) -> str:
    return Path(normalize_path(path)).stem


def _hit_path(chunk: RagChunk) -> str:
    routed = chunk.meta.get("wiki_path")
    return normalize_path(str(routed or chunk.file_path))


def _hit_from_chunk(chunk: RagChunk, seen: frozenset[str]) -> WikiHit:
    """Adapt a ranked backend chunk to the stable page-level result shape."""
    meta = chunk.meta
    path = _hit_path(chunk)
    content_hash = str(meta.get("content_hash", ""))
    already_seen = bool(content_hash and content_hash in seen)
    title = str(meta.get("title") or _page_id(path))
    preferred = str(meta.get("tldr") or chunk.text)
    degraded = _as_bool(meta.get("degraded", False))
    reason = str(meta["degraded_reason"]) if "degraded_reason" in meta else None
    return WikiHit(
        page_id=_page_id(path),
        path=path,
        score=chunk.score,
        summary="" if already_seen else _bounded_snippet(preferred),
        type=str(meta.get("type") or "unknown"),
        title=title,
        content_hash=content_hash,
        seen=already_seen,
        degraded=degraded,
        reason=reason,
    )


def _updated(frontmatter: Mapping[str, object]) -> str | None:
    value = frontmatter.get("updated", frontmatter.get("last_compiled"))
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _links(body: str) -> tuple[str, ...]:
    result: list[str] = []
    for match in _WIKILINK.finditer(body):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if target and target not in result:
            result.append(target)
    return tuple(result)


def _sections(body: str) -> dict[str, str]:
    """Parse Markdown headings without treating horizontal rules as headings."""
    matches = list(_HEADING.finditer(body))
    if not matches:
        text = body.strip()
        return {"Intro": text} if text else {}

    records: list[tuple[str, str, str]] = []
    intro = body[: matches[0].start()].strip()
    if intro:
        records.append(("Intro", "Intro", intro))

    stack: list[str] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        stack[level - 1 :] = [heading]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        text = body[match.end() : end].strip()
        if text:
            records.append((heading, " > ".join(stack), text))

    counts: dict[str, int] = {}
    for heading, _path, _text in records:
        counts[heading.casefold()] = counts.get(heading.casefold(), 0) + 1
    result: dict[str, str] = {}
    for heading, path, text in records:
        key = path if counts[heading.casefold()] > 1 else heading
        result[key] = text
    return result


def _select_section(sections: Mapping[str, str], requested: str) -> dict[str, str]:
    wanted = requested.strip().casefold()
    if not wanted:
        raise ValueError("section must not be empty")
    exact = [(key, value) for key, value in sections.items() if key.casefold() == wanted]
    if len(exact) == 1:
        return dict(exact)
    leaf = [
        (key, value)
        for key, value in sections.items()
        if key.rsplit(" > ", 1)[-1].casefold() == wanted
    ]
    if len(leaf) == 1:
        return dict(leaf)
    if len(leaf) > 1:
        raise KeyError(f"ambiguous wiki section: {requested}")
    raise KeyError(f"no such wiki section: {requested}")


@dataclass(slots=True)
class ScoutDiyEngine:
    """Scoped wiki search over pgvector and canonical reads from the vault."""

    embedder: Embedder
    pages: PageSupplier = field(default_factory=tuple)
    rag_backend: RagBackend | None = None
    wiki_dir: Path | None = None
    # Accepted for source compatibility only.  No cache is read or written.
    cache_path: Path | None = field(default=None, repr=False)
    rrf_k: int = field(default=60, repr=False)
    _owns_backend: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> ScoutDiyEngine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    async def __aenter__(self) -> ScoutDiyEngine:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.aclose()

    @classmethod
    def from_vault(
        cls,
        embedder: Embedder,
        wiki_dir: Path | None = None,
        cache_path: Path | None = None,
        rag_backend: RagBackend | None = None,
    ) -> ScoutDiyEngine:
        """Wire the shared backend and a lazily read vault directory."""
        root = wiki_dir or vault.WIKI_DIR

        def load() -> Sequence[PageLike]:
            return cast(Sequence[PageLike], vault.load_pages(root))

        return cls(
            embedder=embedder,
            pages=load,
            rag_backend=rag_backend,
            wiki_dir=root,
            cache_path=cache_path,
        )

    async def _ensure_index(self) -> RagBackend:
        """Return the shared backend; no process-local index is constructed."""
        if self.rag_backend is None:
            from scout.backends.pgvector import PgVectorRlsBackend

            self.rag_backend = PgVectorRlsBackend(embedder=self.embedder)
            self._owns_backend = True
        return self.rag_backend

    async def aclose(self) -> None:
        """Close an internally created backend without owning injected ones."""
        if not self._owns_backend or self.rag_backend is None:
            return
        close = getattr(self.rag_backend, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
        self.rag_backend = None
        self._owns_backend = False

    def close(self) -> None:
        """Synchronous compatibility hook for callers outside an event loop."""
        if not self._owns_backend:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
        else:
            raise RuntimeError("use 'await engine.aclose()' inside an event loop")

    async def wiki_search(
        self,
        query: str,
        k: int = 5,
        *,
        seen: Sequence[str] = (),
        scope: Scope | None = None,
    ) -> list[WikiHit]:
        """Return at most ``k`` distinct pages using the caller's exact scope."""
        if scope is None:
            raise ValueError("wiki_search requires an authenticated scope")
        if k <= 0 or not query.strip():
            return []

        backend = await self._ensure_index()
        chunks = await backend.retrieve(query, scope=scope, k=k)
        seen_hashes = frozenset(str(value) for value in seen if str(value))
        hits: list[WikiHit] = []
        paths: set[str] = set()
        for chunk in chunks:
            hit = _hit_from_chunk(chunk, seen_hashes)
            if hit.path in paths:
                continue
            paths.add(hit.path)
            hits.append(hit)
            if len(hits) == k:
                break
        return hits

    def _loaded_pages(self) -> list[PageLike]:
        source = self.pages
        return list(source() if callable(source) else source)

    def _disk_page(self, identifier: str) -> tuple[vault.Page, str]:
        if self.wiki_dir is None:
            raise KeyError(f"no disk vault configured for wiki page: {identifier}")
        root = self.wiki_dir.resolve(strict=True)
        target = normalize_path(identifier)
        if target.startswith("wiki/"):
            target = target.removeprefix("wiki/")

        direct = (root / target).resolve(strict=False)
        if direct.suffix.casefold() != ".md":
            direct = direct.with_suffix(".md")
        try:
            direct.relative_to(root)
        except ValueError as exc:
            raise KeyError(f"wiki page escapes the vault: {identifier}") from exc
        if direct.is_file() and not direct.is_symlink():
            return vault.parse_page(direct), direct.relative_to(root).as_posix()

        matches: list[vault.Page] = []
        wanted = target.casefold()
        for page in vault.load_pages(root):
            rel = page.path.relative_to(root).as_posix()
            if (
                page.slug.casefold() == wanted
                or page.title.casefold() == wanted
                or rel.casefold() == wanted
                or rel.removesuffix(".md").casefold() == wanted
            ):
                matches.append(page)
        if len(matches) != 1:
            qualifier = "ambiguous" if matches else "no such"
            raise KeyError(f"{qualifier} wiki page: {identifier}")
        selected = matches[0]
        return vault.parse_page(selected.path), selected.path.relative_to(root).as_posix()

    def _memory_page(self, identifier: str) -> tuple[PageLike, str]:
        target = normalize_path(identifier)
        matches = [
            page
            for page in self._loaded_pages()
            if page.slug == identifier or normalize_path(page.rel) == target
        ]
        if len(matches) != 1:
            raise KeyError(f"no such wiki page: {identifier}")
        return matches[0], normalize_path(matches[0].rel)

    async def wiki_read(
        self,
        path: str,
        *,
        mode: str = "full",
        section: str | None = None,
        scope: Scope | None = None,
    ) -> WikiPage:
        """Normalise the current Markdown file into the canonical envelope."""
        if scope is None:
            raise ValueError("wiki_read requires an authenticated scope")
        if section is None and mode not in _READ_MODES:
            raise ValueError(f"unsupported wiki_read mode: {mode}")

        if self.wiki_dir is not None:
            disk_page, rel = self._disk_page(path)
            page: PageLike = cast(PageLike, disk_page)
            raw = disk_page.path.read_bytes()
            raw_text = raw.decode("utf-8", errors="replace")
            catalog = load_index_catalog(self.wiki_dir / "index.md")
        else:
            page, rel = self._memory_page(path)
            raw_text = page.body
            raw = raw_text.encode("utf-8")
            catalog = IndexCatalog.empty()

        parsed = parse_markdown(raw_text, rel)
        digest = hashlib.sha256(raw).hexdigest()
        prepared = prepare_wiki_document(parsed, catalog, content_hash=digest)
        all_sections = _sections(page.body)
        selected_sections = (
            _select_section(all_sections, section)
            if section is not None
            else all_sections
        )
        sources_value = page.frontmatter.get("sources", [])
        sources = tuple(sources_value) if isinstance(sources_value, list) else ()
        outline_value = prepared.metadata.get("outline", [])
        outline = (
            tuple(cast(Sequence[Mapping[str, object]], outline_value))
            if isinstance(outline_value, list)
            else ()
        )
        return WikiPage(
            frontmatter=page.frontmatter,
            body=page.body,
            path=rel,
            title=prepared.title,
            type=str(prepared.metadata.get("type") or "unknown"),
            updated=_updated(page.frontmatter),
            tldr=str(prepared.metadata["tldr"]),
            outline=outline,
            sections=selected_sections,
            sources=sources,
            links=_links(page.body),
            content_hash=digest,
            mode="section" if section is not None else mode,
        )
