#!/usr/bin/env python3
"""Launch basic-memory's MCP server with the broken ChatGPT-compat tools removed.

WHY THIS EXISTS
---------------
basic-memory ships a generic ``search`` / ``fetch`` tool pair
(``mcp/tools/chatgpt_tools.py``, the OpenAI/ChatGPT deep-research connector
adapter) that round-trips documents **by permalink**: ``search`` returns each
hit's ``id = permalink or f"doc-{N}"`` and ``fetch(id)`` resolves it via
``read_note``.

This deployment sets ``disable_permalinks: true`` (so the engine never rewrites
frontmatter into the vault — keeps it pristine, R-2.5). With permalinks off,
``search`` has no stable id and falls back to throwaway ``doc-0``, ``doc-1`` …
which ``fetch`` then cannot resolve ("Note Not Found"). Agents that reach for
``search``/``fetch`` stumble. The **native** ``search_notes`` / ``read_note``
tools key on title/path and are unaffected — they are what AGENTS.md prescribes.

WHAT IT DOES
------------
Wraps the shared FastMCP server's ``run`` so that — after ``bm mcp`` has
imported and registered every tool, but before the transport starts — it drops
``search`` and ``fetch`` from the registry via FastMCP's public
``remove_tool``. Then it delegates to the normal ``bm mcp`` command so all of
its setup (routing, the sync lifespan, logging, project constraint) is
unchanged. No package files are patched.
"""

from __future__ import annotations

import contextlib
from typing import Any

from basic_memory.mcp.server import mcp

# The ChatGPT-compat tools whose ids don't round-trip when permalinks are off.
_TRIM = ("search", "fetch")

_orig_run = mcp.run


def _run_without_chatgpt_tools(*args: Any, **kwargs: Any) -> Any:
    """Remove the broken tools (idempotent, best-effort) then run the server."""
    for name in _TRIM:
        with contextlib.suppress(Exception):
            mcp.remove_tool(name)
    return _orig_run(*args, **kwargs)


mcp.run = _run_without_chatgpt_tools  # type: ignore[method-assign]


if __name__ == "__main__":
    # Delegate to the real CLI command so its full setup runs unchanged; our
    # wrapped `mcp.run` (called via its deferred server) does the trimming.
    from basic_memory.cli.commands.mcp import mcp as mcp_command

    mcp_command(
        transport="streamable-http",
        host="0.0.0.0",
        port=8765,
        path="/mcp",
        project="snp-wiki",
    )
