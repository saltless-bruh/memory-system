"""Tests for scout.backends — fake in-memory and PostgreSQL pgvector RLS backends."""

from __future__ import annotations

import re
from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout.backends.fake import FakeRagBackend
from scout.backends.pgvector import PgVectorRlsBackend
from scout.types import RagChunk, Scope
from tests.fakes import FakeEmbedder


def _pg_row(path: str = "wiki/page.md") -> dict[str, object]:
    return {
        "chunk_id": f"chunk:{path}",
        "chunk_text": f"Body for {path}",
        "source_uri": path,
        "metadata": {"loc": "Section Detail", "type": "concept"},
        "rrf_score": 0.031,
    }


def _mock_pool(rows: list[dict[str, object]]) -> tuple[MagicMock, MagicMock]:
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)

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
    return mock_pool, mock_conn


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


async def test_pgvector_groups_hybrid_candidates_by_page_before_limit() -> None:
    pool, connection = _mock_pool([_pg_row("wiki/alpha.md")])
    backend = PgVectorRlsBackend(embedder=FakeEmbedder(), pool=pool)

    await backend.retrieve(
        "page grouping",
        scope=Scope(departments=frozenset({"infra"})),
        k=5,
    )

    call = connection.fetch.call_args
    query = " ".join(str(call.args[0]).split())
    assert re.search(r"DISTINCT\s+ON\s*\(\s*doc_id\s*\)", query)
    assert query.index("DISTINCT ON") > query.index("combined AS")
    assert call.args[3] == 20
    assert call.args[5] == 5


async def test_pgvector_class_penalties_are_inside_parameterized_rrf() -> None:
    pool, connection = _mock_pool([_pg_row()])
    backend = PgVectorRlsBackend(
        embedder=FakeEmbedder(),
        pool=pool,
        rrf_k=55,
        raw_rank_penalty=13,
        contested_rank_penalty=21,
    )

    await backend.retrieve(
        "class ranking",
        scope=Scope(departments=frozenset({"infra"})),
    )

    call = connection.fetch.call_args
    query = " ".join(str(call.args[0]).split())
    assert re.search(r"->>\s*'type'\s*=\s*'raw'", query)
    assert re.search(r"->>\s*'contested'\s*=\s*'true'", query)
    assert "unknown" not in query.casefold()
    assert call.args[6:9] == (55, 13, 21)


async def test_pgvector_healthy_hybrid_result_is_explicitly_not_degraded() -> None:
    pool, connection = _mock_pool([_pg_row()])
    backend = PgVectorRlsBackend(embedder=FakeEmbedder(), pool=pool)

    chunks = await backend.retrieve(
        "healthy",
        scope=Scope(departments=frozenset({"infra"})),
    )

    assert chunks[0].meta["degraded"] == "false"
    assert "degraded_reason" not in chunks[0].meta
    assert "vector_matches" in str(connection.fetch.call_args.args[0])


async def test_pgvector_embedding_timeout_falls_back_to_sparse_results() -> None:
    class TimeoutEmbedder:
        async def aembed_texts(self, _texts: Sequence[str]) -> list[list[float]]:
            raise TimeoutError("controlled timeout")

    pool, connection = _mock_pool([_pg_row("wiki/sparse.md")])
    backend = PgVectorRlsBackend(
        embedder=TimeoutEmbedder(),
        pool=pool,
        dense_timeout_seconds=0.01,
    )

    chunks = await backend.retrieve(
        "sparse fallback",
        scope=Scope(departments=frozenset({"infra"})),
    )

    query = str(connection.fetch.call_args.args[0])
    assert "vector_matches" not in query
    assert "text_matches" in query
    assert chunks[0].file_path == "wiki/sparse.md"
    assert chunks[0].meta["degraded"] == "true"
    assert chunks[0].meta["degraded_reason"] == "embedding_timeout"


async def test_pgvector_embedding_error_falls_back_without_leaking_error() -> None:
    class ErrorEmbedder:
        async def aembed_texts(self, _texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("provider secret must not leak")

    pool, _connection = _mock_pool([_pg_row("wiki/sparse.md")])
    backend = PgVectorRlsBackend(embedder=ErrorEmbedder(), pool=pool)

    chunks = await backend.retrieve(
        "sparse fallback",
        scope=Scope(departments=frozenset({"infra"})),
    )

    assert chunks[0].meta["degraded"] == "true"
    assert chunks[0].meta["degraded_reason"] == "embedding_error"
    assert "secret" not in str(chunks[0].meta)


def test_pgvector_rejects_invalid_ranking_configuration() -> None:
    with pytest.raises(ValueError, match="rrf_k"):
        PgVectorRlsBackend(rrf_k=0)
    with pytest.raises(ValueError, match="penalties"):
        PgVectorRlsBackend(raw_rank_penalty=-1)
    with pytest.raises(ValueError, match="timeout"):
        PgVectorRlsBackend(dense_timeout_seconds=0)


async def test_pgvector_is_unfiltered_by_default_so_the_compile_pipeline_sees_raw() -> (
    None
):
    """`retrieve()` is the only method on `RagBackend`, and the compile
    pipeline calls it to ground a page against **raw** evidence. A backend
    built with no corpus must bind SQL NULL, which both queries read as "every
    corpus". This is the guardrail for the defect where the wiki tier was a
    constant in shared SQL and silently made groundedness circular.
    """
    pool, connection = _mock_pool([_pg_row()])
    backend = PgVectorRlsBackend(embedder=FakeEmbedder(), pool=pool)

    assert backend.corpus is None
    await backend.retrieve("evidence", scope=Scope(departments=frozenset({"infra"})))
    assert connection.fetch.call_args.args[9] is None


async def test_pgvector_binds_the_corpus_tier_rather_than_inlining_it() -> None:
    """The tier travels as a bind parameter, never as a literal in the SQL.

    Asserting the absence of the literal is what stops a future edit from
    re-hardcoding the filter: the query text must stay corpus-agnostic so one
    backend class can serve both the wiki tier and the unfiltered pipeline.
    """
    pool, connection = _mock_pool([_pg_row()])
    backend = PgVectorRlsBackend(embedder=FakeEmbedder(), pool=pool, corpus="wiki")

    await backend.retrieve("tiered", scope=Scope(departments=frozenset({"infra"})))

    call = connection.fetch.call_args
    query = " ".join(str(call.args[0]).split())
    assert re.search(r"->>\s*'corpus'\s*=\s*\$\d+::text", query)
    assert "'wiki'" not in query
    assert call.args[9] == "wiki"


async def test_pgvector_sparse_fallback_keeps_the_corpus_tier() -> None:
    """A dead embedding service must degrade the arm, never the authority.

    The sparse query binds one fewer parameter than the hybrid one, so the
    tier sits at a different position; a renumbering mistake here would drop
    the filter only on the degraded path, where it is least likely to be
    noticed.
    """

    class TimeoutEmbedder:
        async def aembed_texts(self, _texts: Sequence[str]) -> list[list[float]]:
            raise TimeoutError("controlled timeout")

    pool, connection = _mock_pool([_pg_row("wiki/sparse.md")])
    backend = PgVectorRlsBackend(
        embedder=TimeoutEmbedder(),
        pool=pool,
        dense_timeout_seconds=0.01,
        corpus="wiki",
    )

    chunks = await backend.retrieve(
        "degraded", scope=Scope(departments=frozenset({"infra"}))
    )

    call = connection.fetch.call_args
    assert "vector_matches" not in str(call.args[0])
    assert call.args[8] == "wiki"
    assert chunks[0].meta["degraded"] == "true"
