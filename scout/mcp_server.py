"""Agent-facing MCP layer for Scout — the only door into RAG (R-4.1, R-4.2).

Exposes exactly one tool to the agent: ``rag_fetch``. PostgreSQL 16 + pgvector (or any
other `RagBackend` adapter) is never reachable from the agent directly — the
agent can only ask Scout, and Scout is the sole caller of the backend
(design.md §2.3, R-4.2). No raw RAG query is exposed here.

The tool's LOGIC (`rag_fetch_tool`) is a plain async function, deliberately
kept separate from the FastMCP registration (`build_server`) so it can be
unit-tested directly — no server, no transport, no network. Its return shape
is fixed to quotes + citations only; there is no ``"action"``/``"command"``
field, which is the structural half of the prompt-injection guard (R-8.5).

A real `RagBackend` (such as `PgVectorRlsBackend`) and an actual
MCP transport are chosen at deploy time — `build_server` takes whatever
backend it is given (dependency injection).
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from fastmcp import FastMCP

from scout.core import rag_fetch
from scout.types import Address, RagBackend, Scope

SCOUT_AUTH_MODE = os.environ.get("SCOUT_AUTH_MODE", "authenticated").lower()


def _resolve_scope(
    department: str | list[str] | None,
    allowed_depts: set[str] | Sequence[str] | None = None,
) -> Scope | None:
    """Derive effective authorized scope from server config and requested department."""
    auth_mode = os.environ.get("SCOUT_AUTH_MODE", "authenticated").lower()

    if allowed_depts is not None:
        base_allowed = set(allowed_depts)
    else:
        env_depts = os.environ.get("SCOUT_ALLOWED_DEPTS", "")
        if env_depts.strip():
            base_allowed = {d.strip() for d in env_depts.split(",") if d.strip()}
        elif auth_mode == "development":
            base_allowed = {"all"}
        else:
            base_allowed = set()

    if department is not None:
        if isinstance(department, str):
            requested = {department.strip()} if department.strip() else set()
        else:
            requested = {d.strip() for d in department if d.strip()}

        if (
            auth_mode == "development"
            and not os.environ.get("SCOUT_ALLOWED_DEPTS", "").strip()
            and allowed_depts is None
        ):
            effective = requested if requested else {"all"}
        else:
            effective = base_allowed & requested
    else:
        effective = base_allowed

    if not effective:
        return None

    return Scope(roles=frozenset(effective))


async def rag_fetch_tool(
    backend: RagBackend,
    path: str,
    hint: str,
    loc: str | None = None,
    department: str | list[str] | None = None,
    allowed_depts: set[str] | Sequence[str] | None = None,
) -> dict[str, object]:
    """Resolve one wiki `sources[]` address to verbatim quotes + citations.

    This is the tool's actual logic, independent of FastMCP registration
    (design.md §2.3): it builds an `Address` from the raw arguments and
    delegates to `scout.core.rag_fetch` — the only path an agent has into
    RAG (R-4.1, R-4.2).

    Args:
        backend: The injected `RagBackend` adapter (real backend wired at
            deploy time; see `build_server`).
        path: The `raw/` file this address must resolve to.
        hint: The phrase RAG matches on to retrieve the passage.
        loc: Optional human locator (e.g. ``"p.12-14"``) carried into
            citations.
        department: Optional department name or list of department names
            to scope retrieval.
        allowed_depts: Optional server-configured allowed departments. If
            omitted, defaults to `SCOUT_ALLOWED_DEPTS` or environment auth mode.

    Returns:
        A JSON-serializable dict shaped as::

            {"status": "ok" | "no_source",
             "context":   [{"text": ..., "file_path": ..., "loc": ...}, ...],
             "citations": [{"file_path": ..., "loc": ..., "score": ...}, ...]}

        Deliberately contains no ``"action"``/``"command"`` field —
        retrieved `raw/` content is treated as data, never instructions
        (injection guard, R-8.5). On ``"no_source"``, `context` and
        `citations` are both empty (R-4.5).
    """
    scope = _resolve_scope(department=department, allowed_depts=allowed_depts)
    if scope is None:
        return {
            "status": "no_source",
            "context": [],
            "citations": [],
        }

    address = Address(path=path, hint=hint, loc=loc)
    result = await rag_fetch(backend, address, scope=scope)
    return {
        "status": result.status.value,
        "context": [
            {"text": piece.text, "file_path": piece.file_path, "loc": piece.loc}
            for piece in result.context
        ],
        "citations": [
            {
                "file_path": citation.file_path,
                "loc": citation.loc,
                "score": citation.score,
            }
            for citation in result.citations
        ],
    }


def build_server(
    backend: RagBackend,
    name: str = "scout",
    allowed_depts: set[str] | Sequence[str] | None = None,
) -> FastMCP:
    """Build the Scout MCP server, exposing only `rag_fetch` to the agent.

    Args:
        backend: The `RagBackend` adapter to inject. This is dependency
            injection, not a hardcoded engine: the real RAG-Anything backend
            is wired in at deploy time (T-2.1); `build_server` itself never
            imports RAG-Anything and accepts whatever backend it is given.
        name: The MCP server name.
        allowed_depts: Optional server-configured allowed departments.

    Returns:
        A `FastMCP` instance with exactly one registered tool, `rag_fetch`.
        No raw RAG query is exposed — this is the sole door the agent has
        into RAG (R-4.1, R-4.2); RAG-Anything itself is never exposed.
    """
    mcp: FastMCP = FastMCP(name)

    @mcp.tool(name="rag_fetch")
    async def rag_fetch_endpoint(
        path: str,
        hint: str,
        loc: str | None = None,
        department: str | list[str] | None = None,
    ) -> dict[str, object]:
        """Fetch verbatim quotes + citations from RAG for one source address.

        Args:
            path: The `raw/` file this address must resolve to.
            hint: The phrase RAG matches on to retrieve the passage.
            loc: Optional human locator carried into citations.
            department: Optional department name or list of department names.

        Returns:
            See `rag_fetch_tool` for the exact response shape.
        """
        return await rag_fetch_tool(
            backend,
            path,
            hint,
            loc=loc,
            department=department,
            allowed_depts=allowed_depts,
        )

    return mcp


def main() -> None:
    """Deploy-time entry point (not exercised by the test suite).

    A real `RagBackend` (e.g. `scout.backends.rag_anything.RagAnythingBackend`,
    wired in T-2.1) and an MCP transport (stdio/HTTP — chosen at deploy time)
    must be constructed by the caller and passed to `build_server`, followed
    by ``mcp.run(...)``. This function intentionally does not do that itself
    so that importing `scout.mcp_server` never requires RAG-Anything/MinerU
    to be installed.
    """
    raise SystemExit(
        "scout.mcp_server has no default backend/transport wired in. "
        "Construct a RagBackend (e.g. RagAnythingBackend) and a transport "
        "at deploy time, then call build_server(backend).run(...)."
    )


if __name__ == "__main__":
    main()
