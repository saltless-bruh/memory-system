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

import logging
import os

from scout.auth import load_auth_config
from scout.mcp_server import build_server
from scout.types import RagBackend

logger = logging.getLogger(__name__)


def _build_production_backend(backend_choice: str) -> RagBackend:
    """Build only the production pgvector backend; test fakes use injection."""
    if backend_choice.strip().lower() != "pgvector":
        raise ValueError("production RAG_BACKEND must be pgvector")
    from scout.backends.pgvector import PgVectorRlsBackend

    return PgVectorRlsBackend()


def main() -> None:  # pragma: no cover - deploy wiring (needs a live transport)
    """Wire the chosen backend + MCP transport and serve `rag_fetch`."""
    host = os.environ.get("SCOUT_HOST", "0.0.0.0")
    auth_config = load_auth_config(bind_host=host)
    logger.info("Starting Scout with %s authentication", auth_config.mode.value)
    backend = _build_production_backend(os.environ.get("RAG_BACKEND", "pgvector"))

    server = build_server(backend, auth_config=auth_config)
    server.run(
        transport="http",
        host=host,
        port=int(os.environ.get("SCOUT_PORT", "8080")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
