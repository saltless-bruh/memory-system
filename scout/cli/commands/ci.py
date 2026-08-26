"""The CI family: `gate` and `heal`.

Both carry the same safety property, and it is the reason they are separate
commands rather than one: **exit `2` never triggers a mutation.** An
infrastructure failure means the check could not run, and a check that could not
run has said nothing about the vault — healing on it would rewrite addresses on
no evidence at all.

`gate` is the closed-loop state machine; `heal` applies scoped edits and nothing
else. Branching, committing and pushing belong to the gate, not to the healer —
which is why `heal` writes files and stops there.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import CliError, infrastructure_error, input_error
from scout.cli.result import CommandResult, ErrorKind, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]

#: Gate exit codes, in this repository's own terms.
_GATE_MEANING = {
    0: "clean",
    1: "findings remain",
    2: "infrastructure",
}


def gate(
    *,
    mode: str,
    remote: str = "origin",
    branch: str | None = None,
    advisory_groundedness: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Run the closed-loop address gate."""
    from scripts import ci_address_gate

    cfg: Config = config
    cfg.require_repo()
    if mode not in ("pr", "scheduled"):
        raise input_error(
            f"unknown mode {mode!r}", hint="--mode pr or --mode scheduled"
        )

    argv = ["--mode", mode, "--remote", remote]
    if branch:
        argv += ["--branch", branch]
    if advisory_groundedness:
        # Enforcement is the gate's default since 2026-08-26. This is the
        # deliberate, visible override, and it applies to the verdict only — an
        # infrastructure failure is never advisory.
        argv.append("--advisory-groundedness")

    try:
        code = ci_address_gate.main(argv)
    except SystemExit as exc:  # argparse refuses a bad argument
        raise input_error(
            "the gate rejected its arguments", hint=str(exc.code)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - never leak a credential in a trace
        raise infrastructure_error(
            "the gate could not run",
            hint="check the stack and the git remote",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    if code == 2:
        # Reported as an error, not an outcome, precisely so nothing downstream
        # reads it as "the vault is fine" or as permission to heal.
        raise infrastructure_error(
            "the gate could not complete",
            hint="fix the infrastructure and re-run; no heal was attempted",
            retryable=True,
        )
    return CommandResult(
        exit_code=ExitCode.SUCCESS if code == 0 else ExitCode.SEMANTIC_FAILURE,
        data={
            "mode": mode,
            "status": _GATE_MEANING.get(code, "unknown"),
            "gate_exit": code,
        },
        summary=f"gate ({mode}): {_GATE_MEANING.get(code, code)}",
    )


def heal(
    *,
    ci: bool = False,
    dry_run: bool = False,
    confirm: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Re-mint drifted addresses in place. Branching is the gate's job."""
    import asyncio

    from scout.healer import verify_and_heal_vault

    cfg: Config = config
    cfg.require_repo()
    if not dry_run and not confirm:
        raise CliError(
            ErrorKind.CONFIRMATION_REQUIRED,
            "would rewrite hints in sources[] — not performed",
            hint="see what would change with --dry-run, then re-run with --confirm",
            details={"suggested": "snpmemory heal --dry-run"},
        )

    backend = _backend(cfg)
    try:
        code = asyncio.run(verify_and_heal_vault(backend, ci_mode=ci, dry_run=dry_run))
    except CliError:
        raise
    except Exception as exc:  # noqa: BLE001 - a backend trace may carry a DSN
        raise infrastructure_error(
            "the healer could not complete",
            hint="healing needs postgres AND the embedding route; nothing was written",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    if code == 2:
        raise infrastructure_error(
            "the healer could not complete",
            hint="nothing was written",
            retryable=True,
        )
    return CommandResult(
        exit_code=ExitCode.SUCCESS if code == 0 else ExitCode.SEMANTIC_FAILURE,
        data={
            "status": "clean" if code == 0 else "unhealed",
            "dry_run": dry_run,
            "healer_exit": code,
        },
        summary=(
            "[dry-run] nothing written"
            if dry_run
            else (
                "no drift to heal"
                if code == 0
                else "some addresses could not be healed"
            )
        ),
    )


def _backend(cfg: Config) -> Any:
    """The RLS backend, wired entirely from resolved configuration."""
    from scout.cli.commands.authoring import _backend as build

    return build(cfg)
