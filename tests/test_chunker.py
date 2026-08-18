"""Tests for ContextualChunker, LiteLLMBatchEmbedder, and fail-fast EmbeddingError."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

from scout.chunker import (
    ContextualChunker,
    EmbeddingError,
    LiteLLMBatchEmbedder,
    LiteLLMEmbedder,
)
from scout.parsers import ParsedDocument, ParsedSection


def test_contextual_chunker_basic() -> None:
    doc = ParsedDocument(
        title="Sample RFC",
        source_uri="raw/rfc/sample.md",
        sections=[
            ParsedSection(
                text="Short section content.",
                loc="Section 1",
                metadata={"author": "alice"},
            )
        ],
    )
    chunker = ContextualChunker(max_chunk_chars=1000)
    chunks = chunker.chunk_document(doc)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.loc == "Section 1"
    assert chunk.metadata["title"] == "Sample RFC"
    assert chunk.metadata["author"] == "alice"
    assert "[Document: Sample RFC | Source: raw/rfc/sample.md | Context: Section 1]" in chunk.context_prefix
    assert chunk.contextual_text.startswith("[Document: Sample RFC")
    assert "Short section content." in chunk.contextual_text


def test_contextual_chunker_sliding_window() -> None:
    long_text = "Word " * 200
    doc = ParsedDocument(
        title="Long RFC",
        source_uri="raw/rfc/long.md",
        sections=[ParsedSection(text=long_text, loc="Section Long")],
    )
    chunker = ContextualChunker(max_chunk_chars=100, overlap_chars=20)
    chunks = chunker.chunk_document(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.loc == "Section Long"
        assert chunk.context_prefix.startswith("[Document: Long RFC")


def test_litellm_batch_embedder_empty_input() -> None:
    embedder = LiteLLMBatchEmbedder(allow_mock=False)
    assert embedder.embed_texts([]) == []


def test_litellm_batch_embedder_fails_fast_when_allow_mock_false() -> None:
    """When allow_mock is False and LiteLLM is unreachable, EmbeddingError must be raised."""
    embedder = LiteLLMBatchEmbedder(
        base_url="http://invalid-host-unreachable:9999",
        allow_mock=False,
    )
    with pytest.raises(EmbeddingError, match="LiteLLM embedding call failed"):
        embedder.embed_texts(["test string"])


def test_litellm_batch_embedder_mock_fallback_when_allow_mock_true() -> None:
    """When allow_mock is True, offline deterministic pseudo-vectors are returned."""
    embedder = LiteLLMEmbedder(dim=1024, allow_mock=True)
    vectors = embedder.embed_texts(["hello world", "test input"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    assert len(vectors[1]) == 1024
    # Deterministic output
    vectors2 = embedder.embed_texts(["hello world"])
    assert vectors[0] == vectors2[0]


def test_litellm_batch_embedder_mock_override_in_call() -> None:
    embedder = LiteLLMBatchEmbedder(
        base_url="http://invalid-host-unreachable:9999",
        allow_mock=False,
    )
    # Overriding allow_mock=True in method call succeeds
    vectors = embedder.embed_texts(["override"], allow_mock=True)
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024


def test_litellm_batch_embedder_successful_api_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def read(self) -> bytes:
            payload = {
                "data": [
                    {"embedding": [0.1] * 512, "index": 0},
                    {"embedding": [0.2] * 1024, "index": 1},
                    {"embedding": [0.3] * 2048, "index": 2},
                ]
            }
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    embedder = LiteLLMBatchEmbedder(dim=1024, allow_mock=False)
    results = embedder.embed_texts(["a", "b", "c"])

    assert len(results) == 3
    # padded from 512 to 1024
    assert len(results[0]) == 1024
    assert results[0][512] == 0.0
    # exact 1024
    assert len(results[1]) == 1024
    # sliced from 2048 to 1024
    assert len(results[2]) == 1024
