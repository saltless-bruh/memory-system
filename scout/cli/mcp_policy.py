"""Which declared commands become MCP tools, and under what name.

`registry.py` is the single source of truth for what a command *is*. This
module is the single source of truth for how it is *exposed* — kept separate
because the two answers differ: `schema` is a first-class CLI command and a
redundant MCP tool, since an MCP client lists tools natively.

Exposure is not 1:1 on purpose. A tool definition costs roughly 100-500 tokens
of every agent's context, and a long tool list measurably degrades tool
selection — agents call the wrong tool. Eight commands next to `rag_fetch` and
basic-memory's tools is a crowded list for no benefit, so the five verification
commands collapse into one `verify` tool with a `stage` argument. That is the
shape `check` already had.

The invariant that keeps this honest: **every command in the registry must
appear here exactly once.** A new command with no exposure decision fails the
test suite rather than silently becoming a tool, or silently not becoming one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from scout.cli.declarations import DECLARED
from scout.cli.registry import CommandSpec


class Exposure(StrEnum):
    """What happens to a command at the MCP boundary."""

    #: Becomes its own tool.
    TOOL = "tool"
    #: Becomes one `stage` of a grouped tool.
    GROUPED = "grouped"
    #: Deliberately not exposed.
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """The exposure decision for one command."""

    command: str
    exposure: Exposure
    #: For TOOL, the tool name. For GROUPED, the tool it joins.
    tool: str = ""
    #: For GROUPED, the value of the tool's `stage` argument.
    stage: str = ""
    #: Why, when the answer is not obvious.
    reason: str = ""


#: The grouped verification tool's default stage — the full aggregate.
VERIFY_TOOL = "verify"
DEFAULT_VERIFY_STAGE = "all"

POLICIES: tuple[ToolPolicy, ...] = (
    ToolPolicy(
        "schema",
        Exposure.HIDDEN,
        reason="an MCP client lists tools natively; a schema tool is redundant context",
    ),
    ToolPolicy("check", Exposure.GROUPED, tool=VERIFY_TOOL, stage=DEFAULT_VERIFY_STAGE),
    ToolPolicy("verify-vault", Exposure.GROUPED, tool=VERIFY_TOOL, stage="vault"),
    ToolPolicy("verify-secrets", Exposure.GROUPED, tool=VERIFY_TOOL, stage="secrets"),
    ToolPolicy(
        "verify-addresses", Exposure.GROUPED, tool=VERIFY_TOOL, stage="addresses"
    ),
    ToolPolicy(
        "verify-groundedness", Exposure.GROUPED, tool=VERIFY_TOOL, stage="groundedness"
    ),
    ToolPolicy(
        "mcp",
        Exposure.HIDDEN,
        reason="the server cannot serve itself; an agent is already connected to it",
    ),
    ToolPolicy("plan-articles", Exposure.TOOL, tool="plan_articles"),
    ToolPolicy("compile-plan", Exposure.TOOL, tool="compile_plan"),
)

_BY_COMMAND = {policy.command: policy for policy in POLICIES}


def policy_for(command: str) -> ToolPolicy | None:
    return _BY_COMMAND.get(command)


def undecided_commands() -> tuple[str, ...]:
    """Registry commands with no exposure decision. Must always be empty."""
    return tuple(spec.name for spec in DECLARED if spec.name not in _BY_COMMAND)


def unknown_policies() -> tuple[str, ...]:
    """Policies naming a command that no longer exists. Must always be empty."""
    known = {spec.name for spec in DECLARED}
    return tuple(p.command for p in POLICIES if p.command not in known)


def stages() -> dict[str, CommandSpec]:
    """Stage name → the command it runs, for the grouped verify tool."""
    by_name = {spec.name: spec for spec in DECLARED}
    return {
        policy.stage: by_name[policy.command]
        for policy in POLICIES
        if policy.exposure is Exposure.GROUPED and policy.command in by_name
    }


def standalone_tools() -> dict[str, CommandSpec]:
    """Tool name → the command it runs, for ungrouped tools."""
    by_name = {spec.name: spec for spec in DECLARED}
    return {
        policy.tool: by_name[policy.command]
        for policy in POLICIES
        if policy.exposure is Exposure.TOOL and policy.command in by_name
    }
