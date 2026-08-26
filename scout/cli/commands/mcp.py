"""`snpmemory mcp` — serve this repository's operations over stdio MCP, and
`snpmemory mcp-config` — emit the client configuration that reaches it.

The server carries the authority of whoever launches it. That is deliberate and
documented in `scout/mcp/local_server.py`; it is also why this command offers no
`--host` or `--port`. Exposing these tools on a socket needs an authorization
design, not a flag.

`mcp-config` prints by default and writes only when asked. Two properties it
must not lose:

**A user's existing config is merged, never replaced.** It holds settings for
servers this project knows nothing about, and clobbering them is data loss.

**A merged document is written but never echoed.** The generated half contains
only a `${SCOUT_AUTH_HEADER}` reference, but the half read from disk may hold a
real token for an unrelated server, and a command that prints what it merged
would leak it into a terminal, a log, or an agent's context.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import CliError, conflict_error, input_error
from scout.cli.result import CommandResult, ErrorKind, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def mcp(
    *, root: str | None = None, list_tools: bool = False, config: Injected = None
) -> CommandResult:
    """Serve the local MCP tools over stdio, or list them and exit."""
    import os
    from pathlib import Path

    from scout.cli.config import find_repo_root
    from scout.mcp.local_server import build_server, run

    cfg: Config = config

    # `SNP_MEMORY_ROOT` is the same pin by another door. A portable Agent
    # Plugins package cannot express this one: `${PLUGIN_ROOT}` names the
    # *installed plugin's* directory, every resolved path must stay inside it,
    # and the checkout this server has to serve is outside it by construction.
    # So `packages/snp-agent/mcp.json` ships the variable empty and the user
    # fills it — and an empty one fails loudly below rather than serving the
    # wrong tree.
    if root is None:
        root = os.environ.get("SNP_MEMORY_ROOT", "").strip() or None
        if root is None and os.environ.get("SNP_MEMORY_ROOT") is not None:
            raise input_error(
                "SNP_MEMORY_ROOT is set but empty",
                hint=(
                    "put the path of your memory-system checkout in it, or pass "
                    "--root; it ships empty because a portable plugin cannot "
                    "know where your checkout is"
                ),
            )

    if root is None:
        cfg.require_repo()
    else:
        # Pin the working directory before anything runs. Every tool call
        # resolves its configuration, its `.env`, and any relative plan path
        # against the process cwd, so a client that launches this server from
        # its own directory would otherwise serve a different checkout — or,
        # launched from outside one, no checkout at all. Moving the process once
        # at startup pins all of those together, which no amount of threading a
        # root through individual call sites would.
        requested = Path(root).expanduser()
        checkout = find_repo_root(requested) if requested.is_dir() else None
        if checkout is None:
            raise input_error(
                f"--root {root} is not inside a repository checkout",
                hint="pass the directory holding pyproject.toml and AGENTS.md",
                root=str(requested),
            )
        os.chdir(checkout)

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


def mcp_config(
    *,
    client: str,
    out: str | None = None,
    confirm: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Emit MCP client configuration for this stack. Prints unless `--out`."""
    import json
    from pathlib import Path

    from scripts.export_mcp_config import (
        CLIENT_CONFIG_PATHS,
        SUPPORTED_CLIENTS,
        _load_existing,
        _write_exports,
        generate_config,
        merge_configs,
    )

    cfg: Config = config

    if client not in SUPPORTED_CLIENTS:
        raise input_error(
            f"unknown client {client!r}",
            hint="one of: " + ", ".join(SUPPORTED_CLIENTS),
            supported=list(SUPPORTED_CLIENTS),
        )

    # The exported local-server entry pins this checkout, so a client launched
    # from anywhere reaches the tree the config was generated from.
    generated = generate_config(client, root=cfg.require_repo())
    server_key = "servers" if "servers" in generated else "mcpServers"
    servers = sorted(generated[server_key])
    conventional = CLIENT_CONFIG_PATHS[client]

    if out is None:
        # Text mode renders `summary` to stdout and nothing else, so the config
        # itself has to be the summary: printing it is the whole command.
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "client": client,
                "servers": servers,
                "default_path": conventional,
                "config": generated,
            },
            summary=json.dumps(generated, indent=2, sort_keys=True),
            messages=(
                f"{client}: {len(servers)} server(s) — {', '.join(servers)}",
                f"conventional location: {conventional}",
            ),
        )

    target = Path(out).expanduser()
    if target.exists() and not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            f"{target} already exists and was not modified",
            hint=(
                "the managed servers would be merged into it, replacing any "
                "entry of the same name; re-run with --confirm"
            ),
            details={"path": str(target), "servers": servers},
        )

    try:
        merged = merge_configs(_load_existing(target), generated)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        # Only the exception class is reported: the file being complained about
        # is the one that may hold somebody's token.
        raise conflict_error(
            f"{target} could not be read as an MCP client config",
            hint="fix or move the file; nothing was written",
            cause=type(exc).__name__,
        ) from exc

    _write_exports([(client, target, merged)])

    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={"client": client, "servers": servers, "written_to": str(target)},
        summary=f"wrote {len(servers)} server(s) to {target}",
    )
