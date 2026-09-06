"""Tests for the verification command family.

The distinction these exist to protect is part of the public result contract: a
**finding** (the vault has a problem — exit 1) is not a **failure** (the check
could not run — exit 2). Confusing them can authorize action on the strength of
a check that never ran.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.verify import (  # noqa: E402
    DEFAULT_STAGES,
    check,
    verify_secrets,
    verify_vault,
)
from scout.cli.config import Config, resolve  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import CommandResult, ErrorKind, ExitCode  # noqa: E402


def _cfg(repo: Path | None = REPO_ROOT) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, values={}, repo_root=repo)


def _stage(name: str, result: CommandResult) -> tuple[str, object]:
    return (name, lambda **_: result)


# ── outcome vs failure ───────────────────────────────────────────────────────


def test_lint_errors_are_a_finding_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 1 keeps healing permitted; exit 2 would forbid it."""
    monkeypatch.setattr(
        "scout.cli.commands.verify._pages_and_lint",
        lambda _d: (
            [],
            type(
                "L", (), {"errors": ["bad frontmatter"], "warnings": [], "ok": False}
            )(),
            True,
            0,
        ),
    )
    result = verify_vault(config=_cfg())
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.mutating_is_allowed, "a finding must permit caller follow-up"
    assert result.error is None, "a finding is not an error envelope"


def test_a_clean_vault_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.cli.commands.verify._pages_and_lint",
        lambda _d: (
            [],
            type("L", (), {"errors": [], "warnings": [], "ok": True})(),
            True,
            13,
        ),
    )
    result = verify_vault(config=_cfg())
    assert result.exit_code is ExitCode.SUCCESS
    assert result.data["pages"] == 13
    assert result.data["index_current"] is True


def test_a_stale_index_is_a_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.cli.commands.verify._pages_and_lint",
        lambda _d: (
            [],
            type("L", (), {"errors": [], "warnings": [], "ok": True})(),
            False,
            2,
        ),
    )
    result = verify_vault(config=_cfg())
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["index_current"] is False
    assert any("STALE" in m for m in result.messages)


def test_an_unreadable_vault_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symlinked wiki root is a configuration fault, not a lint finding."""

    def _boom(_dir: object) -> None:
        raise ValueError("wiki root must not be a symlink")

    monkeypatch.setattr("scout.cli.commands.verify._pages_and_lint", _boom)
    with pytest.raises(CliError) as caught:
        verify_vault(config=_cfg())
    assert caught.value.kind is ErrorKind.INFRASTRUCTURE
    assert caught.value.to_result().mutating_is_allowed is False


# ── repository requirement ───────────────────────────────────────────────────


def test_local_commands_refuse_outside_a_checkout() -> None:
    for run in (verify_vault, verify_secrets):
        with pytest.raises(CliError) as caught:
            run(config=_cfg(repo=None))
        assert caught.value.kind is ErrorKind.INPUT_VALIDATION


# ── secrets ──────────────────────────────────────────────────────────────────


def test_secret_findings_are_reported_without_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Finding.format()` is redacted at the source; nothing here may undo that."""

    class _Finding:
        def format(self) -> str:
            return "[WORKTREE] a.py:1: OpenAI / LiteLLM token [REDACTED]"

    monkeypatch.setattr(
        "scripts.scan_secrets.scan_all_current", lambda _r: [_Finding()]
    )
    result = verify_secrets(config=_cfg())
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["count"] == 1
    assert "REDACTED" in result.data["findings"][0]


def test_history_scan_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slow all-refs walk is what CI runs, not what a developer waits on."""
    called: list[str] = []
    monkeypatch.setattr("scripts.scan_secrets.scan_all_current", lambda _r: [])
    monkeypatch.setattr(
        "scripts.scan_secrets.scan_git_history",
        lambda _r: called.append("history") or [],
    )
    verify_secrets(config=_cfg())
    assert called == []
    verify_secrets(history=True, config=_cfg())
    assert called == ["history"]


# ── the aggregate ────────────────────────────────────────────────────────────


def test_check_runs_cheapest_first() -> None:
    """Ordering is the point: a lint error should not cost a model call per page."""
    assert [name for name, _ in DEFAULT_STAGES] == [
        "vault",
        "secrets",
        "addresses",
        "groundedness",
    ]


def test_check_stops_at_the_first_failure() -> None:
    ran: list[str] = []

    def _ok(name: str) -> tuple[str, object]:
        return (name, lambda **_: ran.append(name) or CommandResult(summary=name))

    def _fail(name: str) -> tuple[str, object]:
        return (
            name,
            lambda **_: (
                ran.append(name)
                or CommandResult(
                    exit_code=ExitCode.SEMANTIC_FAILURE, summary=f"{name} failed"
                )
            ),
        )

    result = check(stages=[_ok("a"), _fail("b"), _ok("c")], config=_cfg())
    assert ran == ["a", "b"], "stages after a failure must not run"
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["failed_stage"] == "b"


def test_check_passes_when_every_stage_passes() -> None:
    result = check(
        stages=[
            _stage("a", CommandResult(summary="a")),
            _stage("b", CommandResult(summary="b")),
        ],
        config=_cfg(),
    )
    assert result.exit_code is ExitCode.SUCCESS
    assert result.data["failed_stage"] is None
    assert set(result.data["stages"]) == {"a", "b"}


def test_check_propagates_an_infrastructure_failure_with_its_envelope() -> None:
    """CI must see 2, not 1, or it would treat an outage as a vault problem."""
    failure = CommandResult.failure(ErrorKind.INFRASTRUCTURE, "database unreachable")
    result = check(stages=[_stage("addresses", failure)], config=_cfg())
    assert result.exit_code is ExitCode.INFRASTRUCTURE
    assert result.error is not None
    assert result.mutating_is_allowed is False


# ── declaration matches behaviour ────────────────────────────────────────────


def test_every_verify_command_declares_the_semantic_outcome() -> None:
    """`schema` must not promise an agent something the command cannot return."""
    from scout.cli import app  # noqa: F401  (registers the commands)
    from scout.cli.registry import REGISTRY

    for name in ("verify-vault", "verify-addresses", "verify-groundedness", "check"):
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert ExitCode.SEMANTIC_FAILURE in spec.outcomes, name
        assert spec.prerequisite is Prerequisite.LOCAL, name


def test_config_gating_reaches_the_verify_commands() -> None:
    """A LOCAL command may see database keys; that is what makes it LOCAL."""
    cfg = resolve(Prerequisite.LOCAL, environ={"POSTGRES_HOST": "db"}, start=REPO_ROOT)
    assert cfg.get("POSTGRES_HOST") == "db"
    assert cfg.get("GEMINI_API_KEY") is None


def test_compile_plan_refuses_to_write_without_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutation is approved at call time, not by installing the tool."""
    from scout.cli.commands.compile import compile_plan
    from scout.cli.errors import CliError
    from scout.cli.result import ErrorKind

    called: list[int] = []
    monkeypatch.setattr(
        "scripts.compile_plan.compile_plan", lambda *_a, **_k: called.append(1) or []
    )

    class _Cfg:
        # `require_repo` returns the checkout, and `compile_plan` now uses it:
        # the plan path is bounds-checked against that root before anything is
        # spent or written. Input validation runs first by design — telling a
        # caller to "pass --confirm" for a path that would be rejected anyway
        # would send them round the loop twice.
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    with pytest.raises(CliError) as caught:
        compile_plan("some-plan.json", config=_Cfg())

    assert caught.value.to_result().exit_code == 5
    assert caught.value.to_result().error is not None
    assert caught.value.to_result().error.kind is ErrorKind.CONFIRMATION_REQUIRED
    assert called == [], "nothing may run before confirmation"


def test_compile_status_reports_a_terminal_failure_as_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 0 would tell `compile-status && publish` to proceed on a dead batch."""
    import json

    from scout.cli.commands.compile import compile_status
    from scout.cli.result import ExitCode
    from scout.cli.tasks import TaskState, finish_run, write_run_marker

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": "1",
                        "title": "A",
                        "loc": "p.1",
                        "slug": "a",
                        "category": "concept",
                        "department": "ai_eng",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_run_marker(plan, pid=2**22)
    finish_run(plan, state=TaskState.FAILED, detail="route unreachable", exit_code=1)

    class _Cfg:
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    result = compile_status("plan.json", config=_Cfg())

    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["state"] == "failed"
    assert result.data["terminal"] is True


# ── `snpmemory compile-cancel` ────────────────────────────────────────────


def _batch(tmp_path: Path, *slugs: str) -> Path:
    import json

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": str(i + 1),
                        "title": slug.title(),
                        "loc": f"p.{i + 1}",
                        "slug": slug,
                        "category": "concept",
                        "department": "ai_eng",
                    }
                    for i, slug in enumerate(slugs)
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


class _RepoCfg:
    def __init__(self, root: Path) -> None:
        self._root = root

    def require_repo(self) -> Path:
        return self._root


def test_cancelling_a_running_batch_records_the_request_without_claiming_it_stopped(
    tmp_path: Path,
) -> None:
    """R4: it stops after the current article. Saying otherwise would be a lie."""
    import os

    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode
    from scout.cli.tasks import cancel_requested, write_run_marker

    plan = _batch(tmp_path, "a", "b")
    write_run_marker(plan, pid=os.getpid())

    result = compile_cancel(str(plan), config=_RepoCfg(tmp_path))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "cancelling"
    assert result.data["state"] == "running"
    assert "will stop after the current article" in result.summary
    assert cancel_requested(plan) is True


def test_cancelling_a_terminal_batch_is_a_no_op_that_says_so(tmp_path: Path) -> None:
    """An error here would push a caller into treating a finished batch as a fault."""
    import os

    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode
    from scout.cli.tasks import (
        TaskState,
        cancel_requested,
        finish_run,
        write_run_marker,
    )

    plan = _batch(tmp_path, "a")
    write_run_marker(plan, pid=os.getpid())
    finish_run(plan, state=TaskState.CANCELLED, detail="already stopped", exit_code=130)

    result = compile_cancel(str(plan), config=_RepoCfg(tmp_path))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "no_change"
    assert cancel_requested(plan) is False, "nothing may be written for a no-op"


def test_cancelling_a_batch_with_no_live_process_changes_nothing(
    tmp_path: Path,
) -> None:
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode
    from scout.cli.tasks import cancel_requested, write_run_marker

    plan = _batch(tmp_path, "a", "b")
    write_run_marker(plan, pid=2**22)

    result = compile_cancel(str(plan), config=_RepoCfg(tmp_path))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "not_running"
    assert cancel_requested(plan) is False


def test_cancelling_an_unknown_handle_is_input_validation(tmp_path: Path) -> None:
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode

    with pytest.raises(CliError) as caught:
        compile_cancel(str(tmp_path / "nope.json"), config=_RepoCfg(tmp_path))

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_cancelling_a_handle_outside_the_checkout_is_refused(tmp_path: Path) -> None:
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode

    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(CliError) as caught:
        compile_cancel("/etc/plan.json", config=_RepoCfg(root))

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION
