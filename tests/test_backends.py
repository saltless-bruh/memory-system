"""Tests for scout.backends — fake, pgvector stub, and the RAG-Anything adapter."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from scout.backends.fake import FakeRagBackend
from scout.backends.pgvector import PgVectorRlsBackend
from scout.backends.rag_anything import RagAnythingBackend, default_parse
from scout.types import RagChunk, Scope


# ── fake backend ──────────────────────────────────────────────────────────
async def test_fake_ranks_by_hint_overlap(chunks: list[RagChunk]) -> None:
    backend = FakeRagBackend(chunks=chunks)
    out = await backend.retrieve("ESC8 NTLM AD CS", k=1)
    assert out[0].file_path == "raw/advisories/adcs.md"


async def test_fake_respects_k(chunks: list[RagChunk]) -> None:
    backend = FakeRagBackend(chunks=chunks)
    assert len(await backend.retrieve("service", k=2)) == 2


async def test_fake_records_scope() -> None:
    backend = FakeRagBackend(chunks=[])
    scope = Scope(team="blueteam")
    await backend.retrieve("x", scope=scope)
    assert backend.record_scope is scope


# ── pgvector V2 production backend ───────────────────────────────────────
async def test_pgvector_production_backend_interface() -> None:
    backend = PgVectorRlsBackend()
    assert isinstance(backend, PgVectorRlsBackend)
    await backend.close()


# ── RAG-Anything parser (pure) ────────────────────────────────────────────
def test_default_parse_list_of_dicts() -> None:
    raw = [
        {"content": "passage one", "file_path": "raw/a.pdf", "score": 0.7, "page": 3},
        {"text": "passage two", "source": "raw/b.pdf"},
    ]
    out = default_parse(raw, "raw/fallback.pdf")
    assert [c.file_path for c in out] == ["raw/a.pdf", "raw/b.pdf"]
    assert out[0].score == 0.7 and out[0].loc == "3"


def test_default_parse_envelope_and_meta() -> None:
    raw = {
        "chunks": [{"content": "x", "file_path": "raw/a.pdf", "meta": {"team": "rt"}}]
    }
    out = default_parse(raw, "raw/fallback.pdf")
    assert out[0].meta == {"team": "rt"}


def test_default_parse_bare_string_tags_with_path() -> None:
    out = default_parse("just a context blob", "raw/addr.pdf")
    assert len(out) == 1
    assert out[0].file_path == "raw/addr.pdf"


def test_default_parse_empty_string_and_unknown_type() -> None:
    assert default_parse("   ", "raw/a.pdf") == []
    assert default_parse(42, "raw/a.pdf") == []


# ── RAG-Anything adapter (lightrag mocked) ────────────────────────────────
class _FakeRag:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def aquery(self, query: str, param: object = ...) -> object:
        self.calls.append({"query": query, "param": param})
        return self.result


@pytest.fixture
def stub_lightrag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake `lightrag` module so the adapter's lazy import resolves."""
    mod = types.ModuleType("lightrag")

    class QueryParam:  # noqa: N801 - mirrors lightrag's class name
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    mod.QueryParam = QueryParam  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lightrag", mod)


async def test_rag_anything_retrieve_parses_and_passes_only_context(
    stub_lightrag: None,
) -> None:
    fake = _FakeRag(result=[{"content": "hit", "file_path": "raw/a.pdf", "score": 1.0}])
    backend = RagAnythingBackend(rag=fake)
    out = await backend.retrieve("kerberoasting", path="raw/a.pdf", k=5)

    assert [c.text for c in out] == ["hit"]
    # only_need_context and mode are passed through to LightRAG
    param = fake.calls[0]["param"]
    assert param.kwargs["only_need_context"] is True
    assert param.kwargs["mode"] == "mix"
    assert param.kwargs["top_k"] == 5


async def test_rag_anything_custom_parser_used(stub_lightrag: None) -> None:
    sentinel = [RagChunk(text="custom", file_path="raw/z.pdf")]
    backend = RagAnythingBackend(
        rag=_FakeRag(result="ignored"), parse=lambda raw, p: sentinel
    )
    assert list(await backend.retrieve("q", path="raw/z.pdf")) == sentinel
