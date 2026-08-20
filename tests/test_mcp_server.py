"""Scout MCP tool authorization and response-contract tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from scout.auth import (
    AuthConfig,
    AuthMode,
    AuthorizationError,
    CallerIdentity,
    load_auth_config,
)
from scout.backends.fake import FakeRagBackend
from scout.mcp_server import build_server, rag_fetch_tool
from scout.types import RagBackend, RagChunk, Scope


def _development_config() -> AuthConfig:
    return load_auth_config(
        {"SCOUT_AUTH_MODE": "development"}, bind_host="127.0.0.1"
    )


def _identity(*departments: str) -> CallerIdentity:
    return CallerIdentity(
        subject="caller-1",
        departments=frozenset(departments),
        auth_mode=AuthMode.STATIC,
    )


class RecordingBackend(RagBackend):
    def __init__(self, chunks: Sequence[RagChunk] = ()) -> None:
        self.chunks = chunks
        self.calls: list[Scope | None] = []

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        self.calls.append(scope)
        return self.chunks


async def test_rag_fetch_tool_returns_verbatim_context_and_citation(
    backend: FakeRagBackend,
) -> None:
    result = await rag_fetch_tool(
        backend,
        identity=_identity("redteam"),
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
    )
    assert result["status"] == "ok"
    context = cast("list[dict[str, object]]", result["context"])
    citations = cast("list[dict[str, object]]", result["citations"])
    assert len(context) == len(citations) == 2
    assert all(item["file_path"] == "raw/reports/acme.pdf" for item in context)
    assert any("Kerberoasting" in cast("str", item["text"]) for item in context)
    assert all("action" not in item and "command" not in item for item in context)


async def test_authenticated_no_evidence_is_the_only_no_source_case() -> None:
    backend = RecordingBackend()
    result = await rag_fetch_tool(
        backend,
        identity=_identity("infra"),
        path="raw/missing.pdf",
        hint="absent",
    )
    assert result == {"status": "no_source", "context": [], "citations": []}
    assert backend.calls == [Scope(departments=frozenset({"infra"}))]


async def test_department_request_can_narrow_verified_identity() -> None:
    backend = RecordingBackend()
    await rag_fetch_tool(
        backend,
        identity=_identity("infra", "ai_eng"),
        path="raw/a.md",
        hint="text",
        department="infra",
    )
    assert backend.calls == [Scope(departments=frozenset({"infra"}))]


@pytest.mark.parametrize(
    "requested",
    ["redteam", "all", "unknown", "", ["infra", "redteam"], []],
)
async def test_department_expansion_or_malformed_request_never_calls_backend(
    requested: str | list[str],
) -> None:
    backend = RecordingBackend()
    with pytest.raises(AuthorizationError):
        await rag_fetch_tool(
            backend,
            identity=_identity("infra"),
            path="raw/a.md",
            hint="text",
            department=requested,
        )
    assert backend.calls == []


def test_build_server_wires_native_auth_provider() -> None:
    token_mapping = (
        '{"opaque-token":{"subject":"automation","departments":["infra"]}}'
    )
    config = load_auth_config(
        {
            "SCOUT_AUTH_MODE": "static",
            "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
            "SCOUT_STATIC_TOKENS": token_mapping,
        }
    )
    server = build_server(RecordingBackend(), auth_config=config)
    assert isinstance(server, FastMCP)
    assert server.auth is config.provider


async def test_build_server_registers_only_rag_fetch() -> None:
    server = build_server(RecordingBackend(), auth_config=_development_config())
    tools = await server.list_tools()
    assert [tool.name for tool in tools] == ["rag_fetch"]


async def test_development_identity_is_injected_by_server_configuration() -> None:
    backend = RecordingBackend()
    server = build_server(backend, auth_config=_development_config())
    tool = (await server.list_tools())[0]
    result = await tool.run({"path": "raw/a.md", "hint": "text", "department": "infra"})
    assert result.structured_content == {
        "status": "no_source",
        "context": [],
        "citations": [],
    }
    assert backend.calls == [Scope(departments=frozenset({"infra"}))]


async def test_server_lifespan_closes_closeable_backend() -> None:
    backend = RecordingBackend()
    backend.close = AsyncMock()  # type: ignore[attr-defined]
    server = build_server(backend, auth_config=_development_config())
    app = server.http_app(stateless_http=True)

    async with app.lifespan(app):
        backend.close.assert_not_awaited()  # type: ignore[attr-defined]

    backend.close.assert_awaited_once_with()  # type: ignore[attr-defined]
