"""Which declared commands become MCP tools, and under what name.

`registry.py` is the single source of truth for what a command *is*. This
module is the single source of truth for how it is *exposed* — kept separate
because the two answers differ: `schema` is a first-class CLI command and a
redundant MCP tool, since an MCP client lists tools natively.

Exposure is not 1:1 on purpose. A tool definition costs roughly 100-500 tokens
of every agent's context, and a long tool list measurably degrades tool
selection — agents call the wrong tool. Retrieval plus every operator command
would be a crowded list for no benefit, so the five verification
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
    ToolPolicy(
        "mcp-config",
        Exposure.HIDDEN,
        reason=(
            "an agent reading this tool is already connected; the command "
            "exists to get it connected in the first place, and it writes into "
            "a config file the user owns"
        ),
    ),
    ToolPolicy(
        "ingest",
        Exposure.HIDDEN,
        reason=(
            "the sync-job already indexes raw/ on a watch; an agent-triggered "
            "re-index spends embedding calls on a corpus it does not own"
        ),
    ),
    ToolPolicy(
        "gate",
        Exposure.HIDDEN,
        reason=(
            "the gate branches, commits and pushes on a schedule; it is CI's "
            "entry point, and an agent triggering it would bypass the review "
            "the gate exists to feed"
        ),
    ),
    ToolPolicy(
        "heal",
        Exposure.HIDDEN,
        reason=(
            "rewriting sources[] on a live vault is the gate's job under human "
            "review (R-6.4); direct healer use is explicitly not the CI gate"
        ),
    ),
    ToolPolicy(
        "status",
        Exposure.HIDDEN,
        reason=(
            "an agent that cannot start the stack has no use for its health; "
            "diagnosing a dead stack is the operator's job at a terminal"
        ),
    ),
    ToolPolicy("up", Exposure.HIDDEN, reason="lifecycle is an operator decision"),
    ToolPolicy("down", Exposure.HIDDEN, reason="lifecycle is an operator decision"),
    ToolPolicy("logs", Exposure.HIDDEN, reason="lifecycle is an operator decision"),
    ToolPolicy(
        "init",
        Exposure.HIDDEN,
        reason="generates credentials; never something an agent should trigger",
    ),
    ToolPolicy(
        "mint",
        Exposure.HIDDEN,
        reason=(
            "compile_plan already mints every address it needs; a standalone "
            "minting tool is a sub-step an agent does not have to drive, and "
            "each tool definition costs context on every call"
        ),
    ),
    ToolPolicy(
        "compile",
        Exposure.HIDDEN,
        reason=(
            "compile_plan covers the same ground for one article as for many, "
            "and two compilation tools invite the wrong one being picked; the "
            "single-page path stays a CLI operation"
        ),
    ),
    ToolPolicy(
        "propose",
        Exposure.HIDDEN,
        reason=(
            "branching, committing and pushing are the human's decision under "
            "R-6.4/R-7.3; an agent that could open its own PR would be reviewing "
            "its own work"
        ),
    ),
    ToolPolicy(
        "fetch",
        Exposure.HIDDEN,
        reason=(
            "V3 withdraws direct address retrieval from the agent surface; "
            "the operator command remains available for diagnostics only"
        ),
    ),
    ToolPolicy("search", Exposure.TOOL, tool="wiki_search"),
    ToolPolicy("read", Exposure.TOOL, tool="wiki_read"),
    ToolPolicy(
        "install-agent",
        Exposure.HIDDEN,
        reason=(
            "installs instructions into a directory the user owns; an agent "
            "that could install its own operating contract elsewhere is a "
            "decision nobody made"
        ),
    ),
    ToolPolicy(
        "compile-cancel",
        Exposure.HIDDEN,
        reason=(
            "the tool surface stays deliberately small, and an agent that started "
            "a batch can stop caring about it — stopping one is the operator's "
            "job at a terminal. A task-capable client gets tasks/cancel natively"
        ),
    ),
    ToolPolicy("plan-articles", Exposure.TOOL, tool="plan_articles"),
    ToolPolicy("compile-plan", Exposure.TOOL, tool="compile_plan"),
    ToolPolicy("compile-status", Exposure.TOOL, tool="compile_status"),
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
