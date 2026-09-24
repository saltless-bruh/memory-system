"""`snpmemory mcp-config` — emit client configuration for this stack.

**`snpmemory mcp` is gone (leaf-4.3, 2026-09-24).** It served a second MCP
surface over stdio, carrying the authority of whoever launched it. That server
is deleted rather than mitigated: it held a duplicate copy of `wiki_search` and
`wiki_read` that could drift from the ones actually served, and it occupied the
`snpmemory` name the authenticated retrieval server takes next. What it offered
did not disappear with it — `verify`, `plan-articles` and `compile-*` remain
CLI commands, and ship as Agent Skills that run them.

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
        ConfigTargetConflict,
        _load_existing,
        _write_exports,
        generate_config,
        merge_configs,
        validate_config_target,
    )

    cfg: Config = config

    if client not in SUPPORTED_CLIENTS:
        raise input_error(
            f"unknown client {client!r}",
            hint="one of: " + ", ".join(SUPPORTED_CLIENTS),
            supported=list(SUPPORTED_CLIENTS),
        )

    # One server for every client: the authenticated Scout connection. `root`
    # pinned the checkout the local stdio server served, and is now ignored by
    # the exporter; it stays in the call because requiring a repository is
    # still the right precondition for a command that writes this project's
    # configuration.
    generated = generate_config(client, root=cfg.require_repo())
    server_key = {"opencode": "mcp", "vscode": "servers"}.get(client, "mcpServers")
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
    try:
        validate_config_target(client, target)
    except ConfigTargetConflict as exc:
        raise conflict_error(
            str(exc),
            hint="reconcile the JSONC configuration explicitly; nothing was written",
            path=str(target),
        ) from exc

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
        merged = merge_configs(
            _load_existing(target, strict=client == "opencode"), generated
        )
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
