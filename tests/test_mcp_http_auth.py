"""Actual FastMCP Streamable HTTP authentication boundary tests."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from scout.auth import load_auth_config
from scout.diy_engine import ScoutDiyEngine
from scout.mcp_server import build_server
from scout.types import RagBackend, RagChunk, Scope
from tests.fakes import FakeEmbedder


class HttpRecordingBackend(RagBackend):
    def __init__(self) -> None:
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
        return [RagChunk(text="verbatim", file_path=path or "raw/a.md")]


def _server(
    wiki_dir: Path | None = None,
) -> tuple[Any, HttpRecordingBackend]:
    backend = HttpRecordingBackend()
    config = load_auth_config(
        {
            "SCOUT_AUTH_MODE": "static",
            "SCOUT_AUTH_BASE_URL": "http://scout.test",
            "SCOUT_STATIC_TOKENS": (
                '{"valid-token":{"subject":"http-client",'
                '"departments":["infra","ai_eng"]}}'
            ),
        }
    )
    engine = (
        ScoutDiyEngine.from_vault(
            FakeEmbedder(), wiki_dir=wiki_dir, rag_backend=backend
        )
        if wiki_dir is not None
        else None
    )
    return build_server(backend, auth_config=config, wiki_engine=engine), backend


def _write_page(root: Path) -> None:
    page = root / "concepts" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        """---
title: Protected Page
type: concept
updated: 2026-08-31
sources: []
---
# Protected Page

## TL;DR
Canonical protected read.
""",
        encoding="utf-8",
    )


def _jwt_server() -> tuple[Any, HttpRecordingBackend, str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    backend = HttpRecordingBackend()
    config = load_auth_config(
        {
            "SCOUT_AUTH_MODE": "jwt",
            "SCOUT_AUTH_BASE_URL": "http://scout.test",
            "SCOUT_JWT_ALGORITHM": "RS256",
            "SCOUT_JWT_PUBLIC_KEY": public_pem,
            "SCOUT_JWT_ISSUER": "https://issuer.example.test",
            "SCOUT_JWT_AUDIENCE": "scout",
            "SCOUT_JWT_DEPARTMENT_CLAIM": "departments",
        }
    )
    return build_server(backend, auth_config=config), backend, private_pem, public_pem


def _encode_jwt(private_key: str, **overrides: object) -> str:
    claims: dict[str, object] = {
        "sub": "jwt-client",
        "iss": "https://issuer.example.test",
        "aud": "scout",
        "exp": int(time.time()) + 300,
        "departments": ["infra", "ai_eng"],
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


def _factory(app: Any) -> Callable[..., httpx.AsyncClient]:
    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("follow_redirects", None)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://scout.test",
            follow_redirects=True,
            **kwargs,
        )

    return factory


async def test_missing_or_malformed_bearer_is_http_401_before_backend() -> None:
    server, backend = _server()
    app = server.http_app(stateless_http=True)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    async with (
        app.lifespan(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://scout.test"
        ) as client,
    ):
        missing = await client.post("/mcp", json=request)
        malformed = await client.post(
            "/mcp", json=request, headers={"Authorization": "Basic invalid"}
        )
        invalid = await client.post(
            "/mcp", json=request, headers={"Authorization": "Bearer invalid"}
        )
    assert (missing.status_code, malformed.status_code, invalid.status_code) == (
        401,
        401,
        401,
    )
    assert backend.calls == []


async def test_authorization_header_reaches_current_access_token_and_can_narrow() -> (
    None
):
    server, backend = _server()
    app = server.http_app(stateless_http=True)
    transport = StreamableHttpTransport(
        "http://scout.test/mcp",
        auth="valid-token",
        httpx_client_factory=_factory(app),
    )
    async with app.lifespan(app), Client(transport) as client:
        result = await client.call_tool(
            "wiki_search",
            {"query": "text", "department": "infra"},
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["results"][0]["path"] == "raw/a.md"
    assert backend.calls == [Scope(departments=frozenset({"infra"}))]


async def test_protected_http_wiki_read_returns_canonical_payload(
    tmp_path: Path,
) -> None:
    _write_page(tmp_path)
    server, backend = _server(tmp_path)
    app = server.http_app(stateless_http=True)
    transport = StreamableHttpTransport(
        "http://scout.test/mcp",
        auth="valid-token",
        httpx_client_factory=_factory(app),
    )
    async with app.lifespan(app), Client(transport) as client:
        result = await client.call_tool(
            "wiki_read",
            {
                "path": "concepts/page.md",
                "mode": "tldr",
                "department": "infra",
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["path"] == "concepts/page.md"
    assert result.structured_content["tldr"] == "Canonical protected read."
    assert set(result.structured_content) == {
        "path",
        "title",
        "type",
        "tldr",
        "content_hash",
    }
    assert backend.calls == []


async def test_forbidden_department_is_tool_error_and_never_calls_backend() -> None:
    server, backend = _server()
    app = server.http_app(stateless_http=True)
    transport = StreamableHttpTransport(
        "http://scout.test/mcp",
        auth="valid-token",
        httpx_client_factory=_factory(app),
    )
    async with app.lifespan(app), Client(transport) as client:
        with pytest.raises(ToolError, match="authenticated scope"):
            await client.call_tool(
                "wiki_search",
                {"query": "text", "department": "redteam"},
            )
    assert backend.calls == []


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": int(time.time()) - 60},
        {"aud": "wrong-audience"},
        {"departments": ["all"]},
    ],
)
async def test_invalid_jwt_claims_are_http_401_before_backend(
    claim_overrides: dict[str, object],
) -> None:
    server, backend, private_key, _ = _jwt_server()
    app = server.http_app(stateless_http=True)
    encoded = _encode_jwt(private_key, **claim_overrides)
    transport = StreamableHttpTransport(
        "http://scout.test/mcp",
        auth=encoded,
        httpx_client_factory=_factory(app),
    )
    async with app.lifespan(app):
        with pytest.raises(Exception, match="401"):
            async with Client(transport):
                pass
    assert backend.calls == []


async def test_valid_jwt_reaches_current_access_token_and_narrows() -> None:
    server, backend, private_key, _ = _jwt_server()
    app = server.http_app(stateless_http=True)
    transport = StreamableHttpTransport(
        "http://scout.test/mcp",
        auth=_encode_jwt(private_key),
        httpx_client_factory=_factory(app),
    )
    async with app.lifespan(app), Client(transport) as client:
        result = await client.call_tool(
            "wiki_search",
            {"query": "text", "department": "ai_eng"},
        )
    assert not result.is_error
    assert backend.calls == [Scope(departments=frozenset({"ai_eng"}))]
