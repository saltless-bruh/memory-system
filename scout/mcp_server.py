"""Authenticated FastMCP boundary for Scout's single RAG retrieval tool."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken

from scout.auth import (
    AuthConfig,
    AuthMode,
    CallerIdentity,
    access_token_to_identity,
    load_auth_config,
    resolve_authorized_scope,
)
from scout.core import rag_fetch
from scout.types import Address, RagBackend

_CURRENT_ACCESS_TOKEN: Final[AccessToken] = CurrentAccessToken()


async def rag_fetch_tool(
    backend: RagBackend,
    *,
    identity: CallerIdentity,
    path: str,
    hint: str,
    loc: str | None = None,
    department: str | list[str] | None = None,
) -> dict[str, object]:
    """Retrieve one addressed source using only a verified caller identity.

    Authentication is intentionally absent from this helper. HTTP credentials are
    accepted and verified only by FastMCP's auth provider before this function can
    run. ``department`` may narrow the verified identity and can never expand it.
    """
    scope = resolve_authorized_scope(identity, department)
    result = await rag_fetch(
        backend,
        Address(path=path, hint=hint, loc=loc),
        scope=scope,
    )
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
    *,
    auth_config: AuthConfig | None = None,
) -> FastMCP:
    """Build Scout with FastMCP-native auth and exactly one registered tool."""
    config = auth_config or load_auth_config()

    @asynccontextmanager
    async def backend_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
        """Close backend resources exactly when the deployed server stops."""
        try:
            yield {}
        finally:
            close = getattr(backend, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result

    mcp: FastMCP = FastMCP(
        name,
        auth=config.provider,
        mask_error_details=True,
        lifespan=backend_lifespan,
    )

    if config.mode is AuthMode.DEVELOPMENT:
        identity = config.development_identity
        if identity is None:  # defensive: validated configs always provide it
            raise RuntimeError("development mode is missing its server identity")

        @mcp.tool(name="rag_fetch")
        async def rag_fetch_endpoint(
            path: str,
            hint: str,
            loc: str | None = None,
            department: str | list[str] | None = None,
        ) -> dict[str, object]:
            return await rag_fetch_tool(
                backend,
                identity=identity,
                path=path,
                hint=hint,
                loc=loc,
                department=department,
            )

    else:
        if config.provider is None:  # defensive: protected configs need a provider
            raise RuntimeError("protected auth mode is missing its token verifier")

        @mcp.tool(name="rag_fetch")
        async def rag_fetch_endpoint(
            path: str,
            hint: str,
            loc: str | None = None,
            department: str | list[str] | None = None,
            access_token: AccessToken = _CURRENT_ACCESS_TOKEN,
        ) -> dict[str, object]:
            identity = access_token_to_identity(access_token)
            return await rag_fetch_tool(
                backend,
                identity=identity,
                path=path,
                hint=hint,
                loc=loc,
                department=department,
            )

    return mcp
