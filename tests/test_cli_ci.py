"""Tests for the CI family: `gate` and `heal`.

The property under test in most of these is a safety one, inherited from
`ci_address_gate.py` and promised in `README.md`: **exit 2 never triggers a
mutation.** A check that could not run has said nothing about the vault, and
healing on that would rewrite addresses on no evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.ci import gate, heal  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402


def _config(tmp_path: Path) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, repo_root=tmp_path)


# ── gate ──────────────────────────────────────────────────────────────────


def test_a_clean_gate_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("scripts.ci_address_gate.main", lambda argv: 0)
    result = gate(mode="pr", config=_config(tmp_path))
    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "clean"


def test_remaining_findings_are_an_outcome_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("scripts.ci_address_gate.main", lambda argv: 1)
    result = gate(mode="pr", config=_config(tmp_path))
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["gate_exit"] == 1


def test_gate_infrastructure_is_raised_not_reported_as_a_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 2 must not arrive as a `CommandResult` a caller could mistake for data."""
    monkeypatch.setattr("scripts.ci_address_gate.main", lambda argv: 2)
    with pytest.raises(CliError) as caught:
        gate(mode="pr", config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INFRASTRUCTURE
    assert result.error is not None
    assert "no heal was attempted" in (result.error.hint or "")


def test_an_unknown_mode_is_rejected_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "scripts.ci_address_gate.main", lambda argv: calls.append(argv) or 0
    )
    with pytest.raises(CliError) as caught:
        gate(mode="nightly", config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION
    assert calls == []


def test_gate_flags_reach_the_underlying_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.ci_address_gate.main", lambda argv: seen.append(argv) or 0
    )
    gate(
        mode="scheduled",
        branch="heal/addresses-x",
        advisory_groundedness=True,
        config=_config(tmp_path),
    )
    assert seen[0] == [
        "--mode",
        "scheduled",
        "--remote",
        "origin",
        "--branch",
        "heal/addresses-x",
        "--advisory-groundedness",
    ]


# ── heal ──────────────────────────────────────────────────────────────────


def test_heal_without_confirm_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []

    async def _never(*a: object, **k: object) -> int:
        calls.append(a)
        return 0

    monkeypatch.setattr("scout.healer.verify_and_heal_vault", _never)
    monkeypatch.setattr("scout.cli.commands.ci._backend", lambda cfg: object())

    with pytest.raises(CliError) as caught:
        heal(config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.CONFIRMATION_REQUIRED
    assert calls == []


def test_dry_run_needs_no_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Seeing what would change must not itself require authorising a change."""
    seen: dict[str, object] = {}

    async def _run(
        backend: object, *, ci_mode: bool = False, dry_run: bool = False
    ) -> int:
        seen["dry_run"] = dry_run
        return 0

    monkeypatch.setattr("scout.healer.verify_and_heal_vault", _run)
    monkeypatch.setattr("scout.cli.commands.ci._backend", lambda cfg: object())

    result = heal(dry_run=True, config=_config(tmp_path))
    assert result.exit_code == ExitCode.SUCCESS
    assert seen["dry_run"] is True
    assert result.data["dry_run"] is True


def test_heal_infrastructure_is_raised_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _broken(*a: object, **k: object) -> int:
        return 2

    monkeypatch.setattr("scout.healer.verify_and_heal_vault", _broken)
    monkeypatch.setattr("scout.cli.commands.ci._backend", lambda cfg: object())

    with pytest.raises(CliError) as caught:
        heal(confirm=True, config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INFRASTRUCTURE
    assert result.error is not None
    assert "nothing was written" in (result.error.hint or "")


def test_a_backend_fault_never_leaks_its_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A driver trace can carry a DSN, a password, or a path."""

    async def _explode(*a: object, **k: object) -> int:
        raise RuntimeError("postgres://user:hunter2@db.internal:5432/snp_rag")

    monkeypatch.setattr("scout.healer.verify_and_heal_vault", _explode)
    monkeypatch.setattr("scout.cli.commands.ci._backend", lambda cfg: object())

    with pytest.raises(CliError) as caught:
        heal(confirm=True, config=_config(tmp_path))
    rendered = repr(caught.value.to_result())
    assert "hunter2" not in rendered
    assert "db.internal" not in rendered
