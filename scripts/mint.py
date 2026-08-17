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
that **provably** retrieves the file. Crucially it reuses `verify_address`
(T-2.5) — the exact classifier the merge gate runs — so a minted address is
verify-PASS **by construction**, not by a parallel heuristic that could drift
from it.

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
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.types import Address, RagBackend  # noqa: E402
from scripts.verify_addresses import (  # noqa: E402
    VerifyStatus,
    verify_address,
)


class MintStatus(StrEnum):
    """Outcome of a mint attempt across candidate hints."""

    MINTED = "minted"
    NO_HINT_WORKS = "no_hint_works"


@dataclass(frozen=True, slots=True)
class MintResult:
    """The result of minting an address for one target file.

    Attributes:
        path: The ``raw/`` file the address should point at.
        address: A verify-PASS `Address` ready to drop into a page's
            ``sources[]`` — or ``None`` when no candidate hint retrieved the
            file (`status` is then ``NO_HINT_WORKS``).
        status: ``MINTED`` if a working hint was found, else ``NO_HINT_WORKS``.
        tried: Every candidate hint paired with its `VerifyStatus`, in order —
            the diagnostic. ``FAIL`` means the hint retrieved nothing (the file
            may not be indexed / covered yet); ``DRIFT`` means it pulled a
            *different* file (the phrase is ambiguous — narrow it).
    """

    path: str
    address: Address | None
    status: MintStatus
    tried: tuple[tuple[str, VerifyStatus], ...]


async def mint_address(
    backend: RagBackend,
    path: str,
    candidate_hints: Sequence[str],
    *,
    loc: str | None = None,
) -> MintResult:
    """Return the first candidate hint that provably retrieves `path` (R-6.5).

    Each candidate is classified with `verify_address` — the same PASS/DRIFT/
    FAIL logic the merge gate runs — so a ``MINTED`` address is guaranteed to
    pass verification later. Short-circuits on the first PASS.

    Args:
        backend: A live `RagBackend` to query (RAG-Anything today; any engine
            behind the same interface, R-4.8).
        path: The ``raw/`` file the minted address must point at.
        candidate_hints: Phrasings to try, in preference order.
        loc: Optional human locator (e.g. ``p.12``) copied onto the address.

    Returns:
        A `MintResult`. ``address`` is set only when a hint PASSes.
    """
    tried: list[tuple[str, VerifyStatus]] = []
    for hint in candidate_hints:
        report = await verify_address(backend, Address(path=path, hint=hint, loc=loc))
        tried.append((hint, report.status))
        if report.status is VerifyStatus.PASS:
            return MintResult(
                path=path,
                address=Address(path=path, hint=hint, loc=loc),
                status=MintStatus.MINTED,
                tried=tuple(tried),
            )
    return MintResult(
        path=path, address=None, status=MintStatus.NO_HINT_WORKS, tried=tuple(tried)
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
    for hint, status in result.tried:
        print(f"  {status.value.upper():5s}  {hint!r}")
    if result.status is MintStatus.MINTED and result.address is not None:
        print("\nMINTED — paste into the page's `sources:` block:\n")
        print(format_source_block(result.address))
    else:
        print(
            "\nNO WORKING HINT — none of the candidates retrieved "
            f"{result.path!r}.\n  · FAIL = nothing retrieved (is the file "
            "indexed? drop it in raw/ so Nhịp A ingests it).\n  · DRIFT = a "
            "different file was retrieved (the phrase is ambiguous — narrow it)."
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
    ap.add_argument("--loc", default=None, help="optional locator, e.g. p.12")
    args = ap.parse_args(argv)

    backend = backend_factory()
    if backend is None:
        print(
            "No RAG backend configured — mint has nothing to query. Wire a "
            "live backend (RagAnythingHttpBackend) via backend_factory."
        )
        return 2

    result = asyncio.run(mint_address(backend, args.path, args.hints, loc=args.loc))
    _print_result(result)
    return 0 if result.status is MintStatus.MINTED else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(backend_factory=_default_backend_factory))
