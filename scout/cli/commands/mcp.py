"""`snpmemory mcp` — serve this repository's operations over stdio MCP.

The server carries the authority of whoever launches it. That is deliberate and
documented in `scout/mcp/local_server.py`; it is also why this command offers no
`--host` or `--port`. Exposing these tools on a socket needs an authorization
design, not a flag.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.result import CommandResult, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def mcp(*, list_tools: bool = False, config: Injected = None) -> CommandResult:
    """Serve the local MCP tools over stdio, or list them and exit."""
    from scout.mcp.local_server import build_server, run

    cfg: Config = config
    cfg.require_repo()

    if list_tools:
        import asyncio

        tools = asyncio.run(build_server().list_tools())
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "tools": [
                    {
                        "name": tool.name,
                        "read_only": bool(
                            tool.annotations and tool.annotations.readOnlyHint
                        ),
                        "destructive": bool(
                            tool.annotations and tool.annotations.destructiveHint
                        ),
                    }
                    for tool in sorted(tools, key=lambda t: t.name)
                ]
            },
            summary=f"{len(tools)} tool(s) available over stdio",
        )

    # Blocks until the client disconnects. Nothing is printed to stdout: the
    # transport owns it, and a stray print would corrupt the JSON-RPC stream.
    run()
    return CommandResult(exit_code=ExitCode.SUCCESS, summary="mcp server stopped")
