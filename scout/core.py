"""Scout core — the engine-independent RAG bridge logic (R-4).

Depends only on `scout.types`, never on a concrete RAG engine. Given an
`Address` and a `RagBackend`, it retrieves verbatim passages, enforces the
authoritative post-filter (R-4.3), degrades honestly to ``no_source`` (R-4.5),
and packages quotes + citations (R-4.4) — nothing else (injection guard, R-8.5).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

from scout.types import (
    Address,
    Citation,
    ContextPiece,
    FetchResult,
    FetchStatus,
    RagBackend,
    RagChunk,
    Scope,
)


def normalize_path(path: str) -> str:
    """Normalize a file path for equality comparison.

    RAG backends may echo paths with mixed separators or surrounding
    whitespace; the post-filter compares on a canonical POSIX form so
    ``raw\\reports\\a.pdf`` and ``raw/reports/a.pdf`` match.

    Args:
        path: A raw file path from a chunk or an address.

    Returns:
        The path as a normalized POSIX string.
    """
    return PurePosixPath(path.strip().replace("\\", "/")).as_posix()


def post_filter(chunks: Iterable[RagChunk], path: str) -> list[RagChunk]:
    """Keep only chunks whose `file_path` matches `path` (R-4.3).

    This is Scout's authoritative guard and runs for **every** backend — even
    one that claims to pre-filter — because RAG-Anything merges the whole
    corpus and has no ``path_filter``. Results are sorted by descending score.

    Args:
        chunks: Raw chunks from a backend.
        path: The address path that results must belong to.

    Returns:
        On-source chunks, highest score first.
    """
    target = normalize_path(path)
    kept = [c for c in chunks if normalize_path(c.file_path) == target]
    kept.sort(key=lambda c: c.score, reverse=True)
    return kept


async def rag_fetch(
    backend: RagBackend,
    address: Address,
    *,
    scope: Scope | None = None,
    k: int = 10,
) -> FetchResult:
    """Resolve one address to verbatim quotes + citations (R-4.1, R-4.3–R-4.5).

    Args:
        backend: Any `RagBackend` adapter (RAG-Anything today; R2R/pgvector
            in V2). Scout does not care which — that is R-4.8.
        address: The `sources[]` pointer to resolve.
        scope: Caller RBAC context; passed through to the backend (V1 ignores
            it, V2 enforces it — R-4.8).
        k: Max passages to request.

    Returns:
        A `FetchResult` with only quotes + citations, or `NO_SOURCE` when the
        hint retrieves nothing on the addressed file — never fabricated (R-4.5).
    """
    chunks = await backend.retrieve(address.hint, path=address.path, scope=scope, k=k)
    kept = post_filter(chunks, address.path)
    if not kept:
        return FetchResult(status=FetchStatus.NO_SOURCE)

    context = tuple(
        ContextPiece(text=c.text, file_path=c.file_path, loc=c.loc or address.loc)
        for c in kept
    )
    citations = tuple(
        Citation(file_path=c.file_path, loc=c.loc or address.loc, score=c.score)
        for c in kept
    )
    return FetchResult(status=FetchStatus.OK, context=context, citations=citations)


async def rag_fetch_many(
    backend: RagBackend,
    addresses: Sequence[Address],
    *,
    scope: Scope | None = None,
    k: int = 10,
) -> tuple[FetchResult, ...]:
    """Resolve several addresses, one result each, with distinct citations (R-4.7).

    Each address is resolved independently and concurrently; the returned tuple
    is aligned with `addresses` (a two-source page yields two grouped results,
    each cited to its own file — T-2.6).

    Args:
        backend: The RAG backend adapter.
        addresses: The page's `sources[]` addresses.
        scope: Caller RBAC context (see `rag_fetch`).
        k: Max passages per address.

    Returns:
        One `FetchResult` per input address, in the same order.
    """
    results = await asyncio.gather(
        *(rag_fetch(backend, a, scope=scope, k=k) for a in addresses)
    )
    return tuple(results)
