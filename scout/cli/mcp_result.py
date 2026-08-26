"""Turning a `CommandResult` into what an MCP tool returns.

One distinction decides this whole module, and getting it wrong is dangerous:

* **Exit 1 is a finding.** The check ran; the vault has a problem. That is an
  answer an agent must read and reason about, so it comes back as a *successful*
  tool call carrying `status: "fail"`. Raising here would tell the agent the
  tool is broken when in fact the vault is.
* **Exit 2-7 mean the command could not run.** They come back as tool *errors*.
  Exit 2 in particular must never look like a result: `ci_address_gate.py`'s
  contract is that infrastructure failure never authorises mutation, and an
  agent that read a database outage as "no problems found" would heal on it.

Payloads are summary-first. A tool that returns every field fills an agent's
context; detail is available on request instead of by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scout.cli.result import CommandResult, ExitCode

#: Fields worth returning even in summary mode, when a command emits them.
_ALWAYS_KEEP = ("status", "reason", "published", "written_to", "source", "plan")
#: Beyond this many list entries, summary mode reports a count instead.
_MAX_SUMMARY_ITEMS = 20


class ToolFailure(RuntimeError):
    """A command could not run. Surfaces to the client as a tool error.

    Carries the machine-stable kind and exit code so an agent can branch
    without parsing prose — and so exit 2 stays distinguishable from exit 3.
    """

    def __init__(self, result: CommandResult) -> None:
        envelope = result.error
        kind = envelope.kind.value if envelope else "infrastructure"
        message = envelope.message if envelope else "command failed"
        hint = getattr(envelope, "hint", None) if envelope else None
        super().__init__(f"[{kind}] {message}" + (f" — {hint}" if hint else ""))
        self.kind = kind
        self.exit_code = int(result.exit_code)
        self.retryable = bool(getattr(envelope, "retryable", False))
        self.mutating_is_allowed = result.mutating_is_allowed


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """A successful tool call's payload."""

    ok: bool
    exit_code: int
    summary: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "summary": self.summary,
            **self.data,
        }


def _summarize(data: dict[str, Any]) -> dict[str, Any]:
    """Keep decisions possible without filling the agent's context."""
    reduced: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, list) and len(value) > _MAX_SUMMARY_ITEMS:
            reduced[key] = {
                "count": len(value),
                "truncated": True,
                "first": value[:_MAX_SUMMARY_ITEMS],
            }
        elif isinstance(value, list | dict) and key not in _ALWAYS_KEEP:
            reduced[key] = value
        else:
            reduced[key] = value
    return reduced


def to_tool_result(result: CommandResult, *, detail: bool = False) -> dict[str, Any]:
    """Map one command result onto an MCP tool return, or raise `ToolFailure`.

    Raises for exit codes 2-7 only. Exit 1 returns normally with `ok: False`.
    """
    if result.exit_code >= ExitCode.INFRASTRUCTURE:
        raise ToolFailure(result)
    data = dict(result.data) if detail else _summarize(dict(result.data))
    return ToolOutcome(
        ok=result.ok,
        exit_code=int(result.exit_code),
        summary=result.summary,
        data=data,
    ).to_dict()


def run_tool(
    spec: Any, *args: Any, detail: bool = False, **kwargs: Any
) -> dict[str, Any]:
    """Invoke a command and map it onto an MCP tool return.

    Commands signal refusals and configuration problems by raising `CliError` —
    the dispatcher catches those and renders one envelope. The MCP boundary
    needs the same catch, or a routine refusal (`compile_plan` without
    `confirm`) reaches the client as an unhandled exception with a traceback
    attached, which both looks like a crash and leaks internals.
    """
    from scout.cli.errors import CliError
    from scout.cli.invoke import invoke

    try:
        result = invoke(spec, *args, **kwargs)
    except CliError as exc:
        raise ToolFailure(exc.to_result()) from None
    return to_tool_result(result, detail=detail)
