"""Which declared commands become MCP tools, and under what name.

`registry.py` is the single source of truth for what a command *is*. This
module is the single source of truth for how it is *exposed* — kept separate
because the two answers differ: `schema` is a first-class CLI command and a
redundant MCP tool, since an MCP client lists tools natively.

Exposure is not 1:1 on purpose. A tool definition costs roughly 100-500 tokens
of every agent's context, and a long tool list measurably degrades tool
selection — agents call the wrong tool. Retrieval plus every operator command
would be a crowded list for no benefit.

**Since leaf-4.3 (2026-09-24) the exposed surface is retrieval only.** The
verification, planning and compilation tools were served by a second MCP server
over stdio, which is deleted: it carried the authority of whoever launched it
and held a second copy of `wiki_search`/`wiki_read` free to drift from the
served ones. Those commands did not disappear with it — they remain CLI
commands, and ship as Agent Skills that run them — so their entries here are
`HIDDEN` with that reason, not removed. A retired exposure decision still has
to be a decision, or the invariant below cannot tell it from an oversight.

The invariant that keeps this honest: **every command in the registry must
appear here exactly once.** A new command with no exposure decision fails the
test suite rather than silently becoming a tool, or silently not becoming one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from scout.cli.declarations import DECLARED


class Exposure(StrEnum):
    """What happens to a command at the MCP boundary."""

    #: Becomes its own tool.
    TOOL = "tool"
    #: Deliberately not exposed.
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """The exposure decision for one command."""

    command: str
    exposure: Exposure
    #: For TOOL, the served tool name.
    tool: str = ""
    #: Why, when the answer is not obvious.
    reason: str = ""
    #: Which server serves it. `scout` is the authenticated retrieval server,
    #: and since leaf-4.3 it is the only one. This field exists so that server
    #: can derive its tool set from the policy instead of repeating a literal
    #: list: audit finding F1 was that `governance.py` read this table while
    #: `scout/mcp_server.py` built its tools independently, so a pass there
    #: described the declared surface and not the served one. The default is
    #: the empty string — a command is served only by saying which server
    #: serves it.
    surface: str = ""


POLICIES: tuple[ToolPolicy, ...] = (
    ToolPolicy(
        "schema",
        Exposure.HIDDEN,
        reason="an MCP client lists tools natively; a schema tool is redundant context",
    ),
    ToolPolicy(
        "check",
        Exposure.HIDDEN,
        reason=(
            "the grouped `verify` tool lived on the local stdio server, "
            "deleted in leaf-4.3; verification runs as a CLI command and "
            "ships as the snp-verify-page and snp-verify-vault skills"
        ),
    ),
    ToolPolicy(
        "verify-vault",
        Exposure.HIDDEN,
        reason=(
            "the grouped `verify` tool lived on the local stdio server, "
            "deleted in leaf-4.3; verification runs as a CLI command and "
            "ships as the snp-verify-page and snp-verify-vault skills"
        ),
    ),
    ToolPolicy(
        "verify-secrets",
        Exposure.HIDDEN,
        reason=(
            "the grouped `verify` tool lived on the local stdio server, "
            "deleted in leaf-4.3; verification runs as a CLI command and "
            "ships as the snp-verify-page and snp-verify-vault skills"
        ),
    ),
    ToolPolicy(
        "verify-addresses",
        Exposure.HIDDEN,
        reason=(
            "the grouped `verify` tool lived on the local stdio server, "
            "deleted in leaf-4.3; verification runs as a CLI command and "
            "ships as the snp-verify-page and snp-verify-vault skills"
        ),
    ),
    ToolPolicy(
        "verify-groundedness",
        Exposure.HIDDEN,
        reason=(
            "the grouped `verify` tool lived on the local stdio server, "
            "deleted in leaf-4.3; verification runs as a CLI command and "
            "ships as the snp-verify-page and snp-verify-vault skills"
        ),
    ),
    ToolPolicy(
        "verify-extraction",
        Exposure.HIDDEN,
        reason=(
            "the grouped `verify` tool lived on the local stdio server, "
            "deleted in leaf-4.3; verification runs as a CLI command and "
            "ships as the snp-verify-page and snp-verify-vault skills"
        ),
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
        "ingest-wiki",
        Exposure.HIDDEN,
        reason=(
            "the same reason as `ingest`, and more so: the sync-job's vault "
            "watcher indexes the whole vault on every publication, and an "
            "agent re-running it by hand re-embeds a corpus it does not own"
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
    # Served as `wiki_quote` since 2026-09-21 (ADR-0001). The earlier decision
    # withdrew it, on the reasoning that retrieval should end at the canonical
    # page. What that left behind was a hole: AGENTS.md forbids fabricating a
    # source or a quotation, and nothing on the surface could produce one, so an
    # agent asked for the evidence under a page could only decline. The tool is
    # still read-only and still post-filters to the addressed file.
    ToolPolicy("fetch", Exposure.TOOL, tool="wiki_quote", surface="scout"),
    ToolPolicy("search", Exposure.TOOL, tool="wiki_search", surface="scout"),
    ToolPolicy("read", Exposure.TOOL, tool="wiki_read", surface="scout"),
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
    ToolPolicy(
        "plan-articles",
        Exposure.HIDDEN,
        reason=(
            "served by the local stdio server, deleted in leaf-4.3; it runs as a "
            "CLI command and ships as the snp-plan-articles skill"
        ),
    ),
    ToolPolicy(
        "compile-plan",
        Exposure.HIDDEN,
        reason=(
            "served by the local stdio server, deleted in leaf-4.3; it runs as a "
            "CLI command and ships as the snp-compile-batch skill"
        ),
    ),
    ToolPolicy(
        "compile-status",
        Exposure.HIDDEN,
        reason=(
            "served by the local stdio server, deleted in leaf-4.3; it runs as a "
            "CLI command and ships as the snp-compile-batch skill"
        ),
    ),
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


def scout_surface() -> dict[str, str]:
    """Command name -> served tool name, for the authenticated retrieval server.

    The authenticated server builds its tools from this, so the served surface
    and the declared surface cannot answer differently. A command added here
    appears there without editing the server module; a command removed here
    disappears from it.
    """
    return {
        policy.command: policy.tool
        for policy in POLICIES
        if policy.surface == "scout" and policy.exposure is Exposure.TOOL
    }
