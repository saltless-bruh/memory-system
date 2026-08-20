"""Live-container proof that FastMCP receives and enforces bearer identity."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


def _integration_token() -> str:
    direct = os.environ.get("SCOUT_INTEGRATION_INFRA_TOKEN", "").strip()
    if direct:
        return direct
    return Path(os.environ["SCOUT_INTEGRATION_INFRA_TOKEN_FILE"]).read_text(
        encoding="utf-8"
    ).strip()


@pytest.mark.integration
async def test_live_authorization_header_narrows_and_denial_precedes_retrieval() -> None:
    url = os.environ["SCOUT_INTEGRATION_URL"]
    token = _integration_token()

    async with Client(url, auth=token) as client:
        with pytest.raises(ToolError, match="authenticated scope"):
            await client.call_tool(
                "rag_fetch",
                {
                    "path": "raw/does-not-matter-for-denial.md",
                    "hint": "denied requests must not reach embedding or PostgreSQL",
                    "department": "redteam",
                },
            )

        result = await client.call_tool(
            "rag_fetch",
            {
                "path": "raw/does-not-exist.md",
                "hint": "authenticated request propagation",
                "department": "infra",
            },
        )
    assert not result.is_error
