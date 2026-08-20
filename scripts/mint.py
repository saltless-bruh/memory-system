#!/usr/bin/env python3
"""mint.py — Nhịp B compile-on-demand: mint a verify-PASS address (T-3.3).

The two ingest cadences (design.md §5) are deliberately asymmetric:

  * **Nhịp A** (`scout/sync_job.py`) auto-indexes every file dropped into
    ``raw/`` into RAG. It creates **no** wiki pages — dropping ten files does
    not mint ten pages (R-6.2).
  * **Nhịp B** (this module + `propose_page.py` + `verify_addresses.py`) is
    the *manual*, per-page compile: a human decides a topic is worth a page,
    writes it, and PRs it. This is where a page's ``sources[]`` addresses are
    born.

The fragile part of authoring a source is the **hint**: a ``sources[]`` entry
must carry a phrase that actually retrieves its file from RAG's merged KG, or
it will fail verification at merge time (DRIFT/FAIL, R-6.5). Minting closes
that loop *before* the page is written: given the target file and a few
candidate phrasings, `mint_address` queries RAG and returns the first hint
that **provably** retrieves the file — meaning the file wins rank
`TOP_RANK` of everything the page's department may see, *and* the phrase is
lexically grounded in that file's own text. Crucially it reuses
`verify_address` (T-2.5) — the exact classifier the merge gate runs — so a
minted address is verify-PASS **by construction**, not by a parallel
heuristic that could drift from it. Minting adds exactly one extra rule of
its own: the declared `--loc` must be a locator the file actually carries
(m2), so the frontmatter never advertises a locator retrieval cannot back.

The compile flow, end to end:

    1. mint      python scripts/mint.py --path raw/... --hint "..." --hint "..."
    2. write     put the minted sources[] block in the page's frontmatter
    3. PR        python scripts/propose_page.py --title "..."   (never main; R-6.4)
    4. merge     a human reviews & merges
    5. finalize  python scripts/gen_index.py && verify_addresses.py   (R-6.5)

Like `verify_addresses.py`, this never fabricates against nothing: the CLI
refuses to run without a live RAG backend wired in.

Usage:
    python scripts/mint.py --path raw/reports/acme.pdf \\
        --hint "kerberoasting svc-sql" --hint "SPN offline crack" --loc p.12
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.policy import validate_caller_departments  # noqa: E402
from scout.types import Address, RagBackend, ScopedAddress  # noqa: E402
from scripts.verify_addresses import (  # noqa: E402
    GROUNDING_MIN_COVERAGE,
    TOP_RANK,
    VerifyStatus,
    loc_is_consistent,
    verify_address,
)

__all__ = [
    "GROUNDING_MIN_COVERAGE",
    "TOP_RANK",
    "CandidateOutcome",
    "MintResult",
    "MintStatus",
    "format_source_block",
    "main",
    "mint_address",
]


class MintStatus(StrEnum):
    """Outcome of a mint attempt across candidate hints."""

    MINTED = "minted"
    NO_HINT_WORKS = "no_hint_works"


class CandidateOutcome(StrEnum):
    """Why one candidate hint was accepted or rejected.

    The first three mirror `VerifyStatus` exactly — minting delegates the
    PASS/DRIFT/FAIL decision to the single `verify_address` implementation the
    merge gate runs, so there is no second heuristic that could drift from it.
    `LOC_MISMATCH` is the one extra rejection minting adds on top (m2): the
    hint retrieves the right file, but the locator the author declared is not
    one the retrieval actually returned.
    """

    PASS = "pass"
    FAIL = "fail"
    DRIFT = "drift"
    LOC_MISMATCH = "loc_mismatch"


@dataclass(frozen=True, slots=True)
class MintResult:
    """The result of minting an address for one target file.

    Attributes:
        path: The ``raw/`` file the address should point at.
        address: A verify-PASS `Address` ready to drop into a page's
            ``sources[]`` — or ``None`` when no candidate hint retrieved the
            file (`status` is then ``NO_HINT_WORKS``).
        status: ``MINTED`` if a working hint was found, else ``NO_HINT_WORKS``.
        tried: Every candidate hint paired with its `CandidateOutcome`, in
            order — the diagnostic. ``FAIL`` means the addressed file returned
            nothing (is it indexed?); ``DRIFT`` means another file outranked it
            or the phrase is not grounded in its text (narrow it);
            ``LOC_MISMATCH`` means the hint is fine but ``--loc`` names a
            locator the file does not carry.
        available_locs: Every locator the addressed file actually returned
            while trying the candidates — what to choose ``--loc`` from.
    """

    path: str
    department: str
    address: Address | None
    status: MintStatus
    tried: tuple[tuple[str, CandidateOutcome], ...]
    available_locs: tuple[str, ...] = ()


async def mint_address(
    backend: RagBackend,
    path: str,
    candidate_hints: Sequence[str],
    *,
    department: str,
    loc: str,
) -> MintResult:
    """Return the first candidate hint that provably retrieves `path` (R-6.5).

    Each candidate is classified with `verify_address` — the same rank +
    grounding logic the merge gate runs, imported rather than reimplemented —
    so a ``MINTED`` address is verify-PASS by construction. Short-circuits on
    the first candidate that clears both that gate and the locator check.

    Minting is deliberately **stricter** than verification by exactly one rule
    (m2): the declared `loc` must name a locator the retrieval actually
    returned. Verification certifies *retrievability* at merge time and does
    not re-litigate the locator, because a locator that has gone stale is a
    content decision for a human, not something a merge gate should block or an
    auto-healer should silently rewrite. Authoring, by contrast, is exactly the
    moment to refuse to write a locator that is a fiction.

    Args:
        backend: A live `RagBackend` to query. Production uses pgvector; tests
            may inject an offline implementation of the same interface.
        path: The ``raw/`` file the minted address must point at.
        candidate_hints: Phrasings to try, in preference order.
        department: Canonical department of the page that will declare it.
        loc: Required human locator (e.g. ``p.12``); validated against the
            locators the addressed file actually returns.

    Returns:
        A `MintResult`. ``address`` is set only when a hint PASSes verification
        *and* the declared locator checks out.
    """
    canonical_department = next(iter(validate_caller_departments([department])))
    if not loc.strip():
        raise ValueError("minting requires a nonempty locator")
    tried: list[tuple[str, CandidateOutcome]] = []
    seen_locs: dict[str, None] = {}
    for source_index, hint in enumerate(candidate_hints):
        address = Address(path=path, hint=hint, loc=loc)
        report = await verify_address(
            backend,
            ScopedAddress(
                page_path="<mint>",
                page_slug="mint",
                source_index=source_index,
                department=canonical_department,
                address=address,
            ),
        )
        seen_locs.update(dict.fromkeys(report.matched_locs))
        if report.status is not VerifyStatus.PASS:
            tried.append((hint, CandidateOutcome(report.status.value)))
            continue
        if not loc_is_consistent(loc, report.matched_locs):
            tried.append((hint, CandidateOutcome.LOC_MISMATCH))
            continue
        tried.append((hint, CandidateOutcome.PASS))
        return MintResult(
            path=path,
            department=canonical_department,
            address=Address(path=path, hint=hint, loc=loc),
            status=MintStatus.MINTED,
            tried=tuple(tried),
            available_locs=tuple(seen_locs),
        )
    return MintResult(
        path=path,
        department=canonical_department,
        address=None,
        status=MintStatus.NO_HINT_WORKS,
        tried=tuple(tried),
        available_locs=tuple(seen_locs),
    )


def format_source_block(address: Address) -> str:
    """Render a minted `Address` as a ``sources[]`` YAML entry to paste.

    Args:
        address: A verify-PASS address (typically ``MintResult.address``).

    Returns:
        A single YAML list-item block for the page's frontmatter ``sources:``.
    """
    lines = [f"  - path: {address.path}", f"    hint: {address.hint}"]
    if address.loc:
        lines.append(f"    loc: {address.loc}")
    return "\n".join(lines) + "\n"


BackendFactory = Callable[[], RagBackend | None]


def _no_backend_configured() -> RagBackend | None:
    """Default factory — no live RAG backend is wired for a bare CLI run."""
    return None


def _print_result(result: MintResult) -> None:
    """Render a mint result (and the paste-ready block on success) to stdout."""
    for hint, outcome in result.tried:
        print(f"  {outcome.value.upper():13s}  {hint!r}")
    if result.available_locs:
        print(f"\n  locators carried by {result.path}: "
              f"{', '.join(repr(loc) for loc in result.available_locs)}")
    if result.status is MintStatus.MINTED and result.address is not None:
        print("\nMINTED — paste into the page's `sources:` block:\n")
        print(format_source_block(result.address))
    else:
        print(
            f"\nNO WORKING HINT — no candidate minted an address for "
            f"{result.path!r}.\n"
            "  · FAIL = the addressed file returned nothing (is it indexed? "
            "drop it in raw/ so Nhịp A ingests it).\n"
            f"  · DRIFT = another file outranked it (rank {TOP_RANK} is "
            "required), or the phrase is not grounded in the file's own text "
            f"({GROUNDING_MIN_COVERAGE:.0%} of its content words must appear "
            "there) — narrow it, and take the vocabulary from the source.\n"
            "  · LOC_MISMATCH = the hint works but --loc names a locator the "
            "file does not carry; use one of the locators reported above."
        )


def _default_backend_factory() -> RagBackend | None:
    try:
        from scout.backends.pgvector import PgVectorRlsBackend

        return PgVectorRlsBackend()
    except Exception:
        return None


def main(
    argv: Sequence[str] | None = None,
    *,
    backend_factory: BackendFactory = _no_backend_configured,
) -> int:
    """CLI: mint a verify-PASS ``sources[]`` address for one file.

    Kept thin — all logic is in `mint_address`. Refuses to run without a live
    backend rather than fabricate an address (same contract as
    `verify_addresses.py`).

    Args:
        argv: Command-line arguments (defaults to ``sys.argv``).
        backend_factory: Builds the `RagBackend` to query; the default returns
            ``None`` (no backend wired) and the CLI exits nonzero.

    Returns:
        0 if an address was minted, 1 if no hint worked, 2 on a setup error
        (no backend configured).
    """
    ap = argparse.ArgumentParser(
        description="Mint a verify-PASS sources[] address (Nhịp B, T-3.3).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--path", required=True, help="target raw/ file path")
    ap.add_argument(
        "--hint",
        action="append",
        default=[],
        dest="hints",
        required=True,
        help="candidate hint phrase (repeat to try several, in order)",
    )
    ap.add_argument("--department", required=True, help="canonical page department")
    ap.add_argument(
        "--loc",
        required=True,
        help="required locator, e.g. p.12 — must be one the file actually "
        "carries; a mismatch is reported as LOC_MISMATCH with the real ones",
    )
    args = ap.parse_args(argv)

    try:
        backend = backend_factory()
    except Exception:  # noqa: BLE001 - configuration may contain credentials
        print("INFRASTRUCTURE ERROR: mint backend configuration failed.")
        return 2
    if backend is None:
        print(
            "No RAG backend configured — mint has nothing to query. Wire a "
            "live backend via backend_factory."
        )
        return 2

    async def run_and_close() -> MintResult:
        try:
            return await mint_address(
                backend,
                args.path,
                args.hints,
                department=args.department,
                loc=args.loc,
            )
        finally:
            close = getattr(backend, "close", None)
            if close is not None:
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result

    try:
        result = asyncio.run(run_and_close())
    except Exception:  # noqa: BLE001 - backend/config details may contain secrets
        print("INFRASTRUCTURE ERROR: minting could not complete.")
        return 2
    _print_result(result)
    return 0 if result.status is MintStatus.MINTED else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(backend_factory=_default_backend_factory))
