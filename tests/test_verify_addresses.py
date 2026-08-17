"""Tests for scripts.verify_addresses (T-2.5, R-6.5).

All classification/logic tests are seeded with `FakeRagBackend` and synthetic
`Address`/`vault.Page` objects — none of them touch the real `wiki/` vault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.backends.fake import FakeRagBackend
from scout.types import Address, RagChunk
from scripts.verify_addresses import (
    VerifyStatus,
    _collect_addresses,
    main,
    vault,
    verify_address,
    verify_all,
)


def _page(sources: list[object]) -> vault.Page:
    """Build a minimal synthetic vault page carrying only `sources[]`."""
    return vault.Page(
        path=Path("wiki/fake.md"), frontmatter={"sources": sources}, body=""
    )


# --------------------------------------------------------------------------
# verify_address — PASS / DRIFT / FAIL classification
# --------------------------------------------------------------------------


async def test_verify_address_pass_when_hint_retrieves_the_right_file() -> None:
    backend = FakeRagBackend(
        chunks=[
            RagChunk(
                text="kerberoasting service account",
                file_path="raw/reports/acme.pdf",
                score=0.9,
            ),
            RagChunk(text="unrelated content", file_path="raw/other.md", score=0.1),
        ]
    )
    addr = Address(path="raw/reports/acme.pdf", hint="kerberoasting service account")
    report = await verify_address(backend, addr)

    assert report.status is VerifyStatus.PASS
    assert report.path == "raw/reports/acme.pdf"
    assert report.hint == addr.hint
    assert "raw/reports/acme.pdf" in report.matched_files


async def test_verify_address_drift_when_hint_pulls_only_other_files() -> None:
    """Hint pulls chunks, but none from the addressed file — DRIFT."""
    backend = FakeRagBackend(
        chunks=[
            RagChunk(
                text="kerberoasting service account",
                file_path="raw/other.md",
                score=0.9,
            )
        ]
    )
    addr = Address(path="raw/reports/acme.pdf", hint="kerberoasting service account")
    report = await verify_address(backend, addr)

    assert report.status is VerifyStatus.DRIFT
    assert report.matched_files == ("raw/other.md",)


async def test_verify_address_fail_when_nothing_retrieved() -> None:
    """Empty corpus -> nothing retrieved at all -> FAIL."""
    backend = FakeRagBackend(chunks=[])
    addr = Address(path="raw/reports/acme.pdf", hint="anything at all")
    report = await verify_address(backend, addr)

    assert report.status is VerifyStatus.FAIL
    assert report.matched_files == ()


async def test_verify_address_matches_across_separator_styles() -> None:
    """Path matching goes through scout.core.normalize_path/post_filter,
    consistent with how rag_fetch decides "on-source" (R-4.3)."""
    backend = FakeRagBackend(
        chunks=[
            RagChunk(
                text="acme report body",
                file_path="raw\\reports\\acme.pdf",
                score=1.0,
            ),
        ]
    )
    addr = Address(path="raw/reports/acme.pdf", hint="acme report body")
    report = await verify_address(backend, addr)

    assert report.status is VerifyStatus.PASS
    assert report.matched_files == ("raw/reports/acme.pdf",)


async def test_verify_address_matched_files_is_sorted_and_deduplicated() -> None:
    backend = FakeRagBackend(
        chunks=[
            RagChunk(text="alpha beta", file_path="raw/z.md", score=0.5),
            RagChunk(text="alpha beta", file_path="raw/a.md", score=0.4),
            RagChunk(text="alpha beta again", file_path="raw/a.md", score=0.3),
        ]
    )
    addr = Address(path="raw/missing.md", hint="alpha beta")
    report = await verify_address(backend, addr)

    assert report.status is VerifyStatus.DRIFT
    assert report.matched_files == ("raw/a.md", "raw/z.md")


# --------------------------------------------------------------------------
# verify_all — aggregation
# --------------------------------------------------------------------------


async def test_verify_all_preserves_order_and_classifies_each() -> None:
    backend = FakeRagBackend(
        chunks=[
            RagChunk(text="kerberoasting", file_path="raw/reports/acme.pdf", score=0.9),
            RagChunk(text="esc8 relay", file_path="raw/advisories/adcs.md", score=0.8),
        ]
    )
    addresses = [
        Address(path="raw/reports/acme.pdf", hint="kerberoasting"),  # PASS
        Address(path="raw/missing.pdf", hint="esc8 relay"),  # DRIFT
    ]
    reports = await verify_all(backend, addresses)

    assert [r.status for r in reports] == [VerifyStatus.PASS, VerifyStatus.DRIFT]
    assert [r.path for r in reports] == [a.path for a in addresses]


async def test_verify_all_empty_backend_is_all_fail() -> None:
    backend = FakeRagBackend(chunks=[])
    addresses = [Address(path="raw/a.md", hint="x"), Address(path="raw/b.md", hint="y")]
    reports = await verify_all(backend, addresses)

    assert all(r.status is VerifyStatus.FAIL for r in reports)


async def test_verify_all_empty_addresses_returns_empty_list() -> None:
    backend = FakeRagBackend(chunks=[])
    assert await verify_all(backend, []) == []


# --------------------------------------------------------------------------
# _collect_addresses — vault plumbing (synthetic pages only)
# --------------------------------------------------------------------------


def test_collect_addresses_flattens_pages_sources() -> None:
    pages = [
        _page([{"path": "raw/a.md", "hint": "hint a", "loc": "p.1"}]),
        _page(
            [
                {"path": "raw/b.md", "hint": "hint b"},
                {"path": "raw/c.md", "hint": "hint c", "loc": None},
            ]
        ),
    ]
    addresses = _collect_addresses(pages)

    assert [a.path for a in addresses] == ["raw/a.md", "raw/b.md", "raw/c.md"]
    assert addresses[0].hint == "hint a"
    assert addresses[0].loc == "p.1"
    assert addresses[1].loc is None
    assert addresses[2].loc is None


def test_collect_addresses_skips_malformed_entries() -> None:
    pages = [
        _page(
            [
                {"path": "raw/ok.md", "hint": "ok"},
                {"path": "", "hint": "empty path is falsy"},
                {"hint": "missing path key"},
                {"path": "raw/no-hint.md"},
                "not-a-mapping",
            ]
        )
    ]
    addresses = _collect_addresses(pages)

    assert [a.path for a in addresses] == ["raw/ok.md"]


def test_collect_addresses_empty_when_no_pages() -> None:
    assert _collect_addresses([]) == []


def test_collect_addresses_empty_when_page_has_no_sources() -> None:
    assert _collect_addresses([_page([])]) == []


# --------------------------------------------------------------------------
# main() — CLI wiring + nonzero-exit logic
# --------------------------------------------------------------------------


def test_main_returns_2_and_warns_when_no_backend_configured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main()
    out = capsys.readouterr().out

    assert rc == 2
    assert "No RAG backend" in out


def test_main_returns_0_when_all_pass(capsys: pytest.CaptureFixture[str]) -> None:
    backend = FakeRagBackend(
        chunks=[RagChunk(text="ok body", file_path="raw/a.md", score=1.0)]
    )
    pages = [_page([{"path": "raw/a.md", "hint": "ok body"}])]

    rc = main(backend_factory=lambda: backend, pages_loader=lambda: pages)
    out = capsys.readouterr().out

    assert rc == 0
    assert "PASS" in out
    assert "1 address(es) checked" in out


def test_main_returns_1_when_any_fail_or_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = FakeRagBackend(chunks=[])
    pages = [_page([{"path": "raw/a.md", "hint": "x"}])]

    rc = main(backend_factory=lambda: backend, pages_loader=lambda: pages)
    out = capsys.readouterr().out

    assert rc == 1
    assert "FAIL" in out


def test_main_returns_1_when_drift(capsys: pytest.CaptureFixture[str]) -> None:
    backend = FakeRagBackend(
        chunks=[RagChunk(text="wrong file body", file_path="raw/other.md", score=1.0)]
    )
    pages = [_page([{"path": "raw/a.md", "hint": "wrong file body"}])]

    rc = main(backend_factory=lambda: backend, pages_loader=lambda: pages)
    out = capsys.readouterr().out

    assert rc == 1
    assert "DRIFT" in out
    assert "retrieved from: raw/other.md" in out


def test_main_returns_0_when_no_addresses_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = FakeRagBackend(chunks=[])

    rc = main(backend_factory=lambda: backend, pages_loader=lambda: [])
    out = capsys.readouterr().out

    assert rc == 0
    assert "No sources" in out


def test_main_returns_2_on_vault_load_error(capsys: pytest.CaptureFixture[str]) -> None:
    backend = FakeRagBackend(chunks=[])

    def boom() -> list[vault.Page]:
        raise ValueError("bad frontmatter")

    rc = main(backend_factory=lambda: backend, pages_loader=boom)
    out = capsys.readouterr().out

    assert rc == 2
    assert "VAULT ERROR" in out
