"""Production Scout backend selection tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scout.serve import _build_production_backend


def test_production_server_rejects_fake_backend() -> None:
    with pytest.raises(ValueError, match="pgvector"):
        _build_production_backend("fake")


def test_production_server_builds_pgvector_only() -> None:
    sentinel = object()
    with patch("scout.backends.pgvector.PgVectorRlsBackend", return_value=sentinel):
        assert _build_production_backend("pgvector") is sentinel


def test_production_server_serves_only_the_wiki_tier() -> None:
    """The served surface is `wiki_search` and `wiki_read` and nothing else, so
    the backend behind it must be built on the wiki corpus.

    Asserting the keyword rather than just the class is what stops the tier
    from being deleted here unnoticed: `PgVectorRlsBackend()` defaults to every
    corpus, which is correct for the compile pipeline and wrong for the
    authenticated surface the demo talks to.
    """
    with patch("scout.backends.pgvector.PgVectorRlsBackend") as constructed:
        _build_production_backend("pgvector")
    assert constructed.call_args.kwargs.get("corpus") == "wiki"


# ── the startup guard: one vector space, or refuse to serve ────────────────


class _Census:
    """A connection that answers only the model census."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.queries.append(query)
        return self.rows


@pytest.mark.asyncio
async def test_startup_guard_rejects_a_mixed_index() -> None:
    """Two vector spaces in one index is F-2; it must be fatal, not silent."""
    from scout.serve import assert_single_embedding_model

    conn = _Census([{"model": "snp-embed", "n": 2176}, {"model": None, "n": 127}])
    with pytest.raises(RuntimeError, match="unstamped"):
        await assert_single_embedding_model(conn, expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_rejects_a_model_the_process_cannot_query_with() -> None:
    """An index built by another model returns near-random order, with no error."""
    from scout.serve import assert_single_embedding_model

    conn = _Census([{"model": "some/other-model", "n": 2303}])
    with pytest.raises(RuntimeError, match="some/other-model"):
        await assert_single_embedding_model(conn, expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_rejects_two_stamped_models() -> None:
    from scout.serve import assert_single_embedding_model

    conn = _Census(
        [{"model": "snp-embed", "n": 2000}, {"model": "fastembed/bge", "n": 300}]
    )
    with pytest.raises(RuntimeError, match="fastembed/bge"):
        await assert_single_embedding_model(conn, expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_refuses_an_empty_index() -> None:
    """A census that sees nothing has not observed its subject.

    Serving on an empty census would let the guard pass in exactly the state it
    cannot distinguish from fail-closed RLS reading zero rows.
    """
    from scout.serve import assert_single_embedding_model

    with pytest.raises(RuntimeError, match="no chunks"):
        await assert_single_embedding_model(_Census([]), expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_accepts_a_single_matching_model() -> None:
    from scout.serve import assert_single_embedding_model

    conn = _Census([{"model": "snp-embed", "n": 2101}])
    await assert_single_embedding_model(conn, expected_model="snp-embed")


def test_the_guard_expects_what_the_served_backend_actually_embeds_with() -> None:
    """The guard compares the index against the *query* route.

    Reading `LITELLM_EMBED_MODEL` here instead -- the variable that configures
    the gateway -- would refuse to start against a correctly built index, because
    the backend's own embedder never uses it.
    """
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.chunker import configured_embedding_model
    from scout.serve import _expected_embedding_model

    backend = PgVectorRlsBackend(corpus="wiki")
    assert _expected_embedding_model() == configured_embedding_model()
    assert _expected_embedding_model() == backend.embedder.model


# ── /healthz reports the dense arm, not just an open socket ─────────────────


async def test_health_route_goes_503_while_the_dense_arm_is_dead() -> None:
    """The compose healthcheck used to open a TCP socket and nothing more.

    A scout whose embedder route or key was wrong answered every query from the
    sparse arm and still read healthy, so `snpmemory status` -- which rolls an
    unhealthy service into `degraded` -- had nothing to report.
    """
    import httpx
    from fastmcp import FastMCP

    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.serve import add_health_route
    from tests.fakes import FakeEmbedder

    class _Refusing(FakeEmbedder):
        async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
            raise ConnectionRefusedError("refused")

    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    backend = PgVectorRlsBackend(embedder=_Refusing(), pool=pool)
    server = FastMCP("health-test")
    add_health_route(server, backend)
    transport = httpx.ASGITransport(app=server.http_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        # No query has tried the arm yet: nothing is known to be wrong.
        fresh = await client.get("/healthz")
        assert fresh.status_code == 200
        assert fresh.json()["dense"] == "unknown"

        await backend.retrieve("an agent's query")
        down = await client.get("/healthz")
        assert down.status_code == 503
        assert down.json()["dense"] == "degraded"
        assert down.json()["last_error"] == "ConnectionRefusedError"

        # While degraded the route re-probes, so recovery is seen without
        # waiting for the next agent query to succeed.
        backend.embedder = FakeEmbedder()
        up = await client.get("/healthz")
        assert up.status_code == 200
        assert up.json()["dense"] == "ok"
        assert up.json()["degraded_total"] == 1


async def test_health_reprobe_is_bounded_inside_the_healthcheck_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hanging gateway must not hold the route past compose's 3 s timeout."""
    import asyncio

    import httpx
    from fastmcp import FastMCP

    from scout import serve
    from scout.backends.pgvector import PgVectorRlsBackend
    from tests.fakes import FakeEmbedder

    class _Hanging(FakeEmbedder):
        async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(60)
            return self.embed_texts(texts)

    backend = PgVectorRlsBackend(embedder=_Hanging(), pool=MagicMock())
    backend.health.record_degraded("embedding_error", None, retrieval=True)
    monkeypatch.setattr(serve, "_HEALTH_PROBE_TIMEOUT_SECONDS", 0.05)
    server = FastMCP("health-test")
    serve.add_health_route(server, backend)
    transport = httpx.ASGITransport(app=server.http_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await asyncio.wait_for(client.get("/healthz"), timeout=2)
    assert response.status_code == 503
