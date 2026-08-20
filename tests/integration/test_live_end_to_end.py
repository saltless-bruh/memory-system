"""Live end-to-end proofs for the wiki→RAG flow and the raw MCP JSON-RPC contract.

These tests replace `scripts/test_full_system.py` and `scripts/test_mcp_endpoints.py`,
which printed ``TEST SUCCESS`` unconditionally (audit finding M2). Every claim here
is asserted against the running stack:

* no ``FakeEmbedder`` — the wiki engine embeds through LiteLLM, exactly as production
  does, so a broken embedding route fails the test instead of being papered over;
* no silent fallback to an in-memory ``FakeRagBackend`` when the live call fails;
* no hardcoded bearer token — the integration token is read from the environment;
* no fabricated ``raw/rfcs/*`` fixtures — the corpus under test is ingested by the
  test and deleted again;
* HTTP 200 is never treated as proof: an MCP tool call is judged by its JSON-RPC
  **body**, and `test_live_mcp_jsonrpc_result_body_decides_success` pins the exact
  case the deleted script got wrong (a failed call returned with status 200).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from scout.backends.pgvector import PgVectorRlsBackend
from scout.chunker import LiteLLMBatchEmbedder
from scout.diy_engine import LiteLLMEmbedder, ScoutDiyEngine
from scout.ingest import get_pg_connection, ingest_document
from scout.types import Scope
from scout.workflow import AnswerStatus, answer_query

pytestmark = pytest.mark.integration

JSON_RPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"

# A deliberately unique fixture fact. It is ingested by the test and deleted in the
# same test, so a passing assertion can only come from live retrieval.
FIXTURE_FACT = (
    "The Kepler quorum relay acknowledges a write only after seven phases commit."
)


def _require_env(*names: str) -> None:
    """Fail with the names of missing prerequisites instead of skipping."""
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        pytest.fail(
            "live prerequisites are missing: " + ", ".join(missing), pytrace=False
        )


def _integration_token() -> str:
    """Read the integration bearer token; never fall back to a baked-in value."""
    direct = os.environ.get("SCOUT_INTEGRATION_INFRA_TOKEN", "").strip()
    if direct:
        return direct
    _require_env("SCOUT_INTEGRATION_INFRA_TOKEN_FILE")
    return (
        Path(os.environ["SCOUT_INTEGRATION_INFRA_TOKEN_FILE"])
        .read_text(encoding="utf-8")
        .strip()
    )


def _litellm_root() -> str:
    """`LiteLLMEmbedder` appends `/v1/embeddings`, so hand it the gateway root."""
    _require_env("LITELLM_BASE_URL", "LITELLM_MASTER_KEY")
    base = os.environ["LITELLM_BASE_URL"].strip().rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def _decode_jsonrpc(response: httpx.Response) -> dict[str, object]:
    """Decode one JSON-RPC body from a JSON or an SSE (streamable HTTP) response."""
    body = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        frames = [
            line[len("data:") :].strip()
            for line in body.splitlines()
            if line.startswith("data:")
        ]
        if not frames:
            raise AssertionError(f"no SSE data frame in MCP response: {body!r}")
        body = frames[0]
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise AssertionError(f"MCP response is not a JSON-RPC object: {body!r}")
    return decoded


def _tool_call_failure(response: httpx.Response) -> str | None:
    """Return why a tool call failed, or ``None`` when it genuinely succeeded.

    HTTP 200 only proves the transport worked. The JSON-RPC body is the sole
    authority on whether the tool ran — `scripts/test_mcp_endpoints.py` conflated
    the two and therefore reported success for every reachable server.
    """
    if response.status_code != 200:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    payload = _decode_jsonrpc(response)
    if "error" in payload:
        return f"JSON-RPC error member: {payload['error']!r}"
    result = payload.get("result")
    if not isinstance(result, dict):
        return f"no JSON-RPC result object: {payload!r}"
    if result.get("isError"):
        return f"tool result isError: {result.get('content')!r}"
    return None


@asynccontextmanager
async def _mcp_session(
    url: str, token: str
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    """Open a Streamable-HTTP MCP session (initialize → initialized → ready)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        opened = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": JSON_RPC_VERSION,
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "snp-integration-tests", "version": "1"},
                },
            },
        )
        assert opened.status_code == 200, (
            f"MCP initialize was rejected: HTTP {opened.status_code} "
            f"{opened.text[:200]}"
        )
        assert _decode_jsonrpc(opened).get("result"), "MCP initialize returned no result"
        session_id = opened.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id
        await client.post(
            url,
            headers=headers,
            json={"jsonrpc": JSON_RPC_VERSION, "method": "notifications/initialized"},
        )
        yield client, headers


async def _call_tool(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    name: str,
    arguments: dict[str, object],
) -> httpx.Response:
    return await client.post(
        url,
        headers=headers,
        json={
            "jsonrpc": JSON_RPC_VERSION,
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


@pytest.mark.asyncio
async def test_live_mcp_jsonrpc_result_body_decides_success() -> None:
    """A live `rag_fetch` succeeds in its body; a bad call fails in its body at HTTP 200."""
    _require_env("SCOUT_INTEGRATION_URL")
    url = os.environ["SCOUT_INTEGRATION_URL"]
    token = _integration_token()

    async with _mcp_session(url, token) as (client, headers):
        good = await _call_tool(
            client,
            url,
            headers,
            "rag_fetch",
            {
                "path": "raw/does-not-exist.md",
                "hint": "live transport contract for the rag_fetch tool",
                "department": "infra",
            },
        )
        assert _tool_call_failure(good) is None

        result = _decode_jsonrpc(good)["result"]
        assert isinstance(result, dict)
        structured = result.get("structuredContent")
        assert isinstance(structured, dict), f"no structured tool output: {result!r}"
        assert structured.get("status") in {"ok", "no_source"}
        assert isinstance(structured.get("context"), list)
        assert isinstance(structured.get("citations"), list)

        bad = await _call_tool(client, url, headers, "no_such_tool", {})
        failure = _tool_call_failure(bad)
        assert failure is not None, "an unknown tool must not be reported as success"
        if bad.status_code == 200:
            # Exactly the trap the deleted script fell into: a failed MCP call
            # arriving with a 200 status line.
            assert "isError" in failure or "error member" in failure, failure


@pytest.mark.asyncio
async def test_live_mcp_rejects_an_unrecognized_bearer_token() -> None:
    """An unknown token is refused, so no hardcoded dev token can ever work."""
    _require_env("SCOUT_INTEGRATION_URL")
    url = os.environ["SCOUT_INTEGRATION_URL"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer not-a-valid-token-{uuid.uuid4().hex}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": JSON_RPC_VERSION,
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "snp-integration-tests", "version": "1"},
                },
            },
        )
    assert response.status_code == 401, (
        f"unrecognized token was not refused: HTTP {response.status_code} "
        f"{response.text[:200]}"
    )


@pytest.mark.asyncio
async def test_live_wiki_sources_drive_rag_retrieval_end_to_end(
    tmp_path: Path,
) -> None:
    """wiki_search → wiki_read → `sources[]` → live pgvector → cited verbatim text."""
    _require_env(
        "LITELLM_BASE_URL",
        "LITELLM_MASTER_KEY",
        "POSTGRES_HOST",
        "POSTGRES_DB",
        "POSTGRES_INGEST_USER",
        "POSTGRES_QUERY_USER",
    )

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    doc_name = f"kepler-quorum-relay-{uuid.uuid4().hex[:8]}.md"
    (corpus_dir / doc_name).write_text(
        "# Kepler Quorum Relay Operations\n\n"
        "## Commit Protocol\n\n"
        f"{FIXTURE_FACT} Operators tune the phase timeout per region.\n",
        encoding="utf-8",
    )

    conn = await get_pg_connection()
    ingest_embedder = LiteLLMBatchEmbedder()
    source_uri: str | None = None
    try:
        ingested = await ingest_document(
            file_path=corpus_dir / doc_name,
            allowed_depts=["all", "infra"],
            conn=conn,
            embedder=ingest_embedder,
            base_dir=corpus_dir,
        )
        source_uri = ingested.get("source_uri")
        assert isinstance(source_uri, str) and source_uri
        assert ingested.get("chunks_count", 0) > 0, f"nothing was ingested: {ingested!r}"

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "kepler-quorum-relay.md").write_text(
            "---\n"
            "type: concept\n"
            "title: Kepler Quorum Relay\n"
            "summary: How the Kepler quorum relay commits a write across seven phases.\n"
            "entities: [Kepler Quorum Relay]\n"
            "department: infra\n"
            "sources:\n"
            f"  - path: {source_uri}\n"
            "    hint: Kepler quorum relay acknowledges a write after seven phases commit\n"
            "    loc: Section Commit Protocol\n"
            "last_compiled: 2026-08-19\n"
            "---\n\n"
            "## TL;DR\n\nThe relay commits in seven phases.\n",
            encoding="utf-8",
        )

        wiki_embedder = LiteLLMEmbedder(base_url=_litellm_root())
        backend = PgVectorRlsBackend(embedder=LiteLLMBatchEmbedder())
        engine = ScoutDiyEngine.from_vault(
            wiki_embedder,
            wiki_dir=wiki_dir,
            cache_path=tmp_path / "cache" / "wiki_vectors.json",
        )
        try:
            answer = await answer_query(
                wiki=engine,
                rag=backend,
                query="How does the Kepler quorum relay commit a write?",
                scope=Scope(departments=frozenset({"infra"})),
                need_rag=True,
                k=3,
            )
        finally:
            engine.close()
            await backend.close()
            await wiki_embedder.aclose()

        assert answer.page_path is not None, "wiki search found no page"
        assert answer.used_rag is True
        assert answer.status is AnswerStatus.WITH_SOURCES, (
            f"live retrieval returned {answer.status.value} for an ingested source"
        )
        assert answer.context, "no verbatim context was returned"
        assert [c.file_path for c in answer.citations] == [source_uri] * len(
            answer.citations
        ), "a citation escaped the addressed file"
        assert any("seven phases" in piece.text for piece in answer.context), (
            "retrieved context does not contain the ingested fact"
        )
    finally:
        if source_uri:
            await conn.execute(
                "DELETE FROM rag_documents WHERE source_uri = $1;", source_uri
            )
        await conn.close()
