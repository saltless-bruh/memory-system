"""Tests for ContextualChunker, LiteLLMBatchEmbedder, and strict embedding validation."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from scout.chunker import (
    ContextualChunker,
    EmbeddingError,
    LiteLLMBatchEmbedder,
    derive_chunk_loc,
)
from scout.parsers import ParsedDocument, ParsedSection, parse_csv
from tests.fakes import FakeEmbedder

_BENCHMARK_COLUMNS = (
    "model,concurrency,prompt_tokens,completion_tokens,ttft_ms,"
    "inter_token_latency_ms,tokens_per_sec,vram_usage_gb,p99_latency_ms,error_rate"
)


def _benchmark_csv(rows: int) -> str:
    """Build a benchmark CSV shaped like `raw/data/llm_inference_slo_benchmarks.csv`."""
    lines = [_BENCHMARK_COLUMNS]
    for row in range(1, rows + 1):
        lines.append(
            f"llama-3.3-70b-vllm-variant-{row},{row * 8},1024,256,"
            f"{180 + row}.5,{9 + row}.1,{110 - row}.8,{38 + row}.5,"
            f"{295 + row}.1,0.00{row}"
        )
    return "\n".join(lines) + "\n"


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
    total = len(chunks)
    for part, chunk in enumerate(chunks, start=1):
        # Each split chunk names its own part, never the parent span verbatim.
        assert chunk.loc == f"Section Long ({part}/{total})"
        assert chunk.context_prefix == (
            f"[Document: Long RFC | Source: raw/rfc/long.md "
            f"| Context: Section Long ({part}/{total})]"
        )
        assert len(chunk.chunk_text) <= 100
    assert chunks[0].chunk_text[-20:].strip() in chunks[1].chunk_text


def test_split_csv_section_chunks_do_not_all_claim_the_parent_rows() -> None:
    """Split chunks must cite the rows they hold, not the whole parent block.

    Regression guard for a 10-row CSV parsed as one `Rows 1-10` section: the
    chunker used to copy that locator onto all three chunks, so a citation could
    name rows the quoted text never came from.
    """
    doc = parse_csv(_benchmark_csv(10), "raw/data/llm_inference_slo_benchmarks.csv")
    assert [section.loc for section in doc.sections] == ["Rows 1-10"]

    # Production chunking parameters (scout.ingest builds ContextualChunker()).
    chunks = ContextualChunker(max_chunk_chars=1000, overlap_chars=100).chunk_document(doc)

    assert len(chunks) == 3
    locs = [chunk.loc for chunk in chunks]
    assert locs != ["Rows 1-10"] * 3
    assert len(set(locs)) == 3
    assert "Rows 1-10" not in locs

    # Every locator names rows the chunk actually carries.
    for chunk in chunks:
        assert chunk.loc is not None
        first, _, last = chunk.loc.removeprefix("Rows ").partition("-")
        assert f"Row {first}:" in chunk.chunk_text
        assert f"Row {last}:" in chunk.chunk_text
        assert chunk.context_prefix.endswith(f"| Context: {chunk.loc}]")

    # Ordered, covering the whole block; a boundary row may repeat because
    # consecutive chunks overlap, but no chunk over-claims the rows it holds.
    assert locs == ["Rows 1-4", "Rows 5-8", "Rows 8-10"]


def test_unsplit_section_keeps_its_parsed_locator() -> None:
    """A section that fits in one chunk keeps the locator the parser assigned."""
    doc = parse_csv(_benchmark_csv(2), "raw/data/small.csv")
    chunks = ContextualChunker(max_chunk_chars=1000, overlap_chars=100).chunk_document(doc)
    assert [chunk.loc for chunk in chunks] == ["Rows 1-2"]


@pytest.mark.parametrize(
    "parent_loc,chunk_text,part,total,expected",
    [
        # Row-addressed parents resolve to the rows actually present.
        ("Rows 1-10", "Row 5: a\nRow 6: b", 2, 3, "Rows 5-6"),
        ("Rows 1-10", "Row 9: a", 3, 3, "Row 9"),
        # Row numbers outside the parent block never widen the claim.
        ("Rows 1-4", "Row 3: a\nRow 99: b", 2, 2, "Row 3"),
        # A non-row locator stays meaningful and gains an explicit part marker.
        ("Full Source Code", "def f(): ...", 2, 3, "Full Source Code (2/3)"),
        ("p.12", "text", 1, 2, "p.12 (1/2)"),
        # A row-addressed parent with no whole row falls back to the part marker.
        ("Rows 1-10", "trailing fragment", 2, 3, "Rows 1-10 (2/3)"),
        # Unsplit sections and missing locators.
        ("Rows 1-10", "Row 1: a", 1, 1, "Rows 1-10"),
        (None, "text", 2, 3, "Part 2/3"),
        (None, "text", 1, 1, None),
    ],
)
def test_derive_chunk_loc(
    parent_loc: str | None,
    chunk_text: str,
    part: int,
    total: int,
    expected: str | None,
) -> None:
    assert derive_chunk_loc(parent_loc, chunk_text, part, total) == expected


def test_contextual_chunker_overlap_cannot_loop_on_long_unicode_word() -> None:
    text = "ữ" * 251
    chunks = ContextualChunker(max_chunk_chars=50, overlap_chars=100).chunk_text(text)
    assert len(chunks) == 202
    assert all(0 < len(chunk.chunk_text) <= 50 for chunk in chunks)
    assert chunks[-1].chunk_text.endswith("ữ")


def test_litellm_batch_embedder_empty_input() -> None:
    embedder = LiteLLMBatchEmbedder()
    assert embedder.embed_texts([]) == []


def test_litellm_batch_embedder_fails_fast_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LiteLLM is unreachable, EmbeddingError must be raised."""
    def fail_request(*_args: Any, **_kwargs: Any) -> None:
        raise urllib.error.URLError("synthetic offline transport failure")

    monkeypatch.setattr(urllib.request, "urlopen", fail_request)
    embedder = LiteLLMBatchEmbedder(
        base_url="http://invalid-host-unreachable:9999", api_key="test-key"
    )
    with pytest.raises(EmbeddingError, match="LiteLLM embedding call failed"):
        embedder.embed_texts(["test string"])


def test_litellm_batch_embedder_rejects_dimension_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self
        def __exit__(self, *args: Any) -> None:
            pass
        def read(self) -> bytes:
            payload = {"data": [{"embedding": [0.1] * 512, "index": 0}]}
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    embedder = LiteLLMBatchEmbedder(dim=1024, api_key="test-key")
    with pytest.raises(EmbeddingError, match="Embedding dimension mismatch"):
        embedder.embed_texts(["test text"])


def test_litellm_batch_embedder_rejects_non_finite_values(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self
        def __exit__(self, *args: Any) -> None:
            pass
        def read(self) -> bytes:
            # Inject float("nan") or float("inf") equivalent via JSON number issue or inf
            vec = [0.1] * 1023 + [float("inf")]
            payload = {"data": [{"embedding": vec, "index": 0}]}
            return json.dumps(payload).replace("Infinity", "1e999").encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    embedder = LiteLLMBatchEmbedder(dim=1024, api_key="test-key")
    with pytest.raises(EmbeddingError, match="non-finite"):
        embedder.embed_texts(["test text"])


def test_litellm_batch_embedder_successful_exact_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, urllib.request.Request] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self
        def __exit__(self, *args: Any) -> None:
            pass
        def read(self) -> bytes:
            payload = {
                "data": [
                    {"embedding": [0.2] * 1024, "index": 0},
                    {"embedding": [0.3] * 1024, "index": 1},
                ]
            }
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request: urllib.request.Request, **kwargs: object) -> FakeResponse:
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    embedder = LiteLLMBatchEmbedder(dim=1024, api_key="test-key")
    results = embedder.embed_texts(["first", "second"])

    assert len(results) == 2
    assert len(results[0]) == 1024
    assert len(results[1]) == 1024
    assert results[0][0] == 0.2
    assert results[1][0] == 0.3
    assert captured["request"].full_url == "http://localhost:4000/v1/embeddings"


def test_litellm_batch_embedder_orders_out_of_order_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": [
                        {"embedding": [0.3, 0.4], "index": 1},
                        {"embedding": [0.1, 0.2], "index": 0},
                    ]
                }
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    embedder = LiteLLMBatchEmbedder(dim=2, api_key="test-key")
    assert embedder.embed_texts(["first", "second"]) == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.parametrize(
    "items,match",
    [
        (
            [
                {"embedding": [0.1, 0.2], "index": 0},
                {"embedding": [0.3, 0.4], "index": 0},
            ],
            "duplicate index",
        ),
        ([{"embedding": [0.1, 0.2], "index": 2}], "cardinality mismatch"),
        ([{"embedding": ["bad", 0.2], "index": 0}], "non-numeric"),
    ],
)
def test_litellm_batch_embedder_rejects_malformed_items(
    monkeypatch: pytest.MonkeyPatch,
    items: list[dict[str, object]],
    match: str,
) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps({"data": items}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    embedder = LiteLLMBatchEmbedder(dim=2, api_key="test-key")
    texts = ["first"] if match == "non-numeric" else ["first", "second"]
    with pytest.raises(EmbeddingError, match=match):
        embedder.embed_texts(texts)


def test_fake_embedder_offline_deterministic() -> None:
    embedder = FakeEmbedder(dim=1024)
    vectors = embedder.embed_texts(["hello world", "test input"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    assert len(vectors[1]) == 1024

    vectors2 = embedder.embed_texts(["hello world"])
    assert vectors[0] == vectors2[0]


# ── provider batch cap (scale regression) ────────────────────────────────────


def test_embed_texts_splits_batches_at_the_provider_cap() -> None:
    """127 chunks must become two requests, not one rejected request.

    Gemini's `batchEmbedContents` refuses more than 100 inputs with
    `400 INVALID_ARGUMENT`. A 26-page paper yields ~127 chunks, so an un-split
    call fails outright. The old corpus never caught this: its largest document
    produced five chunks.
    """
    from scout.chunker import MAX_EMBED_BATCH, LiteLLMBatchEmbedder

    embedder = LiteLLMBatchEmbedder(base_url="http://gateway/v1", api_key="k")
    seen: list[int] = []

    def fake_batch(texts: list[str]) -> list[list[float]]:
        assert len(texts) <= MAX_EMBED_BATCH, "a batch exceeded the provider cap"
        seen.append(len(texts))
        # echo the text index so ordering can be checked end to end
        return [[float(int(t))] * 3 for t in texts]

    embedder._embed_one_batch = fake_batch  # type: ignore[method-assign]

    out = embedder.embed_texts([str(i) for i in range(127)])

    assert seen == [100, 27], f"expected two capped batches, got {seen}"
    assert len(out) == 127
    # order must survive concatenation: text "42" keeps vector position 42
    assert out[42][0] == 42.0
    assert out[126][0] == 126.0


def test_embed_texts_makes_one_request_below_the_cap() -> None:
    from scout.chunker import LiteLLMBatchEmbedder

    embedder = LiteLLMBatchEmbedder(base_url="http://gateway/v1", api_key="k")
    seen: list[int] = []
    embedder._embed_one_batch = lambda t: (seen.append(len(t)), [[0.0]] * len(t))[1]  # type: ignore[method-assign]
    embedder.embed_texts(["a"] * 20)
    assert seen == [20], "small inputs must not be split unnecessarily"
