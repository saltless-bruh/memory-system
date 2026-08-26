"""`snpmemory install-agent` — install the portable agent package elsewhere.

A wrapper over `scripts/install-agent.sh`, the same way `mcp-config` wraps
`export_mcp_config.py`. The shell script stays the implementation because it is
what the documented `curl | bash` install runs; a second implementation here
would be a second thing to keep true.

What the wrapper adds is the exit-code contract and a refusal. Installing writes
`.agent/` and an `.mcp.json` into a directory somebody else owns, so a
non-empty target needs `--confirm`; `--dry-run` reports what would be written
and touches nothing.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import CliError, infrastructure_error, input_error
from scout.cli.result import CommandResult, ErrorKind, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]

#: What the installer creates in a target directory.
_WRITES = (".agent/rules", ".agent/instructions", ".agent/workflows", ".agent/skills")


def install_agent(
    directory: str = ".",
    *,
    dry_run: bool = False,
    confirm: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Install the agent package into a project directory."""
    import subprocess
    from pathlib import Path

    cfg: Config = config
    repo = cfg.require_repo()

    script = repo / "scripts" / "install-agent.sh"
    if not script.is_file():
        raise infrastructure_error(
            "scripts/install-agent.sh is missing",
            hint="this command wraps that script; reinstall the checkout",
            retryable=False,
        )

    target = Path(directory).expanduser().resolve()
    if not target.is_dir():
        raise input_error(
            f"{directory} is not a directory",
            hint="pass an existing project directory to install into",
            target=str(target),
        )

    package = repo / "packages" / "snp-agent"
    would_write = [f"{target / path}" for path in _WRITES]
    mcp_config = target / ".mcp.json"
    if not mcp_config.exists():
        would_write.append(str(mcp_config))

    if dry_run:
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "status": "dry_run",
                "target": str(target),
                "package": str(package),
                "would_write": would_write,
            },
            summary=f"[dry-run] would install into {target}",
            messages=tuple(f"  {path}" for path in would_write),
        )

    # An existing `.agent/` belongs to somebody's project. The installer copies
    # over it non-destructively, but "non-destructive" is a claim the caller
    # should get to check before it runs, not after.
    if (target / ".agent").exists() and not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            f"{target} already has an .agent/ directory — nothing was installed",
            hint=(
                "review with --dry-run, then re-run with --confirm; existing "
                "files of the same name are replaced"
            ),
            details={"target": str(target)},
        )

    try:
        completed = subprocess.run(  # noqa: S603 - argv is built here, not user text
            [str(script), str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise infrastructure_error(
            "the installer could not be run",
            hint="check that scripts/install-agent.sh is executable",
            retryable=False,
            cause=type(exc).__name__,
        ) from exc

    if completed.returncode != 0:
        raise infrastructure_error(
            f"the installer exited {completed.returncode}",
            hint="re-run scripts/install-agent.sh directly to see its output",
            retryable=False,
            exit_code=completed.returncode,
        )

    servers: list[str] = []
    if mcp_config.is_file():
        import json

        try:
            servers = sorted(
                json.loads(mcp_config.read_text(encoding="utf-8"))["mcpServers"]
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            servers = []

    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={
            "status": "installed",
            "target": str(target),
            "package": str(package),
            "servers": servers,
        },
        summary=f"installed the agent package into {target}",
        messages=tuple(line for line in completed.stdout.splitlines() if line.strip()),
    )
