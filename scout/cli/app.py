"""The `snpmemory` dispatcher.

Owns three things no command should have to think about: argument parsing,
turning any failure into one envelope shape, and choosing the process exit code.

`cyclopts` handles parsing and validation, but its defaults are overridden here
deliberately. Out of the box it exits **1** on a bad argument, and `1` in this
tool means *"the check ran and found real problems"* — so an unrecognised flag
would reach CI looking like genuine address drift. Argument failures are
remapped to `3`, and its error rendering is replaced so nothing writes ANSI into
a pipe.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError

from scout.cli.config import resolve as resolve_config
from scout.cli.errors import CliError
from scout.cli.registry import (
    REGISTRY,
    CommandSpec,
    Effect,
    Prerequisite,
    command,
)
from scout.cli.render import OutputFormat, render
from scout.cli.result import CommandResult, ErrorKind, ExitCode

# ── declarations ─────────────────────────────────────────────────────────────
# Commands are declared as they are implemented. `schema` therefore reports what
# actually exists rather than what is planned, which is the only way an agent
# can trust it.

command(
    "schema",
    "Describe every command, output format, exit code, and error kind.",
    "scout.cli.commands.schema:schema",
    prerequisite=Prerequisite.NONE,
)

_SEMANTIC = (ExitCode.SEMANTIC_FAILURE,)

command(
    "verify-vault",
    "Lint page frontmatter and confirm wiki/index.md is current.",
    "scout.cli.commands.verify:verify_vault",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "verify-secrets",
    "Scan tracked, staged, and untracked bytes for credential-shaped values.",
    "scout.cli.commands.verify:verify_secrets",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION,),
)
command(
    "verify-addresses",
    "Check that every page's sources[] hint still retrieves its own file.",
    "scout.cli.commands.verify:verify_addresses",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "verify-groundedness",
    "Judge each page's body against the sources it cites.",
    "scout.cli.commands.verify:verify_groundedness",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "plan-articles",
    "Propose a multi-article decomposition from a source's own headings.",
    "scout.cli.commands.compile:plan_articles",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "compile-plan",
    "Compile every article in an approved plan; writes nothing unless all pass.",
    "scout.cli.commands.compile:compile_plan",
    effect=Effect.WRITE,
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)
command(
    "check",
    "Run every verification in order and stop at the first failure.",
    "scout.cli.commands.verify:check",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
)


# ── dispatch ─────────────────────────────────────────────────────────────────

OutputOption = Annotated[
    OutputFormat, Parameter(name=["--output", "-o"], help="auto | text | json | yaml")
]


def _build_app() -> App:
    app = App(
        name="snpmemory",
        help="SNP Memory System — dual-layer knowledge vault operations.",
    )
    for spec in REGISTRY:
        app.command(_wrap(spec), name=spec.name)
    return app


def _wrap(spec: CommandSpec) -> Callable[..., CommandResult]:
    """Adapt a declared command into something cyclopts can register.

    Two things happen here that must not happen anywhere else.

    The implementation is imported **at call time**, so registering a command
    never triggers whatever its module does on import. Several scripts in this
    repository call `dotenv.load_dotenv()` at module scope; importing one to
    register it would put thirty variables, including a provider key, into
    `os.environ` before the user had chosen a command.

    Configuration is resolved **from the declaration**, not by the command. A
    `NONE` command receives nothing, so `snpmemory schema` cannot read a
    credential even by accident: the guarantee is structural rather than a
    convention each command must remember.
    """

    def runner(*args: Any, **kwargs: Any) -> CommandResult:
        function = spec.load()
        config = resolve_config(spec.prerequisite)
        if "config" in inspect.signature(function).parameters:
            kwargs["config"] = config
        return function(*args, **kwargs)

    runner.__name__ = spec.name.replace("-", "_")
    runner.__doc__ = spec.summary
    return runner


def _to_result(exc: BaseException) -> CommandResult:
    """Convert any escaped exception into one envelope shape."""
    if isinstance(exc, CliError):
        return exc.to_result()
    if isinstance(exc, CycloptsError):
        # A parsing failure is invalid input (3), never a semantic finding (1).
        return CommandResult.failure(
            ErrorKind.INPUT_VALIDATION,
            str(exc) or "invalid arguments",
            hint="run `snpmemory schema` for the accepted commands and options",
        )
    # Unexpected failures are reported without their text: a traceback or a
    # driver message may carry a DSN, a token, or a path that should not travel.
    return CommandResult.failure(
        ErrorKind.INFRASTRUCTURE,
        f"{type(exc).__name__} during command execution",
        hint="re-run with SNP_CLI_TRACEBACK=1 to see the traceback locally",
        retryable=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code."""
    tokens = list(sys.argv[1:] if argv is None else argv)

    # `--output` is consumed here rather than by each command, so that a failure
    # during parsing is still reported in the format the caller asked for.
    fmt = OutputFormat.AUTO
    remaining: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("-o", "--output", "--format"):
            if index + 1 >= len(tokens):
                return render(
                    CommandResult.failure(
                        ErrorKind.INPUT_VALIDATION,
                        f"{token} requires a value",
                        hint="one of: auto, text, json, yaml",
                    ),
                    fmt,
                )
            try:
                fmt = OutputFormat(tokens[index + 1])
            except ValueError:
                return render(
                    CommandResult.failure(
                        ErrorKind.INPUT_VALIDATION,
                        f"unknown output format {tokens[index + 1]!r}",
                        hint="one of: auto, text, json, yaml",
                    ),
                    fmt,
                )
            index += 2
            continue
        remaining.append(token)
        index += 1

    app = _build_app()
    try:
        # `result_action="return_value"` is essential, not cosmetic: cyclopts
        # otherwise pretty-prints whatever a command returns, which would dump a
        # rich-formatted CommandResult -- ANSI codes and all -- onto stdout ahead
        # of the renderer, corrupting both piped JSON and machine-read text.
        result = app(
            remaining,
            exit_on_error=False,
            print_error=False,
            result_action="return_value",
        )
    except SystemExit as exc:  # --help and --version exit cleanly through cyclopts
        return int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001 - every failure gets one envelope
        if __import__("os").environ.get("SNP_CLI_TRACEBACK") == "1":
            raise
        result = _to_result(exc)

    if not isinstance(result, CommandResult):
        # A command returned something else; treat it as a programming error
        # rather than printing an unknown object to stdout.
        result = CommandResult.failure(
            ErrorKind.INFRASTRUCTURE,
            "command did not return a CommandResult",
            hint="this is a bug in snpmemory, not in your invocation",
        )
    return render(result, fmt)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
