#!/usr/bin/env python3
"""serve.py — deploy-time entry point for the Scout MCP server (T-3.8).

`scout.mcp_server.build_server` is dependency-injected and deliberately left
un-wired — importing it must never require RAG-Anything (see its `main`). This
module is the wiring the compose ``scout`` service actually runs: it builds the
HTTP backend that points at the internal ``rag`` service (reachable only here,
never by the agent — R-4.2) and serves the single ``rag_fetch`` tool over an
MCP HTTP transport for the member's IDE.

Config is env-driven so nothing is hardcoded:

    RAG_URL     internal rag base URL   (default http://rag:8000)
    SCOUT_HOST  bind host               (default 0.0.0.0)
    SCOUT_PORT  bind port               (default 8080)
"""

from __future__ import annotations

import os

from scout.mcp_server import build_server


def main() -> None:  # pragma: no cover - deploy wiring (needs a live transport)
    """Wire the chosen backend + MCP transport and serve `rag_fetch`."""
    backend_choice = os.environ.get("RAG_BACKEND", "pgvector").lower()

    from scout.types import RagBackend

    backend: RagBackend
    if backend_choice == "fake":
        from scout.backends.fake import FakeRagBackend

        backend = FakeRagBackend(chunks=[])
    else:
        from scout.backends.pgvector import PgVectorRlsBackend

        backend = PgVectorRlsBackend()

    server = build_server(backend)
    server.run(
        transport="http",
        host=os.environ.get("SCOUT_HOST", "0.0.0.0"),
        port=int(os.environ.get("SCOUT_PORT", "8080")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
