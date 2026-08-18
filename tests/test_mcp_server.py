"""Tests for scout.mcp_server — the agent's only door into RAG (R-4.1, R-4.2).

Exercises `rag_fetch_tool` directly against `FakeRagBackend` (no real MCP
server or transport is started) and checks the fixed response shape has no
``"action"``/``"command"`` field anywhere (injection guard, R-8.5).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastmcp import FastMCP

from scout.backends.fake import FakeRagBackend
from scout.mcp_server import build_server, rag_fetch_tool
from scout.types import RagChunk


@pytest.fixture(autouse=True)
def _default_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_AUTH_MODE", "development")



def _walk(value: object) -> list[object]:
    """Flatten a JSON-like structure into every dict/list/scalar node."""
    nodes: list[object] = [value]
    if isinstance(value, dict):
        for v in value.values():
            nodes.extend(_walk(v))
    elif isinstance(value, list):
        for item in value:
            nodes.extend(_walk(item))
    return nodes


def _assert_no_action_or_command(payload: dict[str, object]) -> None:
    """Assert no dict anywhere in `payload` has an "action"/"command" key."""
    for node in _walk(payload):
        if isinstance(node, dict):
            assert "action" not in node
            assert "command" not in node


async def test_rag_fetch_tool_ok_returns_only_addressed_file_context(
    backend: FakeRagBackend,
) -> None:
    result = await rag_fetch_tool(
        backend, path="raw/reports/acme.pdf", hint="kerberoasting service account"
    )

    assert result["status"] == "ok"
    context = result["context"]
    citations = result["citations"]
    assert isinstance(context, list) and isinstance(citations, list)
    assert len(context) == len(citations) == 2
    assert all(item["file_path"] == "raw/reports/acme.pdf" for item in context)
    assert all(item["file_path"] == "raw/reports/acme.pdf" for item in citations)
    # verbatim text is carried through, not summarized
    assert any("Kerberoasting" in item["text"] for item in context)
    _assert_no_action_or_command(result)


async def test_rag_fetch_tool_no_source_when_hint_matches_other_file(
    backend: FakeRagBackend,
) -> None:
    """The hint best-matches a chunk in another file; the address points at a
    file with no matching chunk → no_source, context/citations empty (R-4.5)."""
    result = await rag_fetch_tool(
        backend, path="raw/nonexistent.pdf", hint="ESC8 NTLM relay"
    )

    assert result["status"] == "no_source"
    assert result["context"] == []
    assert result["citations"] == []
    _assert_no_action_or_command(result)


async def test_rag_fetch_tool_no_source_on_empty_backend() -> None:
    empty_backend = FakeRagBackend(chunks=[])
    result = await rag_fetch_tool(empty_backend, path="raw/a.pdf", hint="anything")

    assert result["status"] == "no_source"
    assert result["context"] == []
    assert result["citations"] == []
    _assert_no_action_or_command(result)


async def test_rag_fetch_tool_carries_loc_into_citations() -> None:
    chunk = RagChunk(text="body text", file_path="raw/a.pdf", score=1.0)  # no loc
    single_backend = FakeRagBackend(chunks=[chunk])

    result = await rag_fetch_tool(
        single_backend, path="raw/a.pdf", hint="body text", loc="p.42"
    )

    assert result["status"] == "ok"
    context = cast("list[dict[str, object]]", result["context"])
    citations = cast("list[dict[str, object]]", result["citations"])
    assert context[0]["loc"] == "p.42"
    assert citations[0]["loc"] == "p.42"
    _assert_no_action_or_command(result)


async def test_rag_fetch_tool_returns_plain_json_serializable_dict(
    backend: FakeRagBackend,
) -> None:
    """The schema is fixed to text + provenance only (design.md §2.3)."""
    result = await rag_fetch_tool(
        backend, path="raw/reports/acme.pdf", hint="kerberoasting"
    )

    assert set(result.keys()) == {"status", "context", "citations"}
    context = cast("list[dict[str, object]]", result["context"])
    citations = cast("list[dict[str, object]]", result["citations"])
    for item in context:
        assert set(item.keys()) == {"text", "file_path", "loc"}
    for item in citations:
        assert set(item.keys()) == {"file_path", "loc", "score"}


def test_build_server_returns_fastmcp_instance(backend: FakeRagBackend) -> None:
    mcp = build_server(backend)
    assert isinstance(mcp, FastMCP)


async def test_build_server_registers_rag_fetch_tool_only(
    backend: FakeRagBackend,
) -> None:
    mcp = build_server(backend)
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]

    assert names == ["rag_fetch"]


async def test_build_server_tool_delegates_to_backend(backend: FakeRagBackend) -> None:
    """The registered `rag_fetch` tool must actually call through to Scout
    core / the injected backend, not just exist as a stub."""
    mcp = build_server(backend, allowed_depts=["all", "redteam"])
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "rag_fetch")

    result: Any = await tool.run(
        {
            "path": "raw/reports/acme.pdf",
            "hint": "kerberoasting service account",
            "department": "redteam",
        }
    )
    payload = result.structured_content

    assert payload["status"] == "ok"
    assert all(c["file_path"] == "raw/reports/acme.pdf" for c in payload["context"])
    _assert_no_action_or_command(payload)


async def test_rag_fetch_tool_authenticated_unauthorized_dept_rejected(
    backend: FakeRagBackend,
    monkeypatch: Any,
) -> None:
    """Denial test: Requesting an unauthorized department returns fail-closed no_source."""
    monkeypatch.setenv("SCOUT_AUTH_MODE", "authenticated")
    monkeypatch.setenv("SCOUT_ALLOWED_DEPTS", "redteam,infra")

    # blueteam is not in allowed depts
    result = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
        department="blueteam",
    )

    assert result["status"] == "no_source"
    assert result["context"] == []
    assert result["citations"] == []


async def test_rag_fetch_tool_authenticated_empty_env_fails_closed(
    backend: FakeRagBackend,
    monkeypatch: Any,
) -> None:
    """In authenticated mode with no allowed depts configured, calls fail closed."""
    monkeypatch.setenv("SCOUT_AUTH_MODE", "authenticated")
    monkeypatch.delenv("SCOUT_ALLOWED_DEPTS", raising=False)

    result = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
    )

    assert result["status"] == "no_source"
    assert result["context"] == []
    assert result["citations"] == []


async def test_rag_fetch_tool_authenticated_narrowing_valid_scope(
    backend: FakeRagBackend,
    monkeypatch: Any,
) -> None:
    """Narrowing to an authorized department succeeds."""
    monkeypatch.setenv("SCOUT_AUTH_MODE", "authenticated")
    monkeypatch.setenv("SCOUT_ALLOWED_DEPTS", "redteam,infra")

    # Authorized department
    result = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
        department="redteam",
    )
    assert result["status"] == "ok"

    # List of departments with partial match narrows to authorized
    result_multi = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
        department=["redteam", "unauthorized_dept"],
    )
    assert result_multi["status"] == "ok"


async def test_rag_fetch_tool_development_mode(
    backend: FakeRagBackend,
    monkeypatch: Any,
) -> None:
    """In development mode, requests default to 'all' or the requested department."""
    monkeypatch.setenv("SCOUT_AUTH_MODE", "development")
    monkeypatch.delenv("SCOUT_ALLOWED_DEPTS", raising=False)

    # Default call without department
    res_default = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
    )
    assert res_default["status"] == "ok"

    # Call with requested department
    res_dept = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
        department="custom_dev_dept",
    )
    assert res_dept["status"] == "ok"


async def test_rag_fetch_tool_explicit_allowed_depts_injection(
    backend: FakeRagBackend,
) -> None:
    """Explicitly passing allowed_depts takes precedence over environment."""
    result = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
        department="redteam",
        allowed_depts={"redteam"},
    )
    assert result["status"] == "ok"

    unauth = await rag_fetch_tool(
        backend,
        path="raw/reports/acme.pdf",
        hint="kerberoasting service account",
        department="other",
        allowed_depts={"redteam"},
    )
    assert unauth["status"] == "no_source"

