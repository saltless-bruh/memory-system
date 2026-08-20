"""Live smoke test for the production LiteLLM embedding route."""

from __future__ import annotations

import math

import pytest

from scout.chunker import LiteLLMBatchEmbedder

pytestmark = pytest.mark.integration


def test_live_litellm_embedding_route_returns_strict_vectors() -> None:
    vectors = LiteLLMBatchEmbedder(dim=1024).embed_texts(
        ["SNP memory integration smoke test"]
    )
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    assert all(math.isfinite(value) for value in vectors[0])
