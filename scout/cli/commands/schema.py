"""`snpmemory schema` — the capability description agents read instead of --help."""

from __future__ import annotations

from scout.cli.registry import REGISTRY
from scout.cli.result import CommandResult


def schema() -> CommandResult:
    """Describe every command, output format, exit code, and error kind.

    Deliberately dependency-free: no authentication, no configuration file, no
    network, no database. An agent must be able to discover what this tool can
    do before it can do any of it.
    """
    document = REGISTRY.to_schema()
    return CommandResult(
        data=document,
        summary=(
            f"snpmemory: {len(document['commands'])} command(s), "
            f"{len(document['output_formats'])} output formats. "
            "Use -o json for the machine-readable form."
        ),
    )
