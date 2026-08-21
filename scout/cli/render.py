"""Turning a `CommandResult` into bytes on the right stream.

Three rules hold in every output mode, and the whole module exists to enforce
them:

1. **stdout carries data; stderr carries everything else.** So
   ``snpmemory fetch -o json | jq`` is never polluted by progress chatter.
2. **Exit 0 means stdout is trustworthy.** Nothing partial is written before a
   command is known to have succeeded.
3. **No ANSI when stdout is not a terminal.** Escape codes are the classic way
   machine-read output gets corrupted, and `NO_COLOR` is honoured besides.
"""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum
from typing import IO, Any

import yaml

from scout.cli.result import CommandResult


class OutputFormat(StrEnum):
    """Values accepted by ``--output`` / ``-o``."""

    AUTO = "auto"
    TEXT = "text"
    JSON = "json"
    YAML = "yaml"

    def resolve(self) -> OutputFormat:
        """Resolve ``auto``.

        ``auto`` becomes **text**, including when piped. The prevailing
        convention is to emit structured output when stdout is not a terminal,
        but this repository's CI steps and agent workflows read the text output
        of these commands today; switching silently would break them. Machines
        ask for ``-o json`` explicitly, and an explicit format always wins.
        """
        return OutputFormat.TEXT if self is OutputFormat.AUTO else self

    @property
    def is_structured(self) -> bool:
        return self.resolve() in (OutputFormat.JSON, OutputFormat.YAML)


def use_color(stream: IO[str], *, force: bool | None = None) -> bool:
    """Whether ANSI may be written to `stream`.

    `force` is `--color` / `--no-color`. Otherwise: never when redirected, never
    under `NO_COLOR`, and never when `TERM=dumb`.
    """
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _dump(data: Any, fmt: OutputFormat) -> str:
    if fmt is OutputFormat.JSON:
        return json.dumps(data, indent=2, sort_keys=True, default=str)
    return yaml.safe_dump(data, sort_keys=True, default_flow_style=False).rstrip("\n")


def render(
    result: CommandResult,
    fmt: OutputFormat = OutputFormat.AUTO,
    *,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Write `result` to the streams and return its exit code.

    The return value is the exit code so a caller can ``raise SystemExit(...)``
    on it, keeping process control in one place.
    """
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    resolved = fmt.resolve()

    # Diagnostics first, and always to stderr, so they precede the payload in a
    # terminal without ever entering a pipe.
    for message in result.messages:
        print(message, file=err)

    if resolved.is_structured:
        if result.data:
            print(_dump(result.data, resolved), file=out)
        if result.error is not None:
            # Last line of stderr, so a reader can take the final line and parse
            # it without scanning whatever came before.
            print(json.dumps(result.error.to_dict(), sort_keys=True, default=str), file=err)
    else:
        if result.error is not None:
            print(result.error.message, file=err)
            if result.error.hint:
                print(f"hint: {result.error.hint}", file=err)
        elif result.summary:
            print(result.summary, file=out)

    return int(result.exit_code)
