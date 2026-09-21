"""The exposure policy, and the guard that keeps it from drifting."""

from __future__ import annotations

import asyncio

from scout.cli.declarations import DECLARED
from scout.cli.mcp_policy import (
    DEFAULT_VERIFY_STAGE,
    POLICIES,
    VERIFY_TOOL,
    Exposure,
    stages,
    standalone_tools,
    undecided_commands,
    unknown_policies,
)


def test_every_registered_command_has_an_exposure_decision() -> None:
    """A new command must not silently become — or not become — a tool.

    This is the guard `registry.py` exists for. If it fails, add the command to
    POLICIES; deciding is the point, and either answer is acceptable.
    """
    assert undecided_commands() == ()


def test_no_policy_names_a_command_that_no_longer_exists() -> None:
    assert unknown_policies() == ()


def test_each_command_appears_exactly_once() -> None:
    names = [policy.command for policy in POLICIES]
    assert len(names) == len(set(names))


def test_the_tool_surface_is_smaller_than_the_command_surface() -> None:
    """Tool-list bloat degrades selection; collapsing the verify family is why."""
    tool_names = set(standalone_tools()) | {"verify"}
    assert len(tool_names) < len(DECLARED)
    assert tool_names == {
        "wiki_search",
        "wiki_read",
        "verify",
        "plan_articles",
        "compile_plan",
        "compile_status",
    }


def test_verify_stages_cover_the_whole_family() -> None:
    assert set(stages()) == {
        DEFAULT_VERIFY_STAGE,
        "vault",
        "secrets",
        "addresses",
        "groundedness",
        "extraction",
    }


def test_the_served_stage_argument_accepts_exactly_the_declared_stages() -> None:
    """The tool's schema and its own description must name the same stages.

    The served `stage` argument is a hardcoded `Literal`, while the description
    is built from `stages()`. Adding `verify-extraction` to the policy alone
    advertised a stage the schema would have rejected -- a tool that documents
    a call it refuses.
    """
    import typing

    from scout.mcp.local_server import build_server

    server = build_server()
    tool = asyncio.run(server.get_tool(VERIFY_TOOL))
    # `from __future__ import annotations` leaves the signature holding the
    # source string, so resolve it rather than reading `__annotations__` raw.
    hint = typing.get_type_hints(tool.fn, include_extras=True)["stage"]
    literal = typing.get_args(hint)[0]
    assert set(typing.get_args(literal)) == set(stages())


def test_hidden_commands_state_a_reason() -> None:
    for policy in POLICIES:
        if policy.exposure is Exposure.HIDDEN:
            assert policy.reason, f"{policy.command} is hidden without a reason"


def test_declarations_survive_import_without_the_dispatcher() -> None:
    """The dispatcher once lost every command to a deleted side-effect import.

    Consumers must be able to hold the declarations as a value, so a formatter
    removing an "unused" import cannot silently empty the command surface.
    """
    assert len(DECLARED) >= 8
    assert "schema" in {spec.name for spec in DECLARED}
