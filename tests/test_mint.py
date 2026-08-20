"""Tests for Nhịp B minting + the two-cadence separation (T-3.3, R-6.2/R-6.5)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest

from scout.core import post_filter
from scout.sync_job import IndexOutcome, watch
from scout.types import Address, RagChunk, Scope
from scripts.mint import (
    CandidateOutcome,
    MintStatus,
    format_source_block,
    main,
    mint_address,
)

_TARGET = "raw/reports/acme.pdf"
_LOC = "p.12"


@dataclass
class HintMapBackend:
    """A `RagBackend` whose retrieval depends only on the hint — lets a test
    drive PASS/DRIFT/FAIL per candidate and see which hints were queried.

    Like the production backend it honours `path` pre-filtering, so a hint that
    pulls only other files reports an empty addressed source rather than
    silently leaking the wrong one back.
    """

    mapping: dict[str, list[RagChunk]]
    queried: list[str] = field(default_factory=list)
    scopes: list[Scope | None] = field(default_factory=list)
    closed: bool = False

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        self.queried.append(hint)
        self.scopes.append(scope)
        chunks = self.mapping.get(hint, [])
        if path is not None:
            chunks = post_filter(chunks, path)
        return chunks[:k]

    async def close(self) -> None:
        self.closed = True


def _chunk(file_path: str, text: str, *, score: float = 1.0, loc: str = _LOC) -> RagChunk:
    return RagChunk(text=text, file_path=file_path, score=score, loc=loc)


def _pass(hint: str = "good also-good precise kerberoasting") -> list[RagChunk]:
    """The addressed file wins rank 1 and its text carries the hint's words."""
    return [_chunk(_TARGET, hint)]


def _drift(hint: str = "ambiguous") -> list[RagChunk]:
    """Retrieved, but another file outranks the addressed one."""
    return [_chunk("raw/other.pdf", hint, score=1.0), _chunk(_TARGET, hint, score=0.1)]


# ── mint_address ──────────────────────────────────────────────────────────
async def test_mints_first_passing_hint_and_short_circuits() -> None:
    backend = HintMapBackend({"good": _pass(), "also-good": _pass()})
    result = await mint_address(
        backend, _TARGET, ["good", "also-good"], department="redteam", loc=_LOC
    )
    assert result.status is MintStatus.MINTED
    assert result.address == Address(path=_TARGET, hint="good", loc=_LOC)
    assert backend.queried == ["good"]  # never tried the second — short-circuit
    assert backend.scopes == [Scope(departments=frozenset({"redteam"}))]


async def test_skips_fail_and_drift_then_mints() -> None:
    backend = HintMapBackend({"nothing": [], "ambiguous": _drift(), "precise": _pass()})
    result = await mint_address(
        backend,
        _TARGET,
        ["nothing", "ambiguous", "precise"],
        department="infra",
        loc=_LOC,
    )
    assert result.status is MintStatus.MINTED
    assert result.address is not None and result.address.hint == "precise"
    # the diagnostic trail records why the earlier ones were rejected
    assert [s.value for _, s in result.tried] == ["fail", "drift", "pass"]


async def test_no_hint_works_returns_none_with_trail() -> None:
    backend = HintMapBackend({"a": [], "b": _drift()})
    result = await mint_address(
        backend, _TARGET, ["a", "b"], department="ai_eng", loc=_LOC
    )
    assert result.status is MintStatus.NO_HINT_WORKS
    assert result.address is None
    assert [s.value for _, s in result.tried] == ["fail", "drift"]


async def test_minted_address_is_verify_pass_by_construction() -> None:
    """The whole point: mint reuses verify_address, so a minted hint PASSes."""
    backend = HintMapBackend({"kerberoasting": _pass()})
    result = await mint_address(
        backend, _TARGET, ["kerberoasting"], department="blueteam", loc=_LOC
    )
    assert result.address is not None
    assert result.tried == (("kerberoasting", CandidateOutcome.PASS),)
    assert backend.scopes == [Scope(departments=frozenset({"blueteam"}))]


async def test_mint_reuses_the_merge_gate_criterion_not_a_parallel_heuristic() -> None:
    """B1: an ungrounded hint that still wins rank 1 must not mint.

    If minting ever grew its own looser check, this is the case that would
    diverge — the addressed file is the only thing retrieved, so pure
    membership (and pure rank) both say yes.
    """
    backend = HintMapBackend(
        {"zzqq banana marmalade": [_chunk(_TARGET, "entirely unrelated prose")]}
    )
    result = await mint_address(
        backend, _TARGET, ["zzqq banana marmalade"], department="infra", loc=_LOC
    )
    assert result.status is MintStatus.NO_HINT_WORKS
    assert result.tried == (("zzqq banana marmalade", CandidateOutcome.DRIFT),)


# ── m2: the declared locator must be one the source actually carries ───────
async def test_mint_refuses_a_locator_the_source_does_not_carry() -> None:
    backend = HintMapBackend({"good": [_chunk(_TARGET, "good", loc="Rows 1-4")]})
    result = await mint_address(
        backend, _TARGET, ["good"], department="infra", loc="Rows 1-10"
    )
    assert result.status is MintStatus.NO_HINT_WORKS
    assert result.address is None
    assert result.tried == (("good", CandidateOutcome.LOC_MISMATCH),)
    assert result.available_locs == ("Rows 1-4",)


async def test_mint_accepts_a_section_locator_split_across_chunks() -> None:
    """The chunker's ``(i/n)`` marker still satisfies a section locator (m1)."""
    backend = HintMapBackend(
        {"good": [_chunk(_TARGET, "good", loc="Section System Architecture Overview (2/2)")]}
    )
    result = await mint_address(
        backend,
        _TARGET,
        ["good"],
        department="infra",
        loc="Section System Architecture Overview",
    )
    assert result.status is MintStatus.MINTED
    assert result.tried == (("good", CandidateOutcome.PASS),)


async def test_mint_keeps_trying_candidates_after_a_locator_mismatch() -> None:
    backend = HintMapBackend(
        {
            "stale": [_chunk(_TARGET, "stale", loc="p.99")],
            "fresh": [_chunk(_TARGET, "fresh", loc=_LOC)],
        }
    )
    result = await mint_address(
        backend, _TARGET, ["stale", "fresh"], department="infra", loc=_LOC
    )
    assert result.status is MintStatus.MINTED
    assert result.address is not None and result.address.hint == "fresh"
    assert [s.value for _, s in result.tried] == ["loc_mismatch", "pass"]
    assert set(result.available_locs) == {"p.99", _LOC}


@pytest.mark.parametrize("department", ["", "all", "unknown"])
async def test_mint_rejects_invalid_department_before_backend(department: str) -> None:
    backend = HintMapBackend({"kerberoasting": _pass()})
    with pytest.raises(ValueError):
        await mint_address(
            backend, _TARGET, ["kerberoasting"], department=department, loc=_LOC
        )
    assert backend.queried == []


async def test_mint_requires_nonempty_locator_before_backend() -> None:
    backend = HintMapBackend({"kerberoasting": _pass()})
    with pytest.raises(ValueError, match="locator"):
        await mint_address(
            backend, _TARGET, ["kerberoasting"], department="infra", loc=""
        )
    assert backend.queried == []


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
    rc = main(
        ["--path", _TARGET, "--hint", "x", "--department", "infra", "--loc", "p.1"]
    )
    assert rc == 2  # never fabricates against nothing


def test_main_mints_with_backend(capsys: pytest.CaptureFixture[str]) -> None:
    backend = HintMapBackend({"good": _pass()})
    rc = main(
        [
            "--path",
            _TARGET,
            "--hint",
            "good",
            "--department",
            "redteam",
            "--loc",
            _LOC,
        ],
        backend_factory=lambda: backend,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "MINTED" in out and _TARGET in out
    assert backend.closed


def test_main_returns_one_when_no_hint_works() -> None:
    backend = HintMapBackend({"bad": []})
    rc = main(
        [
            "--path",
            _TARGET,
            "--hint",
            "bad",
            "--department",
            "infra",
            "--loc",
            _LOC,
        ],
        backend_factory=lambda: backend,
    )
    assert rc == 1
    assert backend.closed


def test_main_redacts_backend_factory_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "do-not-print-database-password"

    def fail() -> HintMapBackend:
        raise RuntimeError(secret)

    rc = main(
        [
            "--path",
            _TARGET,
            "--hint",
            "bad",
            "--department",
            "infra",
            "--loc",
            "p.1",
        ],
        backend_factory=fail,
    )
    assert rc == 2
    assert secret not in capsys.readouterr().out


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
