"""Scout-DIY — from-scratch wiki engine fallback (T-2.4 → R-2.4).

Exposes the design.md §2.2 minimal wiki-engine contract so this engine can
replace basic-memory on the SAME vault with zero data change:

    wiki_search(query, k)   -> [ {page_id, path, score, summary} ]
    wiki_read(page_id|path) -> { frontmatter, body }

Mechanism: embed each page's `summary` (the one-line routing text carried in
`page.frontmatter["summary"]`, §2.5) once, embed the query, rank by cosine
similarity, return the top-K descending by score. Reading a page is a direct
read of the file already loaded from the Git vault (§2.1) — the vector index
built here is purely derived and rebuildable, never the source of truth.

Embedding is behind an injected `Embedder` Protocol (mirrors
`scout.types.RagBackend`'s swappable-adapter style, R-4.8). `FakeEmbedder`
below is a deterministic, offline, dependency-free implementation used by
tests. `LiteLLMEmbedder` is the production implementation: it wraps bge-m3
via the LiteLLM chokepoint at ``http://localhost:4000`` (model tag
``snp-embed`` -> ``ollama/bge-m3``, see `config/litellm/config.yaml`) — the
SAME model used for RAG, chosen to unify Vietnamese recall between
wiki-search and RAG (design.md §2.2, gate R-8.4.4). It is constructed and
injected by the caller at runtime; this module makes no network call at
import time (the request is built lazily inside `LiteLLMEmbedder.embed`), so
importing and testing this module never requires network access.

Vault-reading is behind a small injectable seam: `ScoutDiyEngine.pages` is
either a ready `Sequence[PageLike]` or a zero-arg callable returning one, so
tests can hand the engine synthetic pages directly. `ScoutDiyEngine.from_vault`
is the convenience constructor that wires the real vault via
`spikes._lib.vault.load_pages`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from scout.core import normalize_path
from scout.types import RagBackend


@dataclass(frozen=True, slots=True)
class WikiHit:
    """One ranked `wiki_search` result (design.md §2.2 contract).

    Attributes:
        page_id: Stable page identifier (the vault filename stem/slug).
        path: Vault-relative path to the page file.
        score: Cosine similarity to the query; higher is more relevant.
        summary: The page's routing summary (`frontmatter["summary"]`).
    """

    page_id: str
    path: str
    score: float
    summary: str


@dataclass(frozen=True, slots=True)
class WikiPage:
    """A page read straight from the vault (design.md §2.2 contract).

    Attributes:
        frontmatter: Parsed YAML frontmatter (includes `sources[]`).
        body: Markdown body below the frontmatter block.
    """

    frontmatter: Mapping[str, object]
    body: str


@runtime_checkable
class Embedder(Protocol):
    """Swappable text-embedding backend (mirrors `scout.types.RagBackend`).

    Every embedding source — `FakeEmbedder` in tests, `LiteLLMEmbedder` in
    production — implements this one async method, so `ScoutDiyEngine`
    depends on the Protocol, never on a concrete embedding client.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text into a fixed-size vector, order preserved.

        Args:
            texts: Texts to embed, in order.

        Returns:
            One vector per input text, same order, same length as `texts`.
        """
        ...


@runtime_checkable
class PageLike(Protocol):
    """Structural shape of a vault page (matches `spikes._lib.vault.Page`).

    Kept structural rather than importing `spikes._lib.vault.Page` directly,
    so tests can pass any object with these four attributes — a real `Page`
    or a lightweight synthetic stand-in — without depending on the spike
    harness location.
    """

    frontmatter: Mapping[str, object]
    body: str
    slug: str
    rel: str


PageSupplier = Sequence[PageLike] | Callable[[], Sequence[PageLike]]


class _VaultModule(Protocol):
    """The slice of `spikes._lib.vault` this module relies on.

    `spikes/_lib/vault.py` is a spike-harness file outside this module's
    ownership (not `--strict` clean itself — untyped PyYAML, bare `dict`).
    Loading it via `importlib.import_module` and casting to this Protocol
    keeps mypy from having to type-check that file's contents on this
    module's behalf; only the shape used here is asserted.
    """

    def load_pages(self, wiki_dir: Path = ...) -> Sequence[PageLike]: ...


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        A value in ``[-1, 1]``, or ``0.0`` if either vector is empty,
        mismatched in length, or all-zero magnitude — guarding the
        divide-by-zero case instead of raising or returning NaN.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (norm_a * norm_b)


@dataclass(frozen=True, slots=True)
class _IndexedPage:
    """Internal: one vault page paired with its precomputed summary vector."""

    page_id: str
    path: str
    summary: str
    frontmatter: Mapping[str, object]
    body: str
    vector: tuple[float, ...]


@dataclass(slots=True)
class FakeEmbedder:
    """Deterministic, offline `Embedder` for tests (token-hash bag-of-words).

    Hashes each lowercased whitespace token into one of `dims` buckets with
    `hashlib.sha1` (stable across runs and processes, unlike Python's salted
    `hash()`) and accumulates term counts. Same text always yields the same
    vector, with no model or network dependency — which is what lets cosine
    ranking be tested fully offline.

    Attributes:
        dims: Vector width. Larger reduces hash collisions between distinct
            tokens; the default is ample for small test corpora.
    """

    dims: int = 64

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Hash-embed each text; text with no tokens yields an all-zero vector."""
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        import hashlib

        vec = [0.0] * self.dims
        for token in text.lower().split():
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            idx = int(digest, 16) % self.dims
            vec[idx] += 1.0
        return vec


@dataclass(slots=True)
class LiteLLMEmbedder:
    """Production `Embedder`: bge-m3 via the LiteLLM chokepoint (R-8.1).

    Wraps LiteLLM's OpenAI-compatible ``POST /v1/embeddings`` at
    `base_url` (default ``http://localhost:4000``, see
    `config/litellm/config.yaml`), routing through the `model` tag
    (default ``snp-embed`` -> ``ollama/bge-m3`` on a local Ollama — no cloud
    egress). Never constructed by the test suite; the HTTP request is built
    only inside `embed`, at call time, so nothing here reaches the network
    during import or collection.

    Attributes:
        base_url: LiteLLM base URL.
        model: LiteLLM `model_list[].model_name` tag to embed with.
        api_key: LiteLLM master key (bearer auth); defaults from
            the `LITELLM_MASTER_KEY` environment variable.
        timeout: Per-request timeout in seconds.
    """

    base_url: str = "http://localhost:4000"
    model: str = "snp-embed"
    api_key: str = field(
        default_factory=lambda: os.environ.get(
            "LITELLM_MASTER_KEY", "sk-local-dev-change-me"
        )
    )
    timeout: float = 300.0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """POST `texts` to LiteLLM `/v1/embeddings`; return vectors in order.

        Runs the blocking HTTP call in a worker thread (`asyncio.to_thread`)
        so it does not block the event loop.

        Raises:
            urllib.error.URLError: On a connection or HTTP-level failure.
            KeyError: If the response payload is missing expected fields.
        """
        return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        import json
        import urllib.request

        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/embeddings",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            payload = json.load(resp)
        ranked = sorted(payload["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in ranked]


@dataclass(slots=True)
class ScoutDiyEngine:
    """Scout-DIY wiki engine (T-2.4): a basic-memory drop-in fallback.

    Exposes `wiki_search`/`wiki_read` matching the design.md §2.2 contract
    exactly, so swapping this engine in for basic-memory is invisible to the
    agent and touches zero bytes of `wiki/` (T-2.4 DoD).

    Attributes:
        embedder: Injected `Embedder` — `FakeEmbedder` in tests,
            `LiteLLMEmbedder` in production.
        pages: Either a ready `Sequence[PageLike]` or a zero-arg callable
            returning one — the vault-reading seam. Tests pass synthetic
            pages directly; `from_vault` wires the real vault loader.
    """

    embedder: Embedder
    pages: PageSupplier
    cache_path: Path = field(
        default_factory=lambda: Path(".basic-memory/vector_cache.json")
    )
    rrf_k: int = 60
    rag_backend: RagBackend | None = None
    _index: list[_IndexedPage] | None = field(default=None, init=False, repr=False)
    _fts_conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_vault(
        cls,
        embedder: Embedder,
        wiki_dir: Path | None = None,
        cache_path: Path | None = None,
        rag_backend: RagBackend | None = None,
    ) -> ScoutDiyEngine:
        """Build an engine wired to the real Git vault (design.md §2.1).

        Args:
            embedder: The `Embedder` to use (typically `LiteLLMEmbedder`).
            wiki_dir: Optional override of the vault directory; defaults to
                `scout.vault.WIKI_DIR` when omitted.
            cache_path: Optional vector cache path.
            rag_backend: Optional RagBackend adapter for RAG fallback.

        Returns:
            A `ScoutDiyEngine` whose `pages` seam calls
            `scout.vault.load_pages` on first use.
        """
        def _load() -> Sequence[PageLike]:
            from typing import cast

            from scout import vault

            if wiki_dir is not None:
                return cast(Sequence[PageLike], list(vault.load_pages(wiki_dir)))
            return cast(Sequence[PageLike], list(vault.load_pages()))

        if cache_path is not None:
            return cls(
                embedder=embedder,
                pages=_load,
                cache_path=cache_path,
                rag_backend=rag_backend,
            )
        return cls(embedder=embedder, pages=_load, rag_backend=rag_backend)

    async def _ensure_index(self) -> list[_IndexedPage]:
        """Build (once) and cache the summary-vector index."""
        if self._index is not None:
            return self._index

        search_db_path = self.cache_path.parent / "search.db"
        if getattr(self, "_fts_conn", None) is None:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._fts_conn = sqlite3.connect(search_db_path)
                self._fts_conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(page_id UNINDEXED, title, summary, entities);"
                )
                self._fts_conn.commit()
            except sqlite3.Error:
                self._fts_conn = None

        source = self.pages
        loaded = source() if callable(source) else source
        pages = list(loaded)

        cache_data: dict[str, list[float]] = {}
        if self.cache_path.exists():
            try:
                with self.cache_path.open("r", encoding="utf-8") as f:
                    cache_data = json.load(f)
            except Exception:
                pass

        page_hashes: list[str] = []
        uncached_indices: list[int] = []
        uncached_summaries: list[str] = []

        for i, p in enumerate(pages):
            summary = str(p.frontmatter.get("summary", ""))
            entities = p.frontmatter.get("entities", [])

            if isinstance(entities, list):
                entities_str = ",".join(str(e) for e in sorted(entities))
            else:
                entities_str = str(entities)

            hash_input = f"{summary}::{entities_str}".encode()
            h = hashlib.sha256(hash_input).hexdigest()
            page_hashes.append(h)

            if h not in cache_data:
                uncached_indices.append(i)
                uncached_summaries.append(summary)

        if uncached_summaries:
            new_vectors = await self.embedder.embed(uncached_summaries)
            for idx, vec in zip(uncached_indices, new_vectors, strict=True):
                h = page_hashes[idx]
                cache_data[h] = vec

            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("w", encoding="utf-8") as f:
                json.dump(cache_data, f)

        # Synchronize and rebuild FTS index if search.db was missing/recreated
        if self._fts_conn is not None:
            try:
                cursor = self._fts_conn.execute("SELECT page_id FROM fts_index")
                existing_page_ids = {row[0] for row in cursor.fetchall()}
            except sqlite3.Error:
                existing_page_ids = set()

            fts_updated = False
            for p in pages:
                if p.slug not in existing_page_ids or uncached_summaries:
                    page_id = p.slug
                    title = str(p.frontmatter.get("title", ""))
                    summary_str = str(p.frontmatter.get("summary", ""))
                    entities = p.frontmatter.get("entities", [])
                    entities_str = (
                        ",".join(str(e) for e in sorted(entities))
                        if isinstance(entities, list)
                        else str(entities)
                    )
                    try:
                        self._fts_conn.execute(
                            "DELETE FROM fts_index WHERE page_id = ?", (page_id,)
                        )
                        self._fts_conn.execute(
                            "INSERT INTO fts_index(page_id, title, summary, entities) VALUES (?, ?, ?, ?)",
                            (page_id, title, summary_str, entities_str),
                        )
                        fts_updated = True
                    except sqlite3.Error:
                        pass

            if fts_updated:
                with contextlib.suppress(sqlite3.Error):
                    self._fts_conn.commit()

        index = [
            _IndexedPage(
                page_id=p.slug,
                path=p.rel,
                summary=str(p.frontmatter.get("summary", "")),
                frontmatter=p.frontmatter,
                body=p.body,
                vector=tuple(cache_data[page_hashes[i]]),
            )
            for i, p in enumerate(pages)
        ]
        self._index = index
        return index

    def _fts_search(self, query: str, limit: int) -> dict[str, int]:
        """Execute BM25 keyword search returning page_id -> rank mapping."""
        if not getattr(self, "_fts_conn", None):
            return {}

        terms = [
            t.replace('"', "").replace("'", "")
            for t in query.split()
            if t.replace('"', "").replace("'", "")
        ]
        if not terms:
            return {}

        match_query = " ".join(f'"{t}"' for t in terms)
        try:
            assert self._fts_conn is not None
            cursor = self._fts_conn.execute(
                "SELECT page_id FROM fts_index WHERE fts_index MATCH ? ORDER BY bm25(fts_index) LIMIT ?",
                (match_query, limit),
            )
            return {row[0]: rank for rank, row in enumerate(cursor.fetchall())}
        except sqlite3.Error:
            return {}

    async def wiki_search(self, query: str, k: int = 5) -> list[WikiHit]:
        """Rank vault pages by RRF combining cosine similarity and BM25 search.

        Args:
            query: Free-text search query.
            k: Max results to return.

        Returns:
            Up to `k` `WikiHit`s, highest RRF score first. Empty when the
            index has no pages or `k <= 0`.
        """
        if k <= 0:
            return []

        index = await self._ensure_index()
        if not query.strip():
            # If query is empty, just return the first k pages with a low score.
            return [
                WikiHit(
                    page_id=ip.page_id,
                    path=ip.path,
                    score=0.1,
                    summary=ip.summary,
                )
                for ip in index[:k]
            ]

        if not index:
            if self.rag_backend:
                try:
                    chunks = await self.rag_backend.retrieve(hint=query, k=k)
                    return [
                        WikiHit(
                            page_id=Path(c.file_path).stem,
                            path=c.file_path,
                            score=c.score,
                            summary=(c.text[:200] + "...")
                            if len(c.text) > 200
                            else c.text,
                        )
                        for c in chunks
                    ]
                except Exception:
                    pass
            return []

        (query_vector,) = await self.embedder.embed([query])

        # 1. Vector Search Rankings
        vector_hits = [(ip, cosine_similarity(query_vector, ip.vector)) for ip in index]
        vector_hits.sort(key=lambda x: x[1], reverse=True)
        vector_ranks = {ip.page_id: rank for rank, (ip, _) in enumerate(vector_hits)}

        # 2. FTS/BM25 Rankings
        # limit keyword search to enough results, e.g. 50, to fuse well
        fts_ranks = self._fts_search(query, limit=50)

        # 3. Combine with RRF
        hits = []
        for ip in index:
            v_rank = vector_ranks.get(ip.page_id, 1000)
            f_rank = fts_ranks.get(ip.page_id, 1000)

            # If a page isn't in FTS results, f_rank is 1000 (negligible contribution)
            rrf_score = (1.0 / (self.rrf_k + v_rank)) + (1.0 / (self.rrf_k + f_rank))
            hits.append(
                WikiHit(
                    page_id=ip.page_id,
                    path=ip.path,
                    score=rrf_score,
                    summary=ip.summary,
                )
            )

        hits.sort(key=lambda h: h.score, reverse=True)

        hits = hits[:k]

        if not hits and self.rag_backend:
            try:
                chunks = await self.rag_backend.retrieve(hint=query, k=k)
                hits = [
                    WikiHit(
                        page_id=Path(c.file_path).stem,
                        path=c.file_path,
                        score=c.score,
                        summary=(c.text[:200] + "...") if len(c.text) > 200 else c.text,
                    )
                    for c in chunks
                ]
            except Exception:
                pass

        return hits

    async def wiki_read(self, path: str) -> WikiPage:
        """Read one page by `page_id` or vault-relative `path`.

        Args:
            path: Either a page's `page_id` (slug) or its `path`, as
                returned by `wiki_search`.

        Returns:
            The page's frontmatter and body.

        Raises:
            KeyError: No page matches `path`.
        """
        index = await self._ensure_index()
        target = normalize_path(path)
        for ip in index:
            if ip.page_id == path or normalize_path(ip.path) == target:
                return WikiPage(frontmatter=ip.frontmatter, body=ip.body)
        raise KeyError(f"no such wiki page: {path}")
