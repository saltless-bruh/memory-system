"""`snpmemory schema` — the capability description agents read instead of --help."""

from __future__ import annotations

# Importing the declarations is what populates `REGISTRY`. Without it, calling
# `schema()` directly -- from a test, from the MCP layer, from anything that did
# not come through `app.py` -- returns an empty document that looks like a tool
# with no commands. The module declares only; it imports no implementation.
from scout.cli import declarations as _declarations  # noqa: F401
from scout.cli.errors import input_error
from scout.cli.registry import REGISTRY
from scout.cli.result import CommandResult


def schema(command: str | None = None) -> CommandResult:
    """Describe every command, output format, exit code, and error kind.

    Deliberately dependency-free: no authentication, no configuration file, no
    network, no database. An agent must be able to discover what this tool can
    do before it can do any of it.

    Args:
        command: Narrow the document to one command. The tool-level fields stay,
            because a consumer reading one command still needs the exit codes and
            error kinds it references. An unknown name is a caller mistake
            (exit 3), never an empty document -- silently returning nothing is
            how a typo becomes "that command does not exist".
    """
    document = REGISTRY.to_schema()
    if command is not None:
        matched = [entry for entry in document["commands"] if entry["name"] == command]
        if not matched:
            known = ", ".join(sorted(entry["name"] for entry in document["commands"]))
            raise input_error(
                f"unknown command {command!r}",
                hint=f"known commands: {known}",
            )
        document = {**document, "commands": matched}
    return CommandResult(
        data=document,
        summary=(
            f"snpmemory: {len(document['commands'])} command(s), "
            f"{len(document['output_formats'])} output formats. "
            "Use -o json for the machine-readable form."
        ),
    )
