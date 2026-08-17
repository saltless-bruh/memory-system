"""RAG-Anything adapter — the V1 RAG engine behind the interface (R-3, R-4.8).

Thin on purpose: it maps `RagBackend.retrieve` onto LightRAG's
``aquery(..., only_need_context=True)`` (R-3.3) and parses the result into
`RagChunk`s carrying real per-chunk `file_path` so Scout's post-filter (R-4.3)
is meaningful.

LightRAG is imported lazily inside `retrieve`, so importing this module — and
running the whole Scout test suite — never requires the heavy RAG-Anything /
MinerU stack.

⚠️ VERIFY AT T-2.1: LightRAG's ``only_need_context`` return shape is
version-dependent. `default_parse` handles the common shapes (list of chunk
dicts, a ``{"chunks": [...]}`` envelope, or a bare context string) but MUST be
confirmed against a live instance and adjusted if the real output differs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from scout.types import RagChunk, Scope

ContextParser = Callable[[object, str], list[RagChunk]]


def _chunk_from_mapping(item: dict[str, object], fallback_path: str) -> RagChunk:
    """Build a `RagChunk` from one LightRAG chunk mapping."""
    text = str(item.get("content") or item.get("text") or "")
    file_path = str(item.get("file_path") or item.get("source") or fallback_path)
    raw_score = item.get("score") or item.get("distance") or 0.0
    score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
    loc_val = item.get("loc") or item.get("page")
    loc = str(loc_val) if loc_val is not None else None
    meta_val = item.get("meta")
    if isinstance(meta_val, dict):
        meta = {str(k): str(v) for k, v in meta_val.items()}
    else:
        meta = {}
    return RagChunk(text=text, file_path=file_path, score=score, loc=loc, meta=meta)


def default_parse(raw: object, path: str) -> list[RagChunk]:
    """Parse a LightRAG ``only_need_context`` result into chunks.

    Args:
        raw: LightRAG's return — a list of chunk mappings, a ``{"chunks": [...]}``
            envelope, or a bare context string.
        path: The queried address path, used to tag chunks that lack an explicit
            ``file_path``.

    Returns:
        Parsed chunks. A bare string becomes a single chunk tagged with `path`
        (best-effort — see the module VERIFY note; without per-chunk file_path
        the post-filter cannot distinguish sources, which is why real output
        must be confirmed at T-2.1).
    """
    items: object = raw
    if isinstance(raw, dict):
        items = raw.get("chunks") or raw.get("data") or raw.get("context") or raw

    if isinstance(items, list):
        return [_chunk_from_mapping(it, path) for it in items if isinstance(it, dict)]
    if isinstance(items, str):
        text = items.strip()
        return [RagChunk(text=text, file_path=path)] if text else []
    return []


class _AqueryRag(Protocol):
    """Structural type for the injected LightRAG/RAGAnything instance."""

    async def aquery(self, query: str, param: object = ...) -> object: ...


@dataclass(slots=True)
class RagAnythingBackend:
    """`RagBackend` over a LightRAG/RAGAnything instance.

    Attributes:
        rag: A LightRAG/RAGAnything instance exposing ``aquery``.
        mode: LightRAG query mode (``"mix"`` per design §2.3).
        parse: Result parser (override to match a specific LightRAG version).
    """

    rag: _AqueryRag
    mode: str = "mix"
    parse: ContextParser = field(default=default_parse)

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        """Retrieve verbatim passages via LightRAG (`only_need_context=True`).

        `scope` is accepted for interface parity (R-4.8) but ignored: RAG-Anything
        cannot enforce role-based access (that is the V2 swap). `path` is not sent
        to LightRAG (it has no `path_filter`); Scout post-filters instead (R-4.3).
        """
        from lightrag import QueryParam  # lazy: heavy dep, only at call time

        param = QueryParam(mode=self.mode, only_need_context=True, top_k=k)
        raw = await self.rag.aquery(hint, param=param)
        return self.parse(raw, path or "")
