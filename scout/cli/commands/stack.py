"""The stack family: `up`, `down`, `status`, `logs`, and `init`.

A thin, honest layer over `docker compose` and the secret bootstrapper. These
exist so an operator has one tool rather than two, not to hide the one they
already know: unrecognised arguments are forwarded, so `snpmemory up --build`
behaves exactly as `docker compose up -d --build` does.

`status` is the one that earns its place. It reports what compose reports **and**
runs `scripts/preflight_stack.py`, whose two checks name the failures that took
the stack down on 2026-08-24 and that neither compose nor a health column
surfaces: containers created without a forwardable resolver, and an image older
than the checkout.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import conflict_error, infrastructure_error
from scout.cli.result import CommandResult, ErrorKind, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]

#: Services `down` would stop. Named so a refusal can say what is at stake.
_STATEFUL = ("postgres", "git")


def _compose(args: list[str], *, cwd: Any, capture: bool = True) -> Any:
    """Run one `docker compose` invocation, or report why it could not run."""
    import subprocess

    try:
        return subprocess.run(  # noqa: S603 - fixed argv head, no shell
            ["docker", "compose", *args],
            cwd=cwd,
            capture_output=capture,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        raise infrastructure_error(
            "docker is not available on PATH",
            hint="install Docker, or run the underlying command yourself",
            retryable=False,
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise infrastructure_error(
            "docker compose could not be run",
            hint="check that the Docker daemon is running",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc


def _completed_one_shot(row: dict[str, Any]) -> bool:
    """Whether a non-running service finished successfully rather than failed."""
    return row["state"] == "exited" and row.get("exit_code") == 0


def _services(cwd: Any) -> list[dict[str, Any]]:
    """Every service compose knows about, with its state and health."""
    import json

    completed = _compose(["ps", "--all", "--format", "json"], cwd=cwd)
    if completed.returncode != 0:
        raise infrastructure_error(
            "docker compose could not list services",
            hint=(completed.stderr or "").strip()[:200] or "is the daemon running?",
            retryable=True,
        )
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(
            {
                "service": payload.get("Service", ""),
                "state": payload.get("State", ""),
                "status": payload.get("Status", ""),
                "health": payload.get("Health", ""),
                "exit_code": payload.get("ExitCode"),
            }
        )
    return sorted(rows, key=lambda row: row["service"])


def up(*extra: str, config: Injected = None) -> CommandResult:
    """Start the stack. Unrecognised arguments are forwarded to compose."""
    cfg: Config = config
    repo = cfg.require_repo()
    completed = _compose(["up", "-d", *extra], cwd=repo, capture=False)
    if completed.returncode != 0:
        raise infrastructure_error(
            "docker compose up failed",
            hint="run `snpmemory logs` to see which service refused to start",
            retryable=True,
        )
    return CommandResult(
        data={"services": _services(repo)},
        summary="stack started",
        messages=("run `snpmemory status` to check every route",),
    )


def down(*extra: str, confirm: bool = False, config: Injected = None) -> CommandResult:
    """Stop the stack. Requires `--confirm`: `postgres` holds the corpus."""
    from scout.cli.errors import CliError

    cfg: Config = config
    repo = cfg.require_repo()
    if not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            "would stop every service — not performed",
            hint="re-run with --confirm: snpmemory down --confirm",
            details={"stateful_services": list(_STATEFUL)},
        )
    completed = _compose(["down", *extra], cwd=repo, capture=False)
    if completed.returncode != 0:
        raise infrastructure_error("docker compose down failed", retryable=True)
    return CommandResult(data={"status": "stopped"}, summary="stack stopped")


def logs(
    service: str | None = None, *extra: str, config: Injected = None
) -> CommandResult:
    """Show recent logs for one service, or for all of them."""
    cfg: Config = config
    repo = cfg.require_repo()
    args = ["logs", "--no-color", *extra]
    if service:
        args.append(service)
    completed = _compose(args, cwd=repo)
    if completed.returncode != 0:
        raise infrastructure_error(
            "docker compose logs failed",
            hint=(completed.stderr or "").strip()[:200] or None,
            retryable=True,
        )
    lines = completed.stdout.splitlines()
    return CommandResult(
        data={"service": service, "lines": lines},
        summary=completed.stdout.rstrip("\n"),
    )


def status(*, config: Injected = None) -> CommandResult:
    """Report every service, plus the two checks compose cannot make."""
    from scripts.preflight_stack import collect_findings, exit_code_for

    cfg: Config = config
    repo = cfg.require_repo()
    services = _services(repo)
    findings = collect_findings()
    preflight = [
        {
            "check": finding.check,
            "ok": finding.ok,
            "available": finding.available,
            "detail": finding.detail,
            "remedy": finding.remedy,
        }
        for finding in findings
    ]

    unhealthy = [row["service"] for row in services if row["health"] == "unhealthy"]
    # A one-shot that finished its job is not a service that is down.
    # `postgres-migrate` runs the migrations and exits 0 by design; reporting it
    # as stopped makes `status` cry wolf on a perfectly healthy stack, and a
    # check that is wrong on the happy path stops being read.
    stopped = [
        row["service"]
        for row in services
        if row["state"] != "running" and not _completed_one_shot(row)
    ]
    preflight_code = exit_code_for(findings)

    # A stopped service or a failed preflight is a *finding* about the stack,
    # not a failure of this command: `status` ran correctly and is reporting
    # what it found, which is exactly the 0/1 outcome split.
    ok = not unhealthy and not stopped and preflight_code == 0
    return CommandResult(
        exit_code=ExitCode.SUCCESS if ok else ExitCode.SEMANTIC_FAILURE,
        data={
            "status": "ok" if ok else "degraded",
            "services": services,
            "preflight": preflight,
            "unhealthy": unhealthy,
            "stopped": stopped,
        },
        summary="\n".join(
            [
                *(
                    f"{row['service']:<14} {row['state']:<12} {row['health'] or '-'}"
                    for row in services
                ),
                *(
                    f"{'PASS' if f.ok else 'FAIL' if f.available else 'UNAVAIL'} "
                    f"{f.check}: {f.detail}"
                    for f in findings
                ),
            ]
        ),
    )


def init(
    *,
    directory: str = ".secrets",
    rotate: bool = False,
    confirm: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Create the local secret files the stack needs.

    Never prints a secret, and never overwrites one by accident: rotation is a
    separate flag that also requires `--confirm`, because replacing a secret
    that services are already holding takes the stack down until they restart.
    """
    from pathlib import Path

    from scout.cli.errors import CliError
    from scripts.bootstrap_secrets import ensure_secrets

    cfg: Config = config
    repo = cfg.require_repo()
    target = Path(directory)
    if not target.is_absolute():
        target = repo / target

    if rotate and not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            "would replace every managed secret — not performed",
            hint="re-run with --confirm: snpmemory init --rotate --confirm",
            details={"directory": str(target)},
        )
    if not rotate and target.exists() and any(target.iterdir()):
        # Creating over an existing set would silently do nothing for the files
        # that exist and everything for the ones that do not — a half state
        # nobody asked for. Say so instead.
        existing = sorted(path.name for path in target.iterdir() if path.is_file())
        if existing:
            raise conflict_error(
                f"{len(existing)} secret file(s) already exist in {target}",
                hint="use --rotate --confirm to replace them, or delete them first",
                existing=existing,
            )

    created = ensure_secrets(target, rotate=rotate)
    return CommandResult(
        data={
            "directory": str(target),
            "created": sorted(path.name for path in created),
            "rotated": rotate,
        },
        # The names only: a value here would end up in a terminal, a log, and a
        # CI transcript.
        summary=f"{'rotated' if rotate else 'created'} {len(created)} secret file(s) in {target}",
    )
