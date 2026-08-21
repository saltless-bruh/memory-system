"""Command declarations, and the machine-readable schema built from them.

Every command is declared here before it is implemented. The declaration is the
single source of truth for three consumers: the dispatcher (which commands
exist), ``snpmemory schema`` (what an agent may call), and — later — the MCP
server, whose tool definitions are generated from these same records rather
than written a second time and left to drift.

Implementations are imported **lazily**. `schema` must answer before anything
else works: no authentication, no configuration file, no database, no running
stack. Importing a command module that reaches for credentials at import time
would break that guarantee, so nothing is imported until it is called.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from scout.cli.result import CommandResult, ErrorKind, ExitCode


class Prerequisite(StrEnum):
    """What a command needs before it can run at all."""

    #: Needs only a server URL and a token; runs anywhere the package installed.
    REMOTE = "remote"
    #: Needs a repository checkout on disk.
    LOCAL = "local"
    #: Needs neither. `schema` and `--help` must stay in this class.
    NONE = "none"


class Effect(StrEnum):
    """What running the command does to the world."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One command, declared independently of its implementation."""

    name: str
    summary: str
    target: str
    prerequisite: Prerequisite = Prerequisite.LOCAL
    effect: Effect = Effect.READ
    #: Outcome codes this command can legitimately return (0 always implied).
    outcomes: tuple[ExitCode, ...] = ()
    #: Error kinds it can raise, so an agent can prepare for them.
    errors: tuple[ErrorKind, ...] = ()

    def load(self) -> Callable[..., CommandResult]:
        """Import the implementation. Called only when the command runs."""
        module_name, _, attribute = self.target.rpartition(":")
        module = importlib.import_module(module_name)
        function: Callable[..., CommandResult] = getattr(module, attribute)
        return function

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "prerequisite": self.prerequisite.value,
            "effect": self.effect.value,
            "outcomes": [
                {"code": int(ExitCode.SUCCESS), "meaning": "success"},
                *(
                    {"code": int(code), "meaning": code.name.lower()}
                    for code in self.outcomes
                ),
            ],
            "errors": [
                {"kind": kind.value, "code": int(kind.exit_code)} for kind in self.errors
            ],
        }


@dataclass
class Registry:
    """The set of declared commands."""

    _commands: dict[str, CommandSpec] = field(default_factory=dict)

    def add(self, spec: CommandSpec) -> CommandSpec:
        if spec.name in self._commands:
            raise ValueError(f"command already registered: {spec.name}")
        self._commands[spec.name] = spec
        return spec

    def get(self, name: str) -> CommandSpec | None:
        return self._commands.get(name)

    def __iter__(self) -> Iterator[CommandSpec]:
        return iter(sorted(self._commands.values(), key=lambda s: s.name))

    def __len__(self) -> int:
        return len(self._commands)

    def to_schema(self) -> dict[str, Any]:
        """The whole capability description an agent reads instead of `--help`."""
        return {
            "tool": "snpmemory",
            "spec": "docs/CLI_SPEC.md",
            "output_formats": ["auto", "text", "json", "yaml"],
            "exit_codes": [
                {
                    "code": int(code),
                    "name": code.name.lower(),
                    "class": "outcome" if code <= ExitCode.SEMANTIC_FAILURE else "error",
                    "mutating_allowed": code < ExitCode.INFRASTRUCTURE,
                }
                for code in ExitCode
            ],
            "error_envelope": {
                "stream": "stderr",
                "position": "last line",
                "emitted_when": "output format is structured",
                "required": ["kind", "message"],
                "optional": ["hint", "details", "retryable"],
            },
            "commands": [spec.to_schema() for spec in self],
        }


REGISTRY = Registry()


def command(
    name: str,
    summary: str,
    target: str,
    *,
    prerequisite: Prerequisite = Prerequisite.LOCAL,
    effect: Effect = Effect.READ,
    outcomes: tuple[ExitCode, ...] = (),
    errors: tuple[ErrorKind, ...] = (),
) -> CommandSpec:
    """Declare a command on the global registry."""
    return REGISTRY.add(
        CommandSpec(
            name=name,
            summary=summary,
            target=target,
            prerequisite=prerequisite,
            effect=effect,
            outcomes=outcomes,
            errors=errors,
        )
    )
