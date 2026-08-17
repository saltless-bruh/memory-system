"""Core types and the RAG-backend interface for Scout (R-4, R-4.8).

This module is the **stable contract** that makes the RAG engine a swappable
slot (design.md §2.3). `scout.core` and the agent-facing layer depend only on
what is defined here — never on RAG-Anything's specific API. Each RAG engine
is an adapter under `scout.backends` implementing `RagBackend`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Address:
    """A pointer from a wiki page into RAG (`frontmatter.sources[]`).

    Attributes:
        path: The `raw/` file this address resolves to. Scout guarantees only
            chunks from this file are returned (post-filter, R-4.3).
        hint: The phrase RAG matches on to retrieve the passage.
        loc: Optional human locator (e.g. ``"p.12-14"``) carried into citations.
    """

    path: str
    hint: str
    loc: str | None = None


@dataclass(frozen=True, slots=True)
class Scope:
    """Caller access context — the RBAC hook (R-4.8).

    Present in the interface from V1 but **not enforced** by the V1 backend
    (RAG-Anything cannot do row-level RBAC). A V2 backend (R2R / pgvector+RLS)
    reads this to restrict which chunks the caller may retrieve. Carrying it
    from day one means V2 enforces something already flowing through, rather
    than a signature change everywhere. See Suggestion_V2_RAG_Replacement.md.
    """

    roles: frozenset[str] = frozenset()
    team: str | None = None
    clearance: str | None = None


@dataclass(frozen=True, slots=True)
class RagChunk:
    """One retrieved passage from a RAG backend.

    Attributes:
        text: The verbatim source passage (never summarized — R-3.3).
        file_path: The source file this chunk came from; drives the post-filter.
        score: Backend relevance score (higher is better); 0.0 if unranked.
        loc: Optional locator within the file.
        meta: Backend metadata carried through for citation and V2 filtering —
            e.g. ``team``/``department``/``classification`` (R-4.8).
    """

    text: str
    file_path: str
    score: float = 0.0
    loc: str | None = None
    meta: Mapping[str, str] = field(default_factory=dict)


class FetchStatus(StrEnum):
    """Outcome of a `rag_fetch`. `NO_SOURCE` is returned rather than fabricated
    content when retrieval is empty (R-4.5)."""

    OK = "ok"
    NO_SOURCE = "no_source"


@dataclass(frozen=True, slots=True)
class ContextPiece:
    """A verbatim quote handed back to the agent — data, never instructions.

    The schema is deliberately fixed to **text + provenance only**. There is
    no ``action``/``command`` field: retrieved `raw/` content is treated as
    data, which is the structural half of the prompt-injection guard (R-8.5).
    """

    text: str
    file_path: str
    loc: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """Provenance for a returned quote (R-4.4)."""

    file_path: str
    loc: str | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What `rag_fetch` returns: only quotes + citations (R-4.4).

    No interpretation, no action field (R-8.5). On `NO_SOURCE`, `context` and
    `citations` are empty and the agent falls back to the wiki page it already
    has (R-4.5).
    """

    status: FetchStatus
    context: tuple[ContextPiece, ...] = ()
    citations: tuple[Citation, ...] = ()

    @property
    def ok(self) -> bool:
        """True when at least one on-source passage was retrieved."""
        return self.status is FetchStatus.OK


@runtime_checkable
class RagBackend(Protocol):
    """The swappable RAG engine contract (R-4.8).

    Every RAG engine — RAG-Anything (V1), R2R or pgvector+RLS (V2) — is an
    adapter implementing this one async method. Scout core depends on this
    Protocol, not on any engine's API, so swapping the engine is "add an
    adapter", not a rewrite.

    Implementations MUST return **verbatim** passages (the equivalent of
    ``only_need_context=True``) and MUST NOT synthesize answers.
    """

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        """Retrieve verbatim passages for `hint`.

        Args:
            hint: The phrase to match (from an `Address`).
            path: Optional source-file constraint. A backend that can pre-filter
                (R2R/pgvector) should; RAG-Anything cannot, so Scout post-filters
                regardless (R-4.3).
            scope: Caller RBAC context. V1 backends ignore it; V2 backends
                enforce it (R-4.8).
            k: Max passages to return.

        Returns:
            Retrieved chunks (possibly from files other than `path` — Scout
            applies the authoritative post-filter).
        """
        ...
