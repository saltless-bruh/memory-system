"""Live-container proof that FastMCP receives and enforces bearer identity.

These called `rag_fetch` until 2026-09-26, a tool the served surface stopped
exposing with V3 (the three served tools are `wiki_search`, `wiki_read` and
`wiki_quote`). The first assertion then matched an unknown-tool error rather
than the scope denial it was written to prove, so the live HTTP auth path had
no test that could pass. They now drive the tools an agent actually calls.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

#: The integration token is minted for `infra` only. `redteam` is outside it.
GRANTED = "infra"
FORBIDDEN = "redteam"


def _integration_token() -> str:
    direct = os.environ.get("SCOUT_INTEGRATION_INFRA_TOKEN", "").strip()
    if direct:
        return direct
    return (
        Path(os.environ["SCOUT_INTEGRATION_INFRA_TOKEN_FILE"])
        .read_text(encoding="utf-8")
        .strip()
    )


def _client() -> Client:
    return Client(os.environ["SCOUT_INTEGRATION_URL"], auth=_integration_token())


@pytest.mark.integration
async def test_live_served_surface_is_the_three_wiki_tools() -> None:
    """Pin the names the rest of this file calls, so a rename fails here first."""
    async with _client() as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == {"wiki_search", "wiki_read", "wiki_quote"}


@pytest.mark.integration
async def test_live_denial_precedes_retrieval_on_search_and_read() -> None:
    """A department outside the token's scope is refused as a scope error.

    Matching "authenticated scope" matters: an unknown-tool or not-found error
    would also raise `ToolError`, and that is exactly how the `rag_fetch`
    version of this test would have "passed" while proving nothing.
    """
    async with _client() as client:
        with pytest.raises(ToolError, match="authenticated scope"):
            await client.call_tool(
                "wiki_search",
                {
                    "query": "denied requests must not reach embedding or PostgreSQL",
                    "department": FORBIDDEN,
                },
            )
        with pytest.raises(ToolError, match="authenticated scope"):
            await client.call_tool(
                "wiki_read",
                {"path": "does-not-matter-for-denial.md", "department": FORBIDDEN},
            )
        # Naming a granted department beside a forbidden one is still an
        # expansion, not a narrowing.
        with pytest.raises(ToolError, match="authenticated scope"):
            await client.call_tool(
                "wiki_search",
                {"query": "mixed request", "department": [GRANTED, FORBIDDEN]},
            )


@pytest.mark.integration
async def test_live_authorization_header_narrows_to_a_granted_department() -> None:
    async with _client() as client:
        result = await client.call_tool(
            "wiki_search",
            {"query": "authenticated request propagation", "department": GRANTED},
        )
    assert not result.is_error
    # `structured_content`, not `data`: the declared output schema makes a
    # client coerce `data` into a model, which three gate consumers tripped on.
    envelope = result.structured_content
    assert isinstance(envelope, dict)
    assert {"results", "returned", "suppressed_as_seen", "has_more"} <= set(envelope)
    assert isinstance(envelope["results"], list)
    assert envelope["returned"] == len(envelope["results"])
