"""Tests for scout.core — post-filter, no_source, citations, multi-source."""

from __future__ import annotations

import pytest

from scout.backends.fake import FakeRagBackend
from scout.core import (
    normalize_path,
    post_filter,
    rag_fetch,
    rag_fetch_many,
)
from scout.types import Address, FetchStatus, RagChunk, Scope


@pytest.mark.parametrize(
    "a,b",
    [
        ("raw/reports/acme.pdf", "raw\\reports\\acme.pdf"),
        (" raw/reports/acme.pdf ", "raw/reports/acme.pdf"),
        ("raw/./reports/acme.pdf", "raw/reports/acme.pdf"),
    ],
)
def test_normalize_path_equivalences(a: str, b: str) -> None:
    assert normalize_path(a) == normalize_path(b)


def test_post_filter_keeps_only_matching_and_sorts(chunks: list[RagChunk]) -> None:
    kept = post_filter(chunks, "raw/reports/acme.pdf")
    assert [c.file_path for c in kept] == [
        "raw/reports/acme.pdf",
        "raw/reports/acme.pdf",
    ]
    # sorted by descending score
    assert [c.score for c in kept] == [0.9, 0.5]


def test_post_filter_matches_across_separator_styles() -> None:
    chunk = RagChunk(text="x", file_path="raw\\reports\\acme.pdf", score=1.0)
    assert post_filter([chunk], "raw/reports/acme.pdf") == [chunk]


async def test_rag_fetch_ok_returns_quotes_and_citations(
    backend: FakeRagBackend,
) -> None:
    addr = Address(path="raw/reports/acme.pdf", hint="kerberoasting service account")
    result = await rag_fetch(backend, addr)

    assert result.status is FetchStatus.OK and result.ok
    # only chunks from the addressed file, no leakage of the adcs.md chunk
    assert {c.file_path for c in result.context} == {"raw/reports/acme.pdf"}
    assert all(cite.file_path == "raw/reports/acme.pdf" for cite in result.citations)
    assert len(result.context) == len(result.citations) == 2


async def test_rag_fetch_no_source_when_hint_matches_other_file(
    backend: FakeRagBackend,
) -> None:
    """The hint retrieves the adcs chunk best, but the address points at a file
    with no matching chunk → no_source, never fabricated (R-4.5)."""
    addr = Address(path="raw/nonexistent.pdf", hint="ESC8 NTLM relay")
    result = await rag_fetch(backend, addr)
    assert result.status is FetchStatus.NO_SOURCE
    assert result.context == () and result.citations == ()


async def test_rag_fetch_no_source_on_empty_backend() -> None:
    addr = Address(path="raw/a.pdf", hint="anything")
    result = await rag_fetch(FakeRagBackend(chunks=[]), addr)
    assert result.status is FetchStatus.NO_SOURCE


async def test_rag_fetch_loc_falls_back_to_address_loc() -> None:
    chunk = RagChunk(text="body", file_path="raw/a.pdf", score=1.0)  # no loc
    backend = FakeRagBackend(chunks=[chunk])
    addr = Address(path="raw/a.pdf", hint="body", loc="p.99")
    result = await rag_fetch(backend, addr)
    assert result.context[0].loc == "p.99"
    assert result.citations[0].loc == "p.99"


async def test_rag_fetch_prefers_chunk_loc_over_address_loc(
    backend: FakeRagBackend,
) -> None:
    addr = Address(path="raw/reports/acme.pdf", hint="kerberoasting", loc="p.999")
    result = await rag_fetch(backend, addr)
    # top chunk has its own loc "p.12" which wins over the address loc
    assert result.context[0].loc == "p.12"


async def test_rag_fetch_threads_scope_to_backend(backend: FakeRagBackend) -> None:
    """RBAC hook (R-4.8): scope must reach the backend even though V1 ignores it."""
    scope = Scope(roles=frozenset({"redteam"}), team="redteam")
    addr = Address(path="raw/reports/acme.pdf", hint="kerberoasting")
    await rag_fetch(backend, addr, scope=scope)
    assert backend.record_scope == scope


async def test_rag_fetch_many_groups_per_address(backend: FakeRagBackend) -> None:
    """A two-source page yields two results, each cited to its own file (R-4.7)."""
    addresses = [
        Address(path="raw/reports/acme.pdf", hint="kerberoasting"),
        Address(path="raw/advisories/adcs.md", hint="ESC8 relay"),
    ]
    results = await rag_fetch_many(backend, addresses)

    assert len(results) == 2
    assert results[0].ok and results[1].ok
    assert {c.file_path for c in results[0].context} == {"raw/reports/acme.pdf"}
    assert {c.file_path for c in results[1].context} == {"raw/advisories/adcs.md"}


async def test_rag_fetch_many_preserves_order_and_no_source() -> None:
    backend = FakeRagBackend(
        chunks=[RagChunk(text="only", file_path="raw/a.pdf", score=1.0)]
    )
    results = await rag_fetch_many(
        backend,
        [
            Address(path="raw/a.pdf", hint="only"),
            Address(path="raw/missing.pdf", hint="only"),
        ],
    )
    assert results[0].ok
    assert results[1].status is FetchStatus.NO_SOURCE
