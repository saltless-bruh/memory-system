"""The MCP result mapping, and the one distinction that must never blur."""

from __future__ import annotations

import pytest

from scout.cli.mcp_result import ToolFailure, to_tool_result
from scout.cli.result import CommandResult, ErrorEnvelope, ErrorKind, ExitCode

_ERROR_CODES = [c for c in ExitCode if c >= ExitCode.INFRASTRUCTURE]
_KIND_FOR = {kind.exit_code: kind for kind in ErrorKind}


def _error_result(code: ExitCode) -> CommandResult:
    kind = _KIND_FOR[code]
    return CommandResult(
        exit_code=code,
        error=ErrorEnvelope(kind=kind, message=f"{kind.value} happened"),
    )


def test_success_is_a_result() -> None:
    payload = to_tool_result(
        CommandResult(exit_code=ExitCode.SUCCESS, data={"pages": 5}, summary="fine")
    )
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["pages"] == 5


def test_a_semantic_failure_is_a_result_the_agent_can_reason_about() -> None:
    """Exit 1 means the vault has a problem, not that the tool is broken."""
    payload = to_tool_result(
        CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"status": "fail", "errors": ["drift"]},
            summary="1 drift",
        )
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["status"] == "fail"


@pytest.mark.parametrize("code", _ERROR_CODES)
def test_every_error_code_raises_rather_than_returning(code: ExitCode) -> None:
    with pytest.raises(ToolFailure) as caught:
        to_tool_result(_error_result(code))
    assert caught.value.exit_code == int(code)


def test_infrastructure_failure_never_appears_as_a_successful_result() -> None:
    """ci_address_gate's contract: exit 2 never authorises mutation.

    An agent that read a database outage as "no problems found" would heal on
    it, which is the specific accident this mapping exists to prevent.
    """
    with pytest.raises(ToolFailure) as caught:
        to_tool_result(_error_result(ExitCode.INFRASTRUCTURE))
    assert caught.value.kind == "infrastructure"
    assert caught.value.exit_code == 2
    assert caught.value.mutating_is_allowed is False


def test_error_kinds_stay_distinguishable() -> None:
    """An agent must be able to tell a bad argument from a dead database."""
    with pytest.raises(ToolFailure) as infra:
        to_tool_result(_error_result(ExitCode.INFRASTRUCTURE))
    with pytest.raises(ToolFailure) as bad_input:
        to_tool_result(_error_result(ExitCode.INPUT_VALIDATION))
    assert infra.value.kind != bad_input.value.kind


def test_summary_mode_truncates_long_lists_but_reports_the_count() -> None:
    result = CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={"warnings": [f"w{i}" for i in range(50)]},
        summary="many",
    )
    summary = to_tool_result(result)
    assert summary["warnings"]["count"] == 50
    assert summary["warnings"]["truncated"] is True
    assert len(summary["warnings"]["first"]) == 20

    full = to_tool_result(result, detail=True)
    assert full["warnings"] == [f"w{i}" for i in range(50)]
