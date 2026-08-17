#!/usr/bin/env python3
"""verify_addresses.py — RAG-layer address verification (T-2.5 → R-6.5).

For every `sources[]` entry across the vault, this asks a live RAG backend
whether the `hint` actually retrieves its addressed file. This is a
**different layer** from `gen_index.py`, which only checks that
`sources[].path` exists on disk (design.md §5): a path can exist on disk yet
the hint can still pull the wrong chunk, or nothing at all, out of the RAG
index. Classification, per address:

    PASS  — >=1 retrieved chunk's `file_path` == `address.path` (hint works).
    DRIFT — chunks were retrieved, but none from `address.path` (hint pulls
            a different file — a false-positive retrieval).
    FAIL  — nothing was retrieved at all (hint matches nothing in the KG).

Path matching reuses `scout.core.normalize_path`/`post_filter` so this
agrees with `rag_fetch`'s own notion of "on-source" (R-4.3) — the same
address that would make `rag_fetch` return real context here reports PASS.

The real RAG-Anything backend is wired in T-2.1; until then there is nothing
for this CLI to query. It never fabricates a report: `main()` accepts a
backend via `backend_factory` and, when none is configured, prints a clear
message and exits nonzero instead of inventing results.

Usage:
    python scripts/verify_addresses.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# `scout` lives at the repo root. Under pytest this is importable for free
# (pyproject's `pythonpath = ["."]`), but running this file directly
# (`python scripts/verify_addresses.py`) only puts scripts/ on sys.path, so
# give standalone CLI runs the same root pytest gets.
sys.path.insert(0, str(REPO_ROOT))

# Reuse the shared vault library (also used by gen_index.py and the spike
# harnesses) instead of re-implementing frontmatter parsing. Not a normal
# package, so it needs its directory on sys.path too — same pattern as
# gen_index.py. mypy cannot resolve this dynamic path; the module behaves
# as `Any` from here on, which is fine (see the T-2.5 handoff notes).
sys.path.insert(0, str(REPO_ROOT / "spikes" / "_lib"))
import vault as vault  # type: ignore[import-not-found]  # noqa: E402

from scout.core import normalize_path, post_filter  # noqa: E402
from scout.types import Address, RagBackend  # noqa: E402


class VerifyStatus(StrEnum):
    """Classification of one `sources[]` address against a RAG backend (R-6.5)."""

    PASS = "pass"
    FAIL = "fail"
    DRIFT = "drift"


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Verification outcome for one `sources[]` address.

    Attributes:
        path: The address's `raw/` file path (`sources[].path`).
        hint: The phrase queried against the RAG backend (`sources[].hint`).
        status: PASS / FAIL / DRIFT — see the module docstring.
        matched_files: Distinct, normalized `file_path`s the backend actually
            retrieved for `hint`. Empty on FAIL; on DRIFT this is the
            diagnostic — what the hint pulled instead of `path`; on PASS it
            includes `path` (and possibly other retrieved files).
    """

    path: str
    hint: str
    status: VerifyStatus
    matched_files: tuple[str, ...]


async def verify_address(backend: RagBackend, address: Address) -> VerifyReport:
    """Query `backend` with `address.hint` and classify the result (R-6.5).

    Args:
        backend: Any `RagBackend` adapter (T-2.1's RAG-Anything today; any
            future engine behind the same interface, T-2.7).
        address: One `sources[]` pointer — `hint` is queried, `path` is the
            file it is supposed to retrieve.

    Returns:
        A `VerifyReport` classified PASS/FAIL/DRIFT per the module docstring.
    """
    from scout.types import Scope

    chunks = await backend.retrieve(
        address.hint, scope=Scope(roles=frozenset(["all"])), k=5
    )
    if not chunks:
        return VerifyReport(
            path=address.path,
            hint=address.hint,
            status=VerifyStatus.FAIL,
            matched_files=(),
        )

    matched_files = tuple(sorted({normalize_path(c.file_path) for c in chunks}))
    on_source = post_filter(chunks, address.path)
    status = VerifyStatus.PASS if on_source else VerifyStatus.DRIFT
    return VerifyReport(
        path=address.path, hint=address.hint, status=status, matched_files=matched_files
    )


async def verify_all(
    backend: RagBackend, addresses: Sequence[Address]
) -> list[VerifyReport]:
    """Verify every address concurrently, preserving input order (R-6.5).

    Args:
        backend: The RAG backend adapter to query.
        addresses: All `sources[]` addresses to verify — typically every
            address collected across the whole vault.

    Returns:
        One `VerifyReport` per input address, in the same order.
    """
    sem = asyncio.Semaphore(5)

    async def _sem_verify(a: Address) -> VerifyReport:
        async with sem:
            return await verify_address(backend, a)

    reports = await asyncio.gather(*(_sem_verify(a) for a in addresses))
    return list(reports)


BackendFactory = Callable[[], RagBackend | None]
PagesLoader = Callable[[], Iterable[vault.Page]]


def _no_backend_configured() -> RagBackend | None:
    """Default backend factory — no live RAG backend is wired yet (T-2.1).

    Returns:
        None, always. `main()` treats this as "nothing to verify against"
        and refuses to fabricate a report.
    """
    return None


def _collect_addresses(pages: Iterable[vault.Page]) -> list[Address]:
    """Flatten every page's `frontmatter.sources[]` into `Address` objects.

    Malformed entries (not a mapping, or missing `path`/`hint`) are silently
    skipped — that shape validation is `gen_index.py`'s job (R-1.4); this
    only needs well-formed addresses to query the RAG backend with.

    Args:
        pages: Loaded vault pages (see `vault.load_pages`).

    Returns:
        One `Address` per valid `sources[]` entry, in page/source order.
    """
    addresses: list[Address] = []
    for page in pages:
        for src in page.sources:
            if not isinstance(src, dict):
                continue
            path = src.get("path")
            hint = src.get("hint")
            if not path or not hint:
                continue
            loc = src.get("loc")
            addresses.append(
                Address(path=str(path), hint=str(hint), loc=str(loc) if loc else None)
            )
    return addresses


def _print_report(reports: Sequence[VerifyReport]) -> None:
    """Render a human-readable verification report to stdout."""
    for r in reports:
        print(f"{r.status.value.upper():5s} {r.path}  (hint: {r.hint!r})")
        if r.status is not VerifyStatus.PASS and r.matched_files:
            print(f"      retrieved from: {', '.join(r.matched_files)}")

    counts = Counter(r.status for r in reports)
    print(
        f"\n{len(reports)} address(es) checked — "
        f"{counts.get(VerifyStatus.PASS, 0)} PASS · "
        f"{counts.get(VerifyStatus.FAIL, 0)} FAIL · "
        f"{counts.get(VerifyStatus.DRIFT, 0)} DRIFT"
    )


def _default_backend_factory() -> RagBackend | None:
    try:
        from scout.backends.pgvector import PgVectorRlsBackend

        return PgVectorRlsBackend()
    except Exception:
        return None


def main(
    *,
    backend_factory: BackendFactory = _no_backend_configured,
    pages_loader: PagesLoader = vault.load_pages,
) -> int:
    """CLI entry point: verify every vault address against a RAG backend.

    Kept thin on purpose — all classification logic lives in
    `verify_address`/`verify_all`; this only wires a backend and the vault's
    addresses together and renders the report (R-6.5).

    Args:
        backend_factory: Builds the `RagBackend` to query. Defaults to a
            stub returning `None` because no real backend is wired yet
            (T-2.1 wires RAG-Anything) — this must never fabricate results
            against nothing. Callers (or a future real CLI wrapper) inject
            a live factory once one exists.
        pages_loader: Loads vault pages. Defaults to `vault.load_pages`
            (the real `wiki/` tree); tests inject a fake to avoid depending
            on the real vault's contents (T-2.5's logic tests are backend-
            and vault-independent).

    Returns:
        0 if every address is PASS (or there are none), 1 if any address is
        FAIL/DRIFT, 2 on a setup error (no backend configured, or the vault
        fails to load).
    """
    backend = backend_factory()
    if backend is None:
        print(
            "No RAG backend configured — verify_addresses has nothing to "
            "query yet (the real RAG-Anything backend is wired in T-2.1). "
            "Refusing to fabricate a report; pass a backend_factory once a "
            "live backend exists."
        )
        return 2

    try:
        pages = list(pages_loader())
    except ValueError as e:
        print(f"VAULT ERROR: {e}")
        return 2

    addresses = _collect_addresses(pages)
    if not addresses:
        print("No sources[] addresses found in the vault. Nothing to verify.")
        return 0

    reports = asyncio.run(verify_all(backend, addresses))
    _print_report(reports)

    bad = sum(1 for r in reports if r.status is not VerifyStatus.PASS)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(backend_factory=_default_backend_factory))
