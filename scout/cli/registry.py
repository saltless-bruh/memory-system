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


#: How `Effect` reads in The CLI Spec's vocabulary. The internal enum is kept as
#: it is -- it is what `mcp_policy` and the MCP annotations already branch on --
#: and translated only at the boundary, so no existing consumer changes.
_EFFECT_TO_SPEC: dict[Effect, str] = {
    Effect.READ: "read_only",
    Effect.WRITE: "idempotent",
    Effect.DESTRUCTIVE: "non_idempotent",
}


class Cardinality(StrEnum):
    """How many records a command returns. Drives whether paging is required."""

    #: One record, or one document that is not a collection.
    SINGLE = "single"
    #: A collection whose size the caller's own input fixes.
    BOUNDED = "bounded"
    #: A collection that can grow without limit; the spec then requires
    #: `pagination` **and** the arguments that drive it.
    UNBOUNDED = "unbounded"


@dataclass(frozen=True, slots=True)
class ArgSpec:
    """One argument, described so an agent never has to parse `--help`.

    `name` carries its dashes (`"--confirm"`), because the spec's referential
    rules point at this exact string: a command's `confirmation_bypass_arg` and
    any pagination argument must match a declared `name`.
    """

    name: str
    #: Free-form by design: "string", "integer", "boolean", "path", "string[]".
    type: str = "string"
    #: Single-character alias, written with its dash (`"-o"`).
    short: str | None = None
    required: bool = False
    default: Any = None
    #: The complete accepted set, when the argument is closed.
    enum: tuple[str, ...] = ()
    description: str = ""

    def to_schema(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.short:
            payload["short"] = self.short
        if self.required:
            payload["required"] = True
        if self.default is not None:
            payload["default"] = self.default
        if self.enum:
            payload["enum"] = list(self.enum)
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field of a command's structured output.

    Declared so a consumer knows the shape without running the command --
    "discovery by invocation" is precisely what the spec exists to remove.
    Unlike an argument's free-form type, these are exact, because they drive
    parsing.
    """

    name: str
    #: One of: string, integer, number, boolean, object, array.
    type: str
    nullable: bool = False
    enum: tuple[str, ...] = ()
    #: For an object, its own fields. For an array, use `items`.
    fields: tuple[FieldSpec, ...] = ()
    items: FieldSpec | None = None
    description: str = ""

    def __post_init__(self) -> None:
        # The spec requires `items` on an array, and it is right to: an array
        # whose element shape is undeclared tells a consumer nothing it did not
        # already know.
        if self.type == "array" and self.items is None:
            raise ValueError(f"array field {self.name!r} must declare items")

    def to_schema(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.nullable:
            payload["nullable"] = True
        if self.enum:
            payload["enum"] = list(self.enum)
        if self.fields:
            payload["fields"] = [field.to_schema() for field in self.fields]
        if self.items is not None:
            payload["items"] = self.items.to_schema()
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class Pagination:
    """How a consumer walks an unbounded result."""

    style: str  # "cursor" | "offset" | "none"
    limit_arg: str | None = None
    offset_arg: str | None = None
    cursor_arg: str | None = None

    def to_schema(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"style": self.style}
        for key in ("limit_arg", "offset_arg", "cursor_arg"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload


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
    #: Arguments beyond the tool's global ones. Declaring none says the command
    #: takes none, so a command with flags MUST list them.
    args: tuple[ArgSpec, ...] = ()
    cardinality: Cardinality = Cardinality.SINGLE
    pagination: Pagination | None = None
    #: The flag that turns a refusal into an execution, e.g. `"--confirm"`.
    confirmation_bypass_arg: str | None = None
    requires_tty: bool = False
    stability: str = "stable"
    #: A realistic invocation -- the arguments after the command name -- so an
    #: agent can adapt a call without guessing at flag order, and so conformance
    #: tooling can exercise the command without inventing a positional.
    example: tuple[str, ...] = ()
    #: The structured payload's fields. A data command MUST describe its output
    #: with these or with `stdout_schema`; declaring neither leaves a consumer
    #: to learn the shape by running the command.
    output_fields: tuple[FieldSpec, ...] = ()
    #: The alternative to `output_fields`. An empty mapping is the explicit
    #: statement that the shape follows the caller's input.
    stdout_schema: dict[str, Any] | None = None
    #: Argument selecting which fields appear. Required on unbounded commands.
    fields_arg: str | None = None

    def load(self) -> Callable[..., CommandResult]:
        """Import the implementation. Called only when the command runs."""
        module_name, _, attribute = self.target.rpartition(":")
        module = importlib.import_module(module_name)
        function: Callable[..., CommandResult] = getattr(module, attribute)
        return function

    def to_schema(self) -> dict[str, Any]:
        """The CLI Spec record for this command.

        `summary`, `effect`, `prerequisite` and the `meaning` key are retained
        alongside their spec-named equivalents. Unknown properties are permitted
        at every level of the spec, and schema output an agent has already read
        is itself a contract -- renaming a key is the breakage the stability
        rule exists to prevent.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.summary,
            "effects": _EFFECT_TO_SPEC[self.effect],
            "cardinality": self.cardinality.value,
            "stability": self.stability,
            # retained for existing consumers
            "summary": self.summary,
            "prerequisite": self.prerequisite.value,
            "effect": self.effect.value,
            # At command level the spec takes *references*: the names of
            # outcomes and error kinds declared once at the top level, so a
            # consumer resolves an exit code in one place instead of finding it
            # restated per command and drifting.
            "outcomes": [code.name.lower() for code in self.outcomes],
            "errors": [kind.value for kind in self.errors],
            # The resolved codes, for a reader that would otherwise have to
            # join against the top-level tables itself. Not part of the spec.
            "outcome_codes": [
                {"code": int(ExitCode.SUCCESS), "meaning": "success"},
                *(
                    {"code": int(code), "meaning": code.name.lower()}
                    for code in self.outcomes
                ),
            ],
            "error_codes": [
                {"kind": kind.value, "code": int(kind.exit_code)}
                for kind in self.errors
            ],
        }
        if self.args:
            payload["args"] = [arg.to_schema() for arg in self.args]
        if self.pagination is not None:
            payload["pagination"] = self.pagination.to_schema()
        if self.confirmation_bypass_arg:
            payload["confirmation_bypass_arg"] = self.confirmation_bypass_arg
        if self.requires_tty:
            payload["requires_tty"] = True
        if self.example:
            payload["example"] = {"args": list(self.example)}
        if self.output_fields:
            payload["output_fields"] = [f.to_schema() for f in self.output_fields]
        if self.stdout_schema is not None:
            payload["stdout_schema"] = self.stdout_schema
        if self.fields_arg:
            payload["fields_arg"] = self.fields_arg
        return payload


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
        """The whole capability description an agent reads instead of `--help`.

        Conforms to The CLI Spec v0.3 (`tests/fixtures/clispec-v0.3.json`), and
        keeps this repository's older keys beside the spec-named ones because
        unknown properties are permitted at every level and something may
        already be reading them.
        """
        return {
            # ── The CLI Spec v0.3 required surface ────────────────────────
            "clispec": "0.3",
            "name": "snpmemory",
            "version": _tool_version(),
            "description": (
                "SNP Memory System - dual-layer knowledge vault operations."
            ),
            # `auto` resolves to text even when piped. The spec allows a
            # human-readable default only for a tool that declares it here, so
            # this field is what makes a deliberate divergence conformant
            # rather than silent: CI steps and agent workflows read the text
            # output of these commands today.
            "output": {"tty": "text", "piped": "text"},
            "global_args": [arg.to_schema() for arg in GLOBAL_ARGS],
            "errors": [
                {
                    "kind": kind.value,
                    "exit_code": int(kind.exit_code),
                    "retryable": kind is ErrorKind.INFRASTRUCTURE,
                }
                for kind in ErrorKind
            ],
            "outcomes": [
                {
                    "code": int(ExitCode.SEMANTIC_FAILURE),
                    "name": "semantic_failure",
                    "description": (
                        "the check ran and found real problems; stdout carries "
                        "the findings and no error envelope is written"
                    ),
                }
            ],
            # ── retained for existing consumers ───────────────────────────
            "tool": "snpmemory",
            "spec": "docs/CLI_SPEC.md",
            "output_formats": ["auto", "text", "json", "yaml"],
            "exit_codes": [
                {
                    "code": int(code),
                    "name": code.name.lower(),
                    "class": "outcome"
                    if code <= ExitCode.SEMANTIC_FAILURE
                    else "error",
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


#: Accepted by every command, so each one does not repeat them. The spec's
#: referential rule reads argument names from here as well as from a command's
#: own `args`.
GLOBAL_ARGS: tuple[ArgSpec, ...] = (
    ArgSpec(
        name="--output",
        type="string",
        short="-o",
        default="auto",
        enum=("auto", "text", "json", "yaml"),
        description=(
            "Output format. `--format` is accepted as an alias. An explicit "
            "value always wins over TTY detection."
        ),
    ),
    ArgSpec(
        name="--no-color",
        type="boolean",
        description="Never write ANSI, whatever stdout is attached to.",
    ),
)


def _tool_version() -> str:
    """The installed package version, or a placeholder outside an install.

    `schema` must answer with no configuration and no network, so a missing
    distribution is reported rather than raised.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("snp-memory-system")
    except PackageNotFoundError:  # pragma: no cover - only outside an install
        return "0+unknown"


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
    args: tuple[ArgSpec, ...] = (),
    cardinality: Cardinality = Cardinality.SINGLE,
    pagination: Pagination | None = None,
    confirmation_bypass_arg: str | None = None,
    requires_tty: bool = False,
    stability: str = "stable",
    example: tuple[str, ...] = (),
    output_fields: tuple[FieldSpec, ...] = (),
    stdout_schema: dict[str, Any] | None = None,
    fields_arg: str | None = None,
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
            args=args,
            cardinality=cardinality,
            pagination=pagination,
            confirmation_bypass_arg=confirmation_bypass_arg,
            requires_tty=requires_tty,
            stability=stability,
            example=example,
            output_fields=output_fields,
            stdout_schema=stdout_schema,
            fields_arg=fields_arg,
        )
    )
