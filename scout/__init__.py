"""Scout — the engine-independent RAG bridge (R-4).

Public surface: the core `rag_fetch`/`rag_fetch_many` functions and the types
that make the RAG engine swappable (R-4.8). Concrete RAG engines live under
`scout.backends`.
"""

from __future__ import annotations

from scout.core import normalize_path, post_filter, rag_fetch, rag_fetch_many
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

__all__ = [
    "Address",
    "Citation",
    "ContextPiece",
    "FetchResult",
    "FetchStatus",
    "RagBackend",
    "RagChunk",
    "Scope",
    "normalize_path",
    "post_filter",
    "rag_fetch",
    "rag_fetch_many",
]
