"""Offline unit tests for PgVectorRlsBackend query building and scope resolution."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout.backends.pgvector import PgVectorRlsBackend
from scout.types import Scope
from tests.fakes import FakeEmbedder


def test_resolve_depts_empty_scope() -> None:
    backend = PgVectorRlsBackend(embedder=FakeEmbedder())
    assert backend._resolve_depts(None) == ""


def test_resolve_depts_uses_only_canonical_departments() -> None:
    backend = PgVectorRlsBackend(embedder=FakeEmbedder())
    scope = Scope(departments=frozenset(["infra", "ai_eng"]))
    resolved = backend._resolve_depts(scope)
    dept_set = set(resolved.split(","))
    assert dept_set == {"ai_eng", "infra"}


@pytest.mark.asyncio
async def test_retrieve_empty_hint_returns_empty() -> None:
    backend = PgVectorRlsBackend(embedder=FakeEmbedder())
    assert await backend.retrieve("") == ()
    assert await backend.retrieve("   ") == ()


@pytest.mark.asyncio
async def test_retrieve_mock_pool_executes_hybrid_query() -> None:
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_rows = [
        {
            "chunk_id": "c1",
            "chunk_text": "Sample chunk text",
            "source_uri": "raw/reports/acme.pdf",
            "metadata": json.dumps({"loc": "p.12"}),
            "rrf_score": 0.032,
        }
    ]
    mock_conn.fetch = AsyncMock(return_value=mock_rows)

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire

    backend = PgVectorRlsBackend(embedder=FakeEmbedder(), pool=mock_pool)
    scope = Scope(departments=frozenset(["infra"]))

    chunks = await backend.retrieve(
        hint="test search query",
        path="raw/reports/acme.pdf",
        scope=scope,
        k=5,
    )

    assert len(chunks) == 1
    assert chunks[0].text == "Sample chunk text"
    assert chunks[0].file_path == "raw/reports/acme.pdf"
    assert chunks[0].loc == "p.12"
    assert chunks[0].score == 0.032

    # Verify session setting was set with resolved depts
    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]
    assert "set_config('scout.current_depts'" in args[0]
    assert args[1] == "infra"


@pytest.mark.asyncio
async def test_retrieve_does_not_block_event_loop_while_embedding() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingEmbedder(FakeEmbedder):
        async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
            started.set()
            await release.wait()
            return self.embed_texts(texts)

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

    backend = PgVectorRlsBackend(embedder=BlockingEmbedder(), pool=mock_pool)
    task = asyncio.create_task(backend.retrieve("query"))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        # This coroutine can still run while the synchronous HTTP embedder is busy.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert await task == []


async def test_a_fully_parameterised_backend_does_not_read_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constructor's five settings must be usable on their own.

    Before this, `_get_pool` called `postgres_settings("query")` unconditionally,
    so a caller that had already resolved the credentials still needed them
    exported into `os.environ` — which is exactly what `scout/cli/config.py`
    exists to avoid. Found by `snpmemory fetch`, which resolves settings from a
    `.env` mapping and never mutates the process environment.
    """
    from scout.backends import pgvector

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the environment must not be consulted")

    captured: dict[str, object] = {}

    async def _fake_create_pool(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(pgvector, "postgres_settings", _explode)
    monkeypatch.setattr(pgvector.asyncpg, "create_pool", _fake_create_pool)

    backend = pgvector.PgVectorRlsBackend(
        host="db.internal",
        port=6543,
        database="snp_rag",
        user="rag_app_role",
        password="secret",
    )
    await backend._get_pool()
    assert captured["host"] == "db.internal"
    assert captured["port"] == 6543


async def test_a_partially_parameterised_backend_still_fills_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The previous behaviour is unchanged whenever anything is missing."""
    from scout.backends import pgvector

    class _Settings:
        host = "from-env"
        port = 5432
        database = "snp_rag"
        user = "rag_app_role"
        password = "from-env"

    captured: dict[str, object] = {}

    async def _fake_create_pool(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(pgvector, "postgres_settings", lambda *_a, **_k: _Settings())
    monkeypatch.setattr(pgvector.asyncpg, "create_pool", _fake_create_pool)

    backend = pgvector.PgVectorRlsBackend(host="explicit")
    await backend._get_pool()
    assert captured["host"] == "explicit"
    assert captured["password"] == "from-env"
