from __future__ import annotations

import pytest

from scripts import measure_tokens
from scripts.measure_tokens import count_tokens, measure, project_naive


def test_count_tokens_empty_and_nonempty() -> None:
    assert count_tokens("") == 0
    assert count_tokens("hello world") > 0


def test_project_naive_is_linear() -> None:
    assert project_naive(10.0, 100) == 1000
    assert project_naive(0.0, 500) == 0
    assert project_naive(3.5, 2) == 7


def test_measure_on_real_vault_agent_context_beats_naive() -> None:
    """The core R-5.2 property: agent context < naive dump, both positive."""
    pages = [
        p
        for p in measure_tokens.vault.load_pages()
        if p.slug not in {"index", "archive", "log"}
    ]
    if not pages:
        pytest.skip("Wiki vault currently in Clean Slate state (0 topic pages)")

    index_text = (measure_tokens.vault.WIKI_DIR / "index.md").read_text(
        encoding="utf-8"
    )
    m = measure(pages, index_text)

    assert m.n_pages > 0
    assert m.agent_context_tokens > 0
    assert m.index_tokens > 0
    assert m.agent_context_tokens < m.naive_dump_tokens
