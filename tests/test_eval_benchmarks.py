"""Integration tests for RAG Benchmarks (Needle-in-a-Haystack & Hard Negatives)."""

from __future__ import annotations

import pytest

from tests.eval_hard_negatives import run_hard_negatives
from tests.eval_niah import run_niah_depth

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_benchmark_niah_middle_depth() -> None:
    """Evaluates buried needle retrieval at 50% depth."""
    passed = await run_niah_depth(depth_pct=50)
    assert passed is True, "Needle at 50% depth was not retrieved"


@pytest.mark.asyncio
async def test_benchmark_hard_negatives_ranking() -> None:
    """Evaluates ranking discrimination between true positive and hard negative."""
    passed = await run_hard_negatives()
    assert passed is True, "Ground truth was outranked by hard negative"
