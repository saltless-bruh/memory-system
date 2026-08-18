"""Tests for scout.backends — fake in-memory and PostgreSQL pgvector RLS backends."""

from __future__ import annotations

from scout.backends.fake import FakeRagBackend
from scout.backends.pgvector import PgVectorRlsBackend
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


async def test_pgvector_production_backend_retrieve_empty_pool() -> None:
    from scout.chunker import LiteLLMEmbedder

    backend = PgVectorRlsBackend(embedder=LiteLLMEmbedder(allow_mock=True))
    # When pool is None or during unit tests, verify safe fallback
    backend._pool = None
    res = await backend.retrieve("test query", path="raw/test.pdf")
    assert isinstance(res, list)
    await backend.close()
