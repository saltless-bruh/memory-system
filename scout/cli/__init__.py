"""The `snpmemory` command-line surface.

One dispatcher, three consumers: a person at a terminal, a CI step reading an
exit code, and an agent parsing structured output. Commands return
`CommandResult`; rendering is the renderer's job and process control is the
dispatcher's. See `docs/CLI_SPEC.md`.
"""

from scout.cli.result import CommandResult, ErrorKind, ExitCode

__all__ = ["CommandResult", "ErrorKind", "ExitCode"]
