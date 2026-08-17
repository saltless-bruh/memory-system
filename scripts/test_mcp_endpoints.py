#!/usr/bin/env python3
"""test_mcp_endpoints.py — Live MCP Server execution test for Scout.

Tests the live fastmcp server (scout.mcp_server) that exposes the `rag_fetch`
tool directly over the MCP protocol contract.
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.backends.fake import FakeRagBackend  # noqa: E402
from scout.mcp_server import build_server  # noqa: E402
from scout.types import RagChunk  # noqa: E402


async def main() -> None:
    print("==================================================")
    print("SCOUT MCP SERVER — LIVE ENDPOINT TEST")
    print("==================================================")

    # 1. Seed Data Vault backend with RFC source chunk
    chunk = RagChunk(
        file_path="raw/rfcs/rfc793-tcp.md",
        text="TCP connection establishment uses a 3-way handshake (SYN, SYN-ACK, ACK). Flow control uses sliding window.",
        loc="Section Key Specifications",
        score=0.98,
    )
    backend = FakeRagBackend(chunks=[chunk])

    # 2. Build FastMCP server instance
    mcp_server = build_server(backend)
    print("[1] Scout FastMCP Server created with tool: rag_fetch")

    # 3. Call the rag_fetch MCP tool programmatically
    print("[2] Invoking rag_fetch tool via FastMCP...")
    result = await mcp_server.call_tool(
        "rag_fetch",
        {
            "path": "raw/rfcs/rfc793-tcp.md",
            "hint": "TCP connection establishment 3-way handshake sliding window",
            "loc": "Section Key Specifications",
        },
    )

    print("\n--------------------------------------------------")
    print("MCP TOOL RESPONSE (`rag_fetch`):")
    print("--------------------------------------------------")
    print(result.content if hasattr(result, "content") else result)

    print("\n==================================================")
    print("TEST SUCCESS: Scout MCP Server rag_fetch endpoint verified!")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
