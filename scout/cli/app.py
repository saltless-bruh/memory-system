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

import functools
import sys
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError

from scout.cli.declarations import DECLARED
from scout.cli.errors import CliError
from scout.cli.invoke import invoke
from scout.cli.registry import CommandSpec
from scout.cli.render import OutputFormat, render
from scout.cli.result import CommandResult, ErrorKind

# ── dispatch ─────────────────────────────────────────────────────────────────

OutputOption = Annotated[
    OutputFormat, Parameter(name=["--output", "-o"], help="auto | text | json | yaml")
]


def _build_app() -> App:
    app = App(
        name="snpmemory",
        help="SNP Memory System — dual-layer knowledge vault operations.",
    )
    for spec in DECLARED:
        app.command(_wrap(spec), name=spec.name)
    return app


def _wrap(spec: CommandSpec) -> Callable[..., CommandResult]:
    """Adapt a declared command into something cyclopts can register.

    The implementation is loaded **here**, at registration, because cyclopts
    parses from the real signature: it needs the annotations to know that
    `--dry-run` is a flag rather than a string option, and it needs the
    parameter names to map `--max-depth` onto `max_depth`. A generic
    `(*args, **kwargs)` wrapper gives it neither, and the result is not a
    graceful degradation — every multi-word flag in the tool breaks.

    What must NOT happen at import time is reading an environment or resolving
    a credential. That guarantee now rests on the command modules themselves:
    each keeps its heavy imports inside its functions, so importing one pulls
    in cyclopts and this package and nothing else. `tests/test_cli_core.py`
    holds them to it.

    Configuration is still resolved from the **declaration** at call time, so a
    `NONE` command receives nothing and `snpmemory schema` cannot read a
    credential even by accident.
    """
    function = spec.load()

    @functools.wraps(function)
    def runner(*args: Any, **kwargs: Any) -> CommandResult:
        return invoke(spec, *args, **kwargs)

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
