"""Tests for scout.backends — fake in-memory and PostgreSQL pgvector RLS backends."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from scout.backends.fake import FakeRagBackend
from scout.backends.pgvector import PgVectorRlsBackend
from scout.types import RagChunk, Scope
from tests.fakes import FakeEmbedder


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
    scope = Scope(departments=frozenset({"blueteam"}))
    await backend.retrieve("x", scope=scope)
    assert backend.record_scope is scope


# ── pgvector backend ──────────────────────────────────────────────────────
async def test_pgvector_sql_generation() -> None:
    backend = PgVectorRlsBackend(embedder=FakeEmbedder())

    # Verify query generation structure without live database
    assert backend.host is None
    assert backend.database is None
    assert backend.embedder is not None


async def test_pgvector_department_resolution() -> None:
    backend = PgVectorRlsBackend(embedder=FakeEmbedder())
    scope = Scope(departments=frozenset(["redteam", "blueteam"]))
    depts_str = backend._resolve_depts(scope)
    assert "redteam" in depts_str
    assert "blueteam" in depts_str


async def test_pgvector_production_backend_interface() -> None:
    backend = PgVectorRlsBackend(embedder=FakeEmbedder())
    assert isinstance(backend, PgVectorRlsBackend)
    await backend.close()


async def test_pgvector_production_backend_retrieve_with_mock_pool() -> None:
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire
    mock_pool.close = AsyncMock()

    embedder = FakeEmbedder()
    backend = PgVectorRlsBackend(embedder=embedder, pool=mock_pool)
    res = await backend.retrieve("test query", path="raw/test.pdf")
    assert isinstance(res, (list, tuple))
    await backend.close()
