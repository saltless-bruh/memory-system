#!/usr/bin/env python3
"""serve.py — deploy-time entry point for the Scout MCP server (T-3.8).

`scout.mcp_server.build_server` is dependency-injected. This module is the
wiring the Compose ``scout`` service actually runs: it builds the PostgreSQL
+ pgvector backend and serves the authenticated ``rag_fetch`` tool over MCP
Streamable HTTP for the member's client.

Config is env-driven so nothing is hardcoded:

    SCOUT_HOST  bind host               (default 0.0.0.0)
    SCOUT_PORT  bind port               (default 8080)

Authentication and database settings are validated by ``scout.auth`` and
``scout.config`` before their respective resources are used.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, Protocol

from fastmcp import FastMCP

from scout.auth import load_auth_config
from scout.chunker import configured_embedding_model
from scout.config import postgres_settings
from scout.mcp_server import build_server
from scout.types import RagBackend

logger = logging.getLogger(__name__)


def _build_production_backend(backend_choice: str) -> RagBackend:
    """Build only the production pgvector backend; test fakes use injection."""
    if backend_choice.strip().lower() != "pgvector":
        raise ValueError("production RAG_BACKEND must be pgvector")
    from scout.backends.pgvector import PgVectorRlsBackend

    return PgVectorRlsBackend(corpus="wiki")


#: The re-probe's budget. The compose healthcheck allows 3 s end to end and a
#: pinned test holds it there, so the embed must finish well inside that; the
#: measured single-query latency is 407-611 ms (scout/backends/pgvector.py).
_HEALTH_PROBE_TIMEOUT_SECONDS = 2.0


def add_health_route(server: FastMCP, backend: RagBackend) -> None:
    """Serve `GET /healthz`: 200 while retrieval is whole, 503 while degraded.

    The compose healthcheck used to open a TCP socket and stop, so it measured
    "the process is listening" and nothing about what it answers with. A scout
    whose embedding URL or key was wrong served every query from the relaxed
    sparse arm and read healthy the whole time -- and `snpmemory status` takes
    its verdict from compose health, so it said `ok` too.

    The route is passive while the dense arm reads `ok` or `unknown`: it costs
    no embedding call, and a scout that has not been queried yet has nothing
    known to be wrong. Only while it reads `degraded` does it re-probe, so a
    recovered gateway turns the service healthy again without waiting for an
    agent's query. It is unauthenticated, like every custom route in FastMCP,
    and so returns states, counts and exception class names only.

    A backend with no dense arm to report -- an injected test fake -- is
    healthy by this measure; the socket check it replaced said no more.
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    report = getattr(backend, "dense_health", None)
    probe = getattr(backend, "probe_dense", None)

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> JSONResponse:
        if report is None:
            return JSONResponse({"dense": "not_applicable"})
        body = report()
        if body["dense"] == "degraded" and probe is not None:
            # A probe cut off here records nothing, so the arm still reads
            # degraded and the answer is the honest 503.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(_HEALTH_PROBE_TIMEOUT_SECONDS):
                    await probe()
            body = report()
        status = 503 if body["dense"] == "degraded" else 200
        return JSONResponse(body, status_code=status)


class _Fetches(Protocol):
    """The one thing the guard needs from a connection."""

    async def fetch(self, query: str, *args: Any) -> Any: ...


#: Every distinct embedding model in the index, with its chunk count. Grouped
#: rather than sampled: one chunk from a second vector space is the whole
#: failure, and a sample would usually miss it.
_MODEL_CENSUS = """
    SELECT c.metadata->>'model' AS model, count(*) AS n
    FROM rag_chunks c
    GROUP BY 1
    ORDER BY 2 DESC;
"""


def _expected_embedding_model() -> str:
    """The route this process embeds *queries* with.

    Deliberately not `LITELLM_EMBED_MODEL`: that variable configures the
    gateway, and `PgVectorRlsBackend` builds its embedder without consulting it.
    Comparing the index against it would refuse to serve a correctly built
    index -- the guard would be the outage.
    """
    return configured_embedding_model()


async def assert_single_embedding_model(conn: _Fetches, *, expected_model: str) -> None:
    """Refuse to serve an index built by more than one embedding model.

    Cross-space retrieval is the F-2 failure: a query embedded at 1024
    dimensions and scored against another model's vectors returns near-random
    ordering **with no error**. It is indistinguishable from working, which is
    why it stops the process rather than being logged.

    An empty census is also fatal, because this guard must never succeed in a
    state where it cannot see its subject. `conn` runs as `rag_app_role`, the
    only role this service has credentials for -- it is the query door and must
    not hold ingest credentials -- so it is subject to the department policy: a
    connection that has not set `scout.current_depts` is fail-closed and reads
    zero rows from every table. That is indistinguishable from an empty
    database and has already produced two confident "the database is empty"
    diagnoses in this repository against an index holding 2303 chunks.

    The census is therefore of what this service *can serve*, not of every row
    in the table. That is the right question for it to ask: a chunk no query
    can reach cannot corrupt an answer.
    """
    rows = await conn.fetch(_MODEL_CENSUS)
    census = {row["model"]: int(row["n"]) for row in rows}
    if not census:
        raise RuntimeError(
            "refusing to serve: the index reports no chunks at all. Either it "
            "is empty, or this connection never set scout.current_depts and is "
            "reading zero rows under fail-closed RLS."
        )
    unstamped = census.pop(None, 0)
    if unstamped:
        raise RuntimeError(
            f"refusing to serve: {unstamped} unstamped chunks in the index; "
            "re-ingest them so the embedding model is recorded"
        )
    if set(census) != {expected_model}:
        raise RuntimeError(
            f"refusing to serve: index holds {sorted(census)} but this process "
            f"embeds queries with {expected_model!r}"
        )


async def _check_index_coherence() -> None:  # pragma: no cover - needs a database
    """Run the startup guard against the live index, then close the connection.

    Connects the way the backend does -- the query role, the only credentials
    this service holds. Nothing here writes, and nothing here needs to.
    """
    import asyncpg

    from scout.policy import CANONICAL_DEPARTMENTS

    settings = postgres_settings("query", env=os.environ)
    conn = await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )
    try:
        # Load-bearing: without it every policy denies and the census comes
        # back empty, which the guard reports rather than passing on.
        await conn.execute(
            "SELECT set_config('scout.current_depts', $1, false);",
            ",".join(sorted(CANONICAL_DEPARTMENTS)),
        )
        await assert_single_embedding_model(
            conn, expected_model=_expected_embedding_model()
        )
    finally:
        await conn.close()


def main() -> None:  # pragma: no cover - deploy wiring (needs a live transport)
    """Wire the chosen backend + MCP transport and serve `rag_fetch`."""
    # Without a handler the root logger drops everything below WARNING and
    # prints the rest with no timestamp or logger name, so the two INFO lines
    # below never reached `docker compose logs` and a degraded-retrieval warning
    # could not be placed in time. Configured here, in the entry point, so
    # importing `scout.serve` from a test leaves the caller's logging alone.
    logging.basicConfig(
        level=os.environ.get("SCOUT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("SCOUT_HOST", "0.0.0.0")
    auth_config = load_auth_config(bind_host=host)
    logger.info("Starting Scout with %s authentication", auth_config.mode.value)
    backend = _build_production_backend(os.environ.get("RAG_BACKEND", "pgvector"))

    # Before the socket, not after: an incoherent index answers every query
    # with plausible nonsense, so refusing to bind is the only honest failure.
    asyncio.run(_check_index_coherence())
    logger.info("Index coherence verified: one embedding model")

    server = build_server(backend, auth_config=auth_config)
    add_health_route(server, backend)
    server.run(
        transport="http",
        host=host,
        port=int(os.environ.get("SCOUT_PORT", "8080")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
