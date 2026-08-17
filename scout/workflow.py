"""End-to-end query workflow (T-3.1 → R-5.1, R-5.4).

A reference, testable orchestration of the design §4.1 query flow. In
production the coding agent drives this via MCP (basic-memory `search_notes`/
`read_note`, then Scout `rag_fetch`) exactly as `AGENTS.md` instructs; this
module encodes the same sequence as one async function so the flow is
executable, traceable, and — crucially — so the "the page already answers it,
**stop, do not go to RAG**" branch (R-5.1) is enforced by a test.

The flow:
    1. `wiki_search(query)` -> top-K pages.
    2. `wiki_read(top)` -> body + `sources[]`.
    3. If the page suffices -> answer from it, **do not call RAG** (R-5.1).
    4. Else -> hand the page's `sources[]` addresses to Scout, which
       retrieves + post-filters + cites (R-5.4). Scout never reads the vault.

Both the wiki engine and the RAG backend are injected behind Protocols, so
this works over basic-memory or Scout-DIY, and RAG-Anything or any V2 engine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from scout.core import rag_fetch_many
from scout.diy_engine import WikiHit, WikiPage
from scout.types import Address, Citation, ContextPiece, FetchStatus, RagBackend, Scope


class WikiEngine(Protocol):
    """The wiki lookup contract (design §2.2) — basic-memory or Scout-DIY."""

    async def wiki_search(self, query: str, k: int = 5) -> Sequence[WikiHit]:
        """Return top-K pages for `query`."""
        ...

    async def wiki_read(self, path: str) -> WikiPage:
        """Read one page by path or id."""
        ...


class AnswerStatus(StrEnum):
    """Outcome of an end-to-end query."""

    NO_PAGE = "no_page"  # wiki search found nothing
    PAGE_ONLY = "page_only"  # page sufficed; RAG not consulted (R-5.1)
    WITH_SOURCES = "with_sources"  # descended to RAG and got verbatim context
    NO_SOURCE = "no_source"  # descended to RAG but nothing retrieved (R-4.5)


# A page-sufficiency decision. In production the *agent* judges whether the
# page answers the question; here it is injectable. A bool is the simple case;
# a predicate can inspect the page. Default: the page suffices (stop, R-5.1).
NeedRag = bool | Callable[[WikiPage], bool]


@dataclass(frozen=True, slots=True)
class Answer:
    """Result of the end-to-end query flow.

    Attributes:
        query: The original query.
        status: Which branch was taken.
        page_path: The chosen wiki page (None if search found nothing).
        page_summary: The chosen page's summary (for the page-only answer).
        used_rag: Whether Scout/RAG was consulted — asserted False on the
            "page suffices" branch (R-5.1).
        context: Verbatim RAG passages (empty unless `used_rag`).
        citations: Provenance for `context`.
    """

    query: str
    status: AnswerStatus
    page_path: str | None = None
    page_summary: str | None = None
    used_rag: bool = False
    context: tuple[ContextPiece, ...] = ()
    citations: tuple[Citation, ...] = ()


def _addresses_from_page(page: WikiPage) -> list[Address]:
    """Extract `sources[]` addresses from a page's frontmatter (R-5.4)."""
    raw = page.frontmatter.get("sources") or []
    addresses: list[Address] = []
    if not isinstance(raw, list):
        return addresses
    for src in raw:
        if not isinstance(src, dict):
            continue
        path, hint = src.get("path"), src.get("hint")
        if not path or not hint:
            continue
        loc = src.get("loc")
        addresses.append(
            Address(path=str(path), hint=str(hint), loc=str(loc) if loc else None)
        )
    return addresses


async def answer_query(
    wiki: WikiEngine,
    rag: RagBackend,
    query: str,
    *,
    need_rag: NeedRag = False,
    scope: Scope | None = None,
    k: int = 5,
) -> Answer:
    """Run the wiki→(maybe RAG) query flow for `query` (R-5.1, R-5.4).

    Args:
        wiki: The wiki engine (basic-memory adapter or Scout-DIY).
        rag: The RAG backend — consulted ONLY if the page does not suffice.
        query: The member's question.
        need_rag: Whether to descend to RAG. `False` (default) means the page
            suffices → **RAG is never called** (R-5.1). A predicate lets the
            caller (the agent, in production) decide from the page content.
        scope: Caller RBAC context passed through to the backend (R-4.8).
        k: Wiki top-K.

    Returns:
        An `Answer`. On the page-only branch, `used_rag` is False and `rag`
        is never awaited — the property the test pins (R-5.1).
    """
    hits = await wiki.wiki_search(query, k)
    if not hits:
        return Answer(query=query, status=AnswerStatus.NO_PAGE)

    top = hits[0]
    page = await wiki.wiki_read(top.path)

    descend = need_rag(page) if callable(need_rag) else need_rag
    if not descend:
        return Answer(
            query=query,
            status=AnswerStatus.PAGE_ONLY,
            page_path=top.path,
            page_summary=top.summary,
            used_rag=False,
        )

    addresses = _addresses_from_page(page)
    results = await rag_fetch_many(rag, addresses, scope=scope, k=k)
    context = tuple(piece for r in results for piece in r.context)
    citations = tuple(cite for r in results for cite in r.citations)
    status = (
        AnswerStatus.WITH_SOURCES
        if any(r.status is FetchStatus.OK for r in results)
        else AnswerStatus.NO_SOURCE
    )
    return Answer(
        query=query,
        status=status,
        page_path=top.path,
        page_summary=top.summary,
        used_rag=True,
        context=context,
        citations=citations,
    )
