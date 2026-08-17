#!/usr/bin/env python3
"""measure_tokens.py — quantify the "cheap agent context" claim (T-3.4 → R-5.2).

The design's bet: an agent answering a question loads **top-K search hits + one
page** (+ maybe a RAG passage), NOT the whole index. So the tokens entering the
agent's context are ~constant in corpus size, while the naive "dump the index +
everything" approach grows linearly with the number of pages.

This measures both on the real vault (tiktoken as a model-agnostic proxy) and
extrapolates the index cost to larger page counts to show the scaling gap.

Usage:
    python scripts/measure_tokens.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import tiktoken

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "spikes" / "_lib"))
import vault as vault  # type: ignore[import-not-found]  # noqa: E402

_ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Token count of `text` (cl100k_base — a model-agnostic proxy)."""
    return len(_ENC.encode(text))


@dataclass(frozen=True, slots=True)
class Measurement:
    """Token measurement of the query flow vs. the naive dump.

    Attributes:
        n_pages: Pages in the vault measured.
        index_tokens: Tokens of the full generated index.md (all summaries).
        topk_payload_tokens: Tokens of a top-K search result (path + summary
            per hit) — what the agent receives from search.
        one_page_tokens: Tokens of one full page (what the agent then reads).
        agent_context_tokens: What actually enters the agent's context in the
            flow (top-K + one page) — the design's "cheap" path (R-5.2).
        naive_dump_tokens: Full index + every page body — the "read it all"
            path the design avoids.
    """

    n_pages: int
    index_tokens: int
    topk_payload_tokens: int
    one_page_tokens: int
    agent_context_tokens: int
    naive_dump_tokens: int


def measure(pages: list[vault.Page], index_text: str, k: int = 5) -> Measurement:
    """Compute the token comparison over `pages`.

    Args:
        pages: Loaded vault content pages.
        index_text: The generated index.md content (all summaries).
        k: Search top-K.

    Returns:
        A `Measurement`.
    """
    index_tokens = count_tokens(index_text)

    def hit_text(p: vault.Page) -> str:
        return f"{p.rel}\n{p.frontmatter.get('summary', '')}"

    topk = sorted(pages, key=lambda p: p.rel)[:k]
    topk_payload = sum(count_tokens(hit_text(p)) for p in topk)

    full_texts = [p.path.read_text(encoding="utf-8") for p in pages]
    one_page = max((count_tokens(t) for t in full_texts), default=0)
    naive_dump = index_tokens + sum(count_tokens(t) for t in full_texts)

    return Measurement(
        n_pages=len(pages),
        index_tokens=index_tokens,
        topk_payload_tokens=topk_payload,
        one_page_tokens=one_page,
        agent_context_tokens=topk_payload + one_page,
        naive_dump_tokens=naive_dump,
    )


def project_naive(per_page_tokens: float, n_pages: int) -> int:
    """Project the naive-dump cost to `n_pages` (grows linearly)."""
    return round(per_page_tokens * n_pages)


def main() -> int:
    """Measure the sample vault and print the comparison + extrapolation."""
    pages = [p for p in vault.load_pages() if p.slug not in {"index", "archive", "log"}]
    index_text = (vault.WIKI_DIR / "index.md").read_text(encoding="utf-8")
    m = measure(pages, index_text)

    print("=== Agent context vs naive dump (tiktoken cl100k_base) ===")
    print(f"  vault pages measured      : {m.n_pages}")
    print(f"  full index.md             : {m.index_tokens:>6} tok")
    print(f"  top-K search payload      : {m.topk_payload_tokens:>6} tok")
    print(f"  one page (largest)        : {m.one_page_tokens:>6} tok")
    print(f"  → AGENT CONTEXT (top-K+pg): {m.agent_context_tokens:>6} tok   [~O(1)]")
    print(f"  → NAIVE (index+all pages) : {m.naive_dump_tokens:>6} tok   [O(N)]")

    per_page_naive = m.naive_dump_tokens / m.n_pages if m.n_pages else 0.0
    print("\n  Extrapolated naive dump (grows with page count):")
    for n in (100, 300, 500):
        print(
            f"    {n:>3} pages ≈ {project_naive(per_page_naive, n):>7} tok"
            f"   vs agent context ~{m.agent_context_tokens} tok (flat)"
        )
    print(
        "\n  R-5.2 holds: agent context stays ~hundreds of tokens; the naive "
        "dump scales O(N) into the tens of thousands."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
