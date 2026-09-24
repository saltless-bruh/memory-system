"""The exposure policy, and the guard that keeps it from drifting."""

from __future__ import annotations

from scout.cli.declarations import DECLARED
from scout.cli.mcp_policy import (
    POLICIES,
    Exposure,
    scout_surface,
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
    """Tool-list bloat degrades selection; retrieval is the whole surface."""
    tool_names = set(scout_surface().values())
    assert len(tool_names) < len(DECLARED)
    assert tool_names == {"wiki_search", "wiki_read", "wiki_quote"}


def test_nothing_is_exposed_on_a_server_that_no_longer_exists() -> None:
    """Every exposed command names the one server that serves it.

    Until leaf-4.3 a second value, `local`, meant the stdio server. That server
    is deleted, so an entry still claiming it would be exposed in the table and
    served by nothing — the drift `scout_surface()` exists to prevent, pointing
    the other way.
    """
    exposed = [p for p in POLICIES if p.exposure is Exposure.TOOL]
    assert exposed, "the retrieval surface must not be empty"
    assert {p.surface for p in exposed} == {"scout"}
    assert len(scout_surface()) == len(exposed)


def test_a_command_the_stdio_server_used_to_serve_is_hidden_with_its_reason() -> None:
    """Retiring a surface must leave a decision behind, not a gap.

    These four were tools on the deleted stdio server. Dropping their rows
    would have re-opened them as undecided commands; leaving them TOOL would
    have declared a surface nothing serves.
    """
    by_command = {p.command: p for p in POLICIES}
    for command in ("check", "plan-articles", "compile-plan", "compile-status"):
        policy = by_command[command]
        assert policy.exposure is Exposure.HIDDEN
        assert "leaf-4.3" in policy.reason


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
