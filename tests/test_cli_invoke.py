"""The shared invocation path: lazy import, declaration-driven configuration."""

from __future__ import annotations

from typing import Any

import pytest

from scout.cli.invoke import invoke
from scout.cli.registry import CommandSpec, Prerequisite
from scout.cli.result import CommandResult, ExitCode


def _result(**data: Any) -> CommandResult:
    return CommandResult(exit_code=ExitCode.SUCCESS, data=data, summary="ok")


def test_a_none_command_is_given_no_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`schema` must not be able to read a credential, structurally."""
    seen: dict[str, Any] = {}

    def _impl(*, config: Any = "UNSET") -> CommandResult:
        seen["config"] = config
        return _result()

    spec = CommandSpec(
        name="fake-none",
        summary="s",
        target="x:y",
        prerequisite=Prerequisite.NONE,
    )
    monkeypatch.setattr(CommandSpec, "load", lambda _self: _impl)
    resolved: list[Prerequisite] = []
    monkeypatch.setattr(
        "scout.cli.invoke.resolve_config",
        lambda prerequisite: resolved.append(prerequisite) or None,
    )

    invoke(spec)

    assert resolved == [Prerequisite.NONE]
    assert seen["config"] is None


def test_config_is_not_injected_into_a_command_that_does_not_accept_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _impl(value: int) -> CommandResult:
        return _result(value=value)

    spec = CommandSpec(name="fake", summary="s", target="x:y")
    monkeypatch.setattr(CommandSpec, "load", lambda _self: _impl)
    monkeypatch.setattr("scout.cli.invoke.resolve_config", lambda _p: object())

    result = invoke(spec, 7)

    assert result.data == {"value": 7}


def test_the_implementation_is_imported_only_when_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registering or describing a command must not import its module."""
    loads: list[int] = []

    def _load(_self: CommandSpec) -> Any:
        loads.append(1)
        return lambda **_k: _result()

    spec = CommandSpec(name="fake", summary="s", target="x:y")
    monkeypatch.setattr(CommandSpec, "load", _load)
    monkeypatch.setattr("scout.cli.invoke.resolve_config", lambda _p: None)

    assert loads == []
    invoke(spec)
    assert loads == [1]
