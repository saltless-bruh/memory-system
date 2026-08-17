"""Tests for Nhịp B minting + the two-cadence separation (T-3.3, R-6.2/R-6.5)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest

from scout.sync_job import IndexOutcome, watch
from scout.types import Address, RagChunk, Scope
from scripts.mint import (
    MintStatus,
    format_source_block,
    main,
    mint_address,
)

_TARGET = "raw/reports/acme.pdf"


@dataclass
class HintMapBackend:
    """A `RagBackend` whose retrieval depends only on the hint — lets a test
    drive PASS/DRIFT/FAIL per candidate and see which hints were queried."""

    mapping: dict[str, list[RagChunk]]
    queried: list[str] = field(default_factory=list)

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        self.queried.append(hint)
        return self.mapping.get(hint, [])


def _chunk(file_path: str) -> RagChunk:
    return RagChunk(text="body", file_path=file_path, score=1.0)


def _pass() -> list[RagChunk]:
    return [_chunk(_TARGET)]


def _drift() -> list[RagChunk]:
    return [_chunk("raw/other.pdf")]  # retrieved, but wrong file


# ── mint_address ──────────────────────────────────────────────────────────
async def test_mints_first_passing_hint_and_short_circuits() -> None:
    backend = HintMapBackend({"good": _pass(), "also-good": _pass()})
    result = await mint_address(backend, _TARGET, ["good", "also-good"], loc="p.12")
    assert result.status is MintStatus.MINTED
    assert result.address == Address(path=_TARGET, hint="good", loc="p.12")
    assert backend.queried == ["good"]  # never tried the second — short-circuit


async def test_skips_fail_and_drift_then_mints() -> None:
    backend = HintMapBackend({"nothing": [], "ambiguous": _drift(), "precise": _pass()})
    result = await mint_address(backend, _TARGET, ["nothing", "ambiguous", "precise"])
    assert result.status is MintStatus.MINTED
    assert result.address is not None and result.address.hint == "precise"
    # the diagnostic trail records why the earlier ones were rejected
    assert [s.value for _, s in result.tried] == ["fail", "drift", "pass"]


async def test_no_hint_works_returns_none_with_trail() -> None:
    backend = HintMapBackend({"a": [], "b": _drift()})
    result = await mint_address(backend, _TARGET, ["a", "b"])
    assert result.status is MintStatus.NO_HINT_WORKS
    assert result.address is None
    assert [s.value for _, s in result.tried] == ["fail", "drift"]


async def test_minted_address_is_verify_pass_by_construction() -> None:
    """The whole point: mint reuses verify_address, so a minted hint PASSes."""
    from scripts.verify_addresses import VerifyStatus, verify_address

    backend = HintMapBackend({"h": _pass()})
    result = await mint_address(backend, _TARGET, ["h"])
    assert result.address is not None
    report = await verify_address(backend, result.address)
    assert report.status is VerifyStatus.PASS


# ── format_source_block ───────────────────────────────────────────────────
def test_format_source_block_with_loc() -> None:
    block = format_source_block(Address(path=_TARGET, hint="kerb", loc="p.9"))
    assert block == f"  - path: {_TARGET}\n    hint: kerb\n    loc: p.9\n"


def test_format_source_block_without_loc_omits_it() -> None:
    block = format_source_block(Address(path=_TARGET, hint="kerb"))
    assert "loc:" not in block
    assert block == f"  - path: {_TARGET}\n    hint: kerb\n"


# ── CLI ───────────────────────────────────────────────────────────────────
def test_main_refuses_without_backend() -> None:
    rc = main(["--path", _TARGET, "--hint", "x"])
    assert rc == 2  # never fabricates against nothing


def test_main_mints_with_backend(capsys: pytest.CaptureFixture[str]) -> None:
    backend = HintMapBackend({"good": _pass()})
    rc = main(
        ["--path", _TARGET, "--hint", "good", "--loc", "p.1"],
        backend_factory=lambda: backend,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "MINTED" in out and _TARGET in out


def test_main_returns_one_when_no_hint_works() -> None:
    backend = HintMapBackend({"bad": []})
    rc = main(["--path", _TARGET, "--hint", "bad"], backend_factory=lambda: backend)
    assert rc == 1


# ── R-6.2: the two cadences are separate — Nhịp A mints no pages ───────────
async def test_dropping_files_indexes_but_mints_no_pages() -> None:
    """Ten file drops -> ten reindexes, ZERO addresses/pages. Page addresses
    are born only from an explicit mint_address call (Nhịp B), never from
    auto-ingest (R-6.2)."""

    @dataclass
    class SpyIndexer:
        calls: int = 0

        async def index(self) -> IndexOutcome:
            self.calls += 1
            return IndexOutcome(ok=True, status="indexed")

    async def ten_batches() -> AsyncIterator[object]:  # 10 change batches
        for i in range(10):
            yield {("added", f"raw/f{i}.pdf")}

    spy = SpyIndexer()
    handled = await watch(spy, changes=ten_batches())
    assert handled == 10 and spy.calls == 10  # all indexed
    # nothing here minted an address: mint is a separate, explicit step.
