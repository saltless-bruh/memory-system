#!/usr/bin/env python3
"""Verify every page-scoped RAG address with total 0/1/2 exit semantics.

The merge gate for `sources[]`. Each address is queried under its declaring
page's ``department:``, so a page can only certify against documents whose ACL
admits it, and an address PASSes only when the file it names both wins the
ranking and is lexically backed by its own text — see the criterion note below
`TOP_RANK`. Exit codes are total: ``0`` every address passed, ``1`` at least
one semantic FAIL/DRIFT, ``2`` infrastructure or configuration (never a
mutation trigger, and never a silent green).
"""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dotenv  # noqa: E402

dotenv.load_dotenv(REPO_ROOT / ".env")

from scout import vault  # noqa: E402
from scout.core import normalize_path, post_filter  # noqa: E402
from scout.policy import validate_caller_departments  # noqa: E402
from scout.types import RagBackend, RagChunk, Scope, ScopedAddress  # noqa: E402

# ── The pass criterion (B1) ──────────────────────────────────────────────────
#
# This gate used to PASS whenever the addressed file appeared *anywhere* in a
# global top-5. On a ~23-chunk corpus that window covered a fifth of everything,
# so `"zzqq banana marmalade unicycle wobble 8842"` verified against a vLLM PDF
# and "19/19 PASS" proved close to nothing. Two independent conditions replace
# it, and an address must satisfy BOTH:
#
#   1. RANK — the addressed file must own a *top-scoring* chunk of the whole
#      department-scoped retrieval (rank 1; exact score ties share the rank).
#      Rank 1 is deliberately NOT derived from corpus size: any window that
#      grows with the corpus gets *weaker* as the corpus grows, which is the
#      wrong direction, whereas "best match in everything this page may see"
#      means the same thing at 23 chunks and at 23 million. Ties share rank 1
#      because the backend's `ORDER BY rrf_score DESC` carries no tiebreaker —
#      testing strict row position would make the gate nondeterministic between
#      runs (two live chunks currently tie at 0.03252).
#
#   2. GROUNDING — at least `GROUNDING_MIN_COVERAGE` of the hint's content
#      tokens must actually occur in the text retrieved *from the addressed
#      file*. Rank alone cannot reject gibberish: a nonsense embedding still
#      has a nearest neighbour, and measured live that neighbour was the vLLM
#      PDF at rank 1. Grounding is what makes a hint provably *about* its file
#      rather than accidentally nearest to it.
#
# Why there is no similarity floor here: `RagChunk.score` carries Reciprocal
# Rank Fusion values from `scout/backends/pgvector.py` — `1/(60+rank)` summed
# over the dense and sparse arms, capped near 0.033. That is an ordinal fusion
# weight, not a similarity; thresholding it would bake the backend's RRF
# constant into the merge gate while reading as a similarity. A real cosine
# floor would have to be surfaced through `RagChunk` and `PgVectorRlsBackend`,
# which is a backend change and is deliberately out of scope here. The tradeoff
# is that rank + grounding is a *relative* criterion: a corpus containing no
# relevant document can still elect a winner, and grounding then bounds the
# damage lexically rather than metrically. A cosine floor remains the better
# long-term answer and is recorded as such.

#: The addressed file must hold this rank (ties included) in the scoped result.
TOP_RANK = 1

#: Fraction of a hint's content tokens that must occur in on-source text.
GROUNDING_MIN_COVERAGE = 0.5

#: How many chunks to pull back. This is a *diagnostics and lookup* window
#: only — both conditions above read the leader's score and the on-source
#: chunks, so the verdict is identical for any k >= 1. It is not the criterion.
DIAGNOSTIC_K = 10

#: Tokens shorter than this carry no retrieval signal (``kv``, ``3``, ``of``).
_MIN_TOKEN_LENGTH = 3

#: A hint made only of these is not a retrieval key, so it can never ground.
_STOPWORDS = frozenset(
    {
        "and", "are", "but", "can", "for", "from", "has", "have", "how", "into",
        "its", "not", "the", "that", "then", "this", "was", "were", "what",
        "when", "which", "will", "with", "you", "your",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: `scout.chunker` appends ``(i/n)`` to a locator when it splits one parsed
#: section into several chunks (m1). A declared section-level locator is still
#: satisfied by any part of that section, so the marker is stripped before
#: comparison.
_LOC_PART_RE = re.compile(r"\s*\(\d+\s*/\s*\d+\)\s*$")


def content_tokens(text: str) -> frozenset[str]:
    """Return the tokens of `text` that carry retrieval signal.

    Lowercased alphanumeric runs, minus stopwords and runs shorter than
    `_MIN_TOKEN_LENGTH`. ``"PagedAttention KV-Cache"`` yields
    ``{"pagedattention", "cache"}``.
    """
    return frozenset(
        token
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _STOPWORDS
    )


def grounding_coverage(hint: str, on_source_texts: Iterable[str]) -> float:
    """Fraction of `hint`'s content tokens that occur in the addressed file.

    Returns ``0.0`` for a hint with no content tokens at all (``"the"``,
    ``"of 3"``): such a hint is not a retrieval key and must never certify an
    address, however the ranking happens to fall.
    """
    wanted = content_tokens(hint)
    if not wanted:
        return 0.0
    available = content_tokens(" ".join(on_source_texts))
    return len(wanted & available) / len(wanted)


def hint_is_grounded(hint: str, on_source_texts: Iterable[str]) -> bool:
    """True when `hint` is lexically supported by the addressed file's text."""
    return grounding_coverage(hint, on_source_texts) >= GROUNDING_MIN_COVERAGE


def normalize_loc(loc: str) -> str:
    """Canonicalize a locator for comparison, dropping any ``(i/n)`` marker."""
    return _LOC_PART_RE.sub("", loc.strip()).casefold()


def loc_is_consistent(declared: str | None, retrieved: Iterable[str]) -> bool:
    """True when `declared` names a locator the retrieval actually returned (m2).

    Used by `scripts.mint` at authoring time. Verification does not enforce it:
    the merge gate certifies *retrievability*, while minting certifies that the
    locator a human will read in the frontmatter is not a fiction. An empty
    `retrieved` (a backend that carries no locators) yields ``False`` — an
    unverifiable locator is not a verified one.
    """
    if not declared or not declared.strip():
        return False
    wanted = normalize_loc(declared)
    if not wanted:
        return False
    return any(normalize_loc(loc) == wanted for loc in retrieved if loc)


def holds_top_rank(chunks: Sequence[RagChunk], path: str) -> bool:
    """True when `path` owns a top-scoring chunk of `chunks` (ties included).

    `chunks` must be the backend's own descending-by-score ordering.
    """
    if not chunks:
        return False
    leader = chunks[0].score
    target = normalize_path(path)
    for chunk in chunks:
        if chunk.score < leader:
            break  # past the tied leaders — everything after is rank > TOP_RANK
        if normalize_path(chunk.file_path) == target:
            return True
    return False


class VerifyStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    DRIFT = "drift"


@dataclass(frozen=True, slots=True)
class VerifyReport:
    scoped_address: ScopedAddress
    status: VerifyStatus
    matched_files: tuple[str, ...]
    matched_locs: tuple[str, ...] = ()
    detail: str = ""

    @property
    def path(self) -> str:
        return self.scoped_address.address.path

    @property
    def hint(self) -> str:
        return self.scoped_address.address.hint


def _on_source_locs(chunks: Sequence[RagChunk]) -> tuple[str, ...]:
    """Locators carried by on-source chunks, best-scoring first, deduplicated."""
    return tuple(dict.fromkeys(chunk.loc for chunk in chunks if chunk.loc))


async def verify_address(
    backend: RagBackend, scoped_address: ScopedAddress
) -> VerifyReport:
    """Classify one address under its declaring page department.

    ``PASS``  the addressed file holds rank `TOP_RANK` in the scoped retrieval
              *and* the hint is grounded in the text that file returned.
    ``DRIFT`` the file is retrievable for this hint but lost the ranking, or won
              it without the hint being grounded — the phrase needs re-minting.
    ``FAIL``  the file contributed nothing at all: unindexed, empty after
              parsing, or outside the declaring page's department.
    """
    scope = Scope(departments=frozenset({scoped_address.department}))
    address = scoped_address.address
    chunks = await backend.retrieve(address.hint, scope=scope, k=DIAGNOSTIC_K)
    matched_files = tuple(sorted({normalize_path(chunk.file_path) for chunk in chunks}))
    on_source = post_filter(chunks, address.path)

    if not on_source:
        # Absent from the diagnostic window is not the same as absent from the
        # corpus. Ask the backend directly — the same pre-filtered query
        # production `rag_fetch` runs — before declaring the source empty.
        on_source = post_filter(
            await backend.retrieve(
                address.hint, path=address.path, scope=scope, k=DIAGNOSTIC_K
            ),
            address.path,
        )
        if not on_source:
            return VerifyReport(
                scoped_address,
                VerifyStatus.FAIL,
                matched_files,
                (),
                "addressed file returned no chunks for this hint",
            )

    locs = _on_source_locs(on_source)
    if not holds_top_rank(chunks, address.path):
        return VerifyReport(
            scoped_address,
            VerifyStatus.DRIFT,
            matched_files,
            locs,
            f"another file outranks it (rank {TOP_RANK} required)",
        )
    if not hint_is_grounded(address.hint, [chunk.text for chunk in on_source]):
        coverage = grounding_coverage(
            address.hint, [chunk.text for chunk in on_source]
        )
        return VerifyReport(
            scoped_address,
            VerifyStatus.DRIFT,
            matched_files,
            locs,
            f"hint is ungrounded in the source text "
            f"(coverage {coverage:.0%} < {GROUNDING_MIN_COVERAGE:.0%})",
        )
    return VerifyReport(scoped_address, VerifyStatus.PASS, matched_files, locs)


async def verify_all(
    backend: RagBackend, addresses: Sequence[ScopedAddress]
) -> list[VerifyReport]:
    """Verify concurrently while preserving stable page/source order."""
    semaphore = asyncio.Semaphore(5)

    async def bounded(address: ScopedAddress) -> VerifyReport:
        async with semaphore:
            return await verify_address(backend, address)

    return list(await asyncio.gather(*(bounded(address) for address in addresses)))


BackendFactory = Callable[[], RagBackend | None]
PagesLoader = Callable[[], Iterable[vault.Page]]


def _no_backend_configured() -> RagBackend | None:
    return None


def _collect_addresses(pages: Iterable[vault.Page]) -> list[ScopedAddress]:
    """Collect page-scoped sources without collapsing duplicate addresses."""
    collected: list[ScopedAddress] = []
    for page in pages:
        department = next(iter(validate_caller_departments([page.department])))
        raw_sources = page.frontmatter.get("sources", [])
        if not isinstance(raw_sources, list):
            continue
        for source_index, source in enumerate(raw_sources):
            if not isinstance(source, dict):
                continue
            path = source.get("path")
            hint = source.get("hint")
            if not path or not hint:
                continue
            from scout.types import Address

            loc = source.get("loc")
            collected.append(
                ScopedAddress(
                    page_path=page.rel,
                    page_slug=page.slug,
                    source_index=source_index,
                    department=department,
                    address=Address(
                        path=str(path),
                        hint=str(hint),
                        loc=str(loc) if loc else None,
                    ),
                )
            )
    return collected


def _print_report(reports: Sequence[VerifyReport]) -> None:
    for report in reports:
        identity = f"{report.scoped_address.page_path}#{report.scoped_address.source_index}"
        print(f"{report.status.value.upper():5s} {identity} -> {report.path}")
        if report.status is not VerifyStatus.PASS:
            if report.detail:
                print(f"      reason: {report.detail}")
            if report.matched_files:
                print(f"      retrieved from: {', '.join(report.matched_files)}")
        elif report.matched_locs and not loc_is_consistent(
            report.scoped_address.address.loc, report.matched_locs
        ):
            # Advisory only — never changes the exit code. Minting refuses a
            # locator the source does not carry (m2); an address whose locator
            # went stale *after* it was minted is a content decision, so the
            # gate reports it and leaves it to a human.
            print(
                f"      note: declared loc {report.scoped_address.address.loc!r} "
                f"is not among the locators retrieved "
                f"({', '.join(report.matched_locs) or 'none'})"
            )
    counts = Counter(report.status for report in reports)
    print(
        f"\n{len(reports)} address(es) checked — "
        f"{counts.get(VerifyStatus.PASS, 0)} PASS · "
        f"{counts.get(VerifyStatus.FAIL, 0)} FAIL · "
        f"{counts.get(VerifyStatus.DRIFT, 0)} DRIFT"
    )


async def _close_backend(backend: RagBackend) -> None:
    close = getattr(backend, "close", None)
    if close is None:
        return
    result: Any = close()
    if inspect.isawaitable(result):
        await result


async def _execute(backend: RagBackend, pages_loader: PagesLoader) -> int:
    try:
        pages = list(pages_loader())
        addresses = _collect_addresses(pages)
        if not addresses:
            print("No sources[] addresses found in the vault. Nothing to verify.")
            return 0
        reports = await verify_all(backend, addresses)
        _print_report(reports)
        return 1 if any(r.status is not VerifyStatus.PASS for r in reports) else 0
    finally:
        await _close_backend(backend)


def _default_backend_factory() -> RagBackend | None:
    from scout.backends.pgvector import PgVectorRlsBackend

    return PgVectorRlsBackend()


def main(
    *,
    backend_factory: BackendFactory = _no_backend_configured,
    pages_loader: PagesLoader = vault.load_pages,
) -> int:
    """Return 0 PASS, 1 semantic drift/failure, or 2 infrastructure failure."""
    try:
        backend = backend_factory()
    except Exception:  # noqa: BLE001 - configuration errors are redacted
        print("INFRASTRUCTURE ERROR: RAG backend configuration failed.")
        return 2
    if backend is None:
        print("No RAG backend configured; refusing to fabricate verification.")
        return 2
    try:
        return asyncio.run(_execute(backend, pages_loader))
    except Exception:  # noqa: BLE001 - database/model/network details may contain secrets
        print("INFRASTRUCTURE ERROR: address verification could not complete.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(backend_factory=_default_backend_factory))
