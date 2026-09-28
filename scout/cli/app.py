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

import contextlib
import functools
import os
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
from scout.cli.result import CommandResult, ErrorKind, ExitCode

# ── dispatch ─────────────────────────────────────────────────────────────────

OutputOption = Annotated[
    OutputFormat, Parameter(name=["--output", "-o"], help="auto | text | json | yaml")
]


def _build_app() -> App:
    app = App(
        name="snpmemory",
        help="SNP Memory System — dual-layer knowledge vault operations.",
        # cyclopts invents a negation for every boolean (`--no-dry-run`) and
        # every list (`--empty-seen`). None of them is declared, so `schema`
        # never mentioned them and an agent could not know they existed; and
        # every flag here defaults to off, so a negation only restates the
        # default. The parser accepts what `schema` publishes, nothing more.
        default_parameter=Parameter(negative=()),
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
        # stdout belongs to `render` (render.py, rule 1). Several commands call
        # a script's `main` in-process -- propose, verify-groundedness, publish
        # -- and a script prints; each such line reached stdout ahead of the
        # payload, so `-o json` could not be parsed. While a command runs, a
        # print is diagnostics and goes to stderr, whatever the implementation
        # does. Only the command's run is covered: cyclopts' own `--help`
        # output is data a reader pipes into a pager, and stays on stdout.
        # Child processes inherit the file descriptor, not `sys.stdout`, so
        # they are each captured where they are started.
        with contextlib.redirect_stdout(sys.stderr):
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

    # The global arguments `schema` publishes (`registry.GLOBAL_ARGS`) are
    # consumed here rather than by each command: `--output` so that a failure
    # during parsing is still reported in the format the caller asked for, and
    # `--no-color` because no command declares it. Both spellings of a value
    # (`-o json`, `--output=json`) are accepted, since the schema promises an
    # option and an option takes either. Everything after `--` belongs to the
    # command -- `up -- -o x` forwards `-o` to compose -- and is left alone.
    fmt = OutputFormat.AUTO
    remaining: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            remaining.extend(tokens[index:])
            break
        if token == "--no-color":
            # This tool writes no ANSI of its own (see `render.py`); colour
            # reaches a terminal only from what a command runs -- compose,
            # git. `NO_COLOR` is how those are told, and `use_color` reads it
            # too, so exporting it is what makes the flag mean what it says.
            os.environ["NO_COLOR"] = "1"
            index += 1
            continue
        option, equals, attached = token.partition("=")
        if option in ("-o", "--output", "--format"):
            if equals:
                value, consumed = attached, 1
            elif index + 1 < len(tokens):
                value, consumed = tokens[index + 1], 2
            else:
                return render(
                    CommandResult.failure(
                        ErrorKind.INPUT_VALIDATION,
                        f"{token} requires a value",
                        hint="one of: auto, text, json, yaml",
                    ),
                    fmt,
                )
            try:
                fmt = OutputFormat(value)
            except ValueError:
                return render(
                    CommandResult.failure(
                        ErrorKind.INPUT_VALIDATION,
                        f"unknown output format {value!r}",
                        hint="one of: auto, text, json, yaml",
                    ),
                    fmt,
                )
            index += consumed
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
        if os.environ.get("SNP_CLI_TRACEBACK") == "1":
            raise
        result = _to_result(exc)

    if result is None:
        # cyclopts handled the invocation itself and has already written to the
        # terminal: `--help`, `--version`, and the bare no-command form each
        # print and return None rather than raising SystemExit. There is no
        # result to render, and nothing went wrong. Falling through to the
        # branch below would report a healthy `--help` as exit 2 — the
        # INFRASTRUCTURE code — while printing "this is a bug in snpmemory",
        # which is how a working binary reads as broken to a CI smoke test.
        #
        # Treating None as success is safe only because no command can return
        # it: every declared implementation is annotated `-> CommandResult`,
        # which `tests/test_cli_core.py` pins for the whole registry.
        return int(ExitCode.SUCCESS)
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
