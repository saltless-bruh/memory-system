"""`snpmemory install-agent` — install the SNP package for a target client.

A wrapper over `scripts/install-agent.sh`, the same way `mcp-config` wraps
`export_mcp_config.py`. The shell script stays the implementation because it is
what the documented `curl | bash` install runs; a second implementation here
would be a second thing to keep true.

The portable target retains its `.agent/` confirmation rule. OpenCode shares a
read-only planner with the shell helper to validate native config and managed
destinations before asking for confirmation or copying any files.
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
    client: str = "portable",
    dry_run: bool = False,
    confirm: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Install the agent package into a project directory."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    if client not in {"portable", "opencode"}:
        raise input_error(
            f"unknown installer client: {client}",
            hint="choose portable or opencode with --client",
        )

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
    plan = None
    if client == "opencode":
        from scripts.install_opencode_agent import (
            InstallError,
            prepare_install,
            require_confirmation,
        )

        try:
            plan = prepare_install(target, package)
            if not dry_run:
                require_confirmation(plan, confirm=confirm)
        except InstallError as exc:
            raise _installer_error(exc.code, str(exc), target=target) from exc
        would_write = [str(item.target) for item in plan.files]
        mcp_config = target / "opencode.json"
    else:
        would_write = [str(target / path) for path in _WRITES]
        mcp_config = target / ".mcp.json"
        if not mcp_config.exists():
            would_write.append(str(mcp_config))

    if dry_run:
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "status": "dry_run",
                "client": client,
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
    if client == "portable" and (target / ".agent").exists() and not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            f"{target} already has an .agent/ directory — nothing was installed",
            hint=(
                "review with --dry-run, then re-run with --confirm; existing "
                "files of the same name are replaced"
            ),
            details={"target": str(target)},
        )

    argv = [str(script), "--client", client]
    if confirm:
        argv.append("--confirm")
    argv.extend(["--", str(target)])
    env = os.environ.copy()
    # Use the running CLI's interpreter for the stdlib-only native helper.
    env["SNP_AGENT_PYTHON"] = sys.executable
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built here, not user text
            argv,
            env=env,
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
        if client == "opencode":
            raise _installer_error(
                completed.returncode,
                f"the OpenCode installer exited {completed.returncode}",
                target=target,
            )
        raise infrastructure_error(
            f"the installer exited {completed.returncode}",
            hint="re-run scripts/install-agent.sh directly to see its output",
            retryable=False,
            exit_code=completed.returncode,
        )

    servers: list[str] = list(plan.servers) if plan is not None else []
    if client == "portable" and mcp_config.is_file():
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
            "client": client,
            "target": str(target),
            "package": str(package),
            "servers": servers,
        },
        summary=f"installed the agent package into {target}",
        messages=tuple(line for line in completed.stdout.splitlines() if line.strip()),
    )


def _installer_error(code: int, message: str, *, target: Any) -> CliError:
    """Keep helper and shell failures inside the declared CLI error contract."""
    kind = {
        3: ErrorKind.INPUT_VALIDATION,
        5: ErrorKind.CONFIRMATION_REQUIRED,
        7: ErrorKind.CONFLICT,
    }.get(code, ErrorKind.INFRASTRUCTURE)
    hint = {
        ErrorKind.INPUT_VALIDATION: "pass an existing project directory",
        ErrorKind.CONFIRMATION_REQUIRED: "review --dry-run, then use --confirm",
        ErrorKind.CONFLICT: (
            "reconcile the existing OpenCode config or managed destination; "
            "--confirm cannot override invalid configuration or unsafe paths"
        ),
        ErrorKind.INFRASTRUCTURE: "check the package files and target directory permissions",
    }[kind]
    return CliError(kind, message, hint=hint, details={"target": str(target)})
