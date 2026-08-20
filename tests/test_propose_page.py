"""Tests for exact-scope, PR-first wiki proposals."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import propose_page


@pytest.fixture
def proposal_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    page = tmp_path / "wiki" / "concepts" / "target.md"
    page.parent.mkdir(parents=True)
    page.write_text("page", encoding="utf-8")
    (tmp_path / "wiki" / "index.md").write_text("index", encoding="utf-8")
    (tmp_path / "wiki" / "log.md").write_text("log", encoding="utf-8")
    monkeypatch.setattr(propose_page, "REPO_ROOT", tmp_path)
    return tmp_path


def _completed(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_rejects_page_outside_wiki_without_git_calls(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = MagicMock()
    monkeypatch.setattr(propose_page, "git", git)

    assert propose_page.main(["--page", "../outside.md"]) == 1

    git.assert_not_called()


def test_named_page_must_have_a_working_tree_change(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = MagicMock(side_effect=[_completed("")])
    monkeypatch.setattr(propose_page, "git", git)

    result = propose_page.main(["--page", "wiki/concepts/target.md"])

    assert result == 1
    assert not any(call.args[0] == "checkout" for call in git.call_args_list)


def test_lint_or_verify_failure_causes_no_branch_or_staging(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = MagicMock(return_value=_completed(" M wiki/concepts/target.md\n"))
    monkeypatch.setattr(propose_page, "git", git)
    monkeypatch.setattr(propose_page, "run_lint", lambda: False)
    verify = MagicMock(return_value=True)
    monkeypatch.setattr(propose_page, "run_verify", verify)

    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1
    verify.assert_not_called()
    assert not any(
        call.args[0] in {"checkout", "add", "commit"} for call in git.call_args_list
    )

    git.reset_mock()
    monkeypatch.setattr(propose_page, "run_lint", lambda: True)
    monkeypatch.setattr(propose_page, "run_verify", lambda: False)
    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1
    assert not any(
        call.args[0] in {"checkout", "add", "commit"} for call in git.call_args_list
    )


def test_existing_unrelated_staged_change_is_rejected(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("status", "--porcelain"):
            return _completed(" M wiki/concepts/target.md\n")
        if args[:2] == ("diff", "--cached"):
            return _completed("scout/unrelated.py\n")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(propose_page, "git", fake_git)

    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1


def test_existing_allowed_staged_change_is_rejected_to_preserve_rollback(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("status", "--porcelain"):
            return _completed("M  wiki/concepts/target.md\n")
        if args[:2] == ("diff", "--cached"):
            return _completed("wiki/concepts/target.md\n")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(propose_page, "git", fake_git)

    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1


def test_success_stages_and_commits_only_named_page_and_changed_generated_files(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return _completed(
                " M wiki/concepts/target.md\n M wiki/index.md\n M wiki/log.md\n"
            )
        if args[:2] == ("diff", "--cached"):
            return _completed("")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _completed("main\n")
        return _completed()

    monkeypatch.setattr(propose_page, "git", fake_git)
    monkeypatch.setattr(propose_page, "run_lint", lambda: True)
    monkeypatch.setattr(propose_page, "run_verify", lambda: True)

    result = propose_page.main(
        ["--page", "wiki/concepts/target.md", "--title", "Target"]
    )

    assert result == 0
    allowed = ("wiki/concepts/target.md", "wiki/index.md", "wiki/log.md")
    assert ("add", "--", *allowed) in calls
    commit = next(call for call in calls if call[0] == "commit")
    assert "--only" in commit
    assert commit[-3:] == allowed
    assert not any(argument == "wiki/" for call in calls for argument in call)


def test_dry_run_never_mutates_git_state(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return _completed(" M wiki/concepts/target.md\n")
        if args[:2] == ("diff", "--cached"):
            return _completed("")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _completed("feature/current\n")
        return _completed()

    monkeypatch.setattr(propose_page, "git", fake_git)

    assert propose_page.main(["--page", "wiki/concepts/target.md", "--dry-run"]) == 0
    assert not any(call[0] in {"checkout", "add", "commit", "push"} for call in calls)


@pytest.mark.parametrize("failed_command", ["add", "commit"])
def test_local_proposal_failure_restores_base_branch_and_unstaged_page(
    proposal_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_command: str,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return _completed(" M wiki/concepts/target.md\n")
        if args[:2] == ("diff", "--cached"):
            return _completed("")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _completed("feature/current\n")
        if args[0] == failed_command:
            raise subprocess.CalledProcessError(1, ["git", *args])
        return _completed()

    monkeypatch.setattr(propose_page, "git", fake_git)
    monkeypatch.setattr(propose_page, "run_lint", lambda: True)
    monkeypatch.setattr(propose_page, "run_verify", lambda: True)

    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1

    assert ("reset", "--mixed", "HEAD", "--", "wiki/concepts/target.md") in calls
    assert ("switch", "feature/current") in calls
    assert any(call[:2] == ("branch", "-D") for call in calls)


def test_branch_creation_failure_returns_cleanly_without_rollback_commands(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return _completed(" M wiki/concepts/target.md\n")
        if args[:2] == ("diff", "--cached"):
            return _completed("")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _completed("feature/current\n")
        if args[:2] == ("checkout", "-b"):
            raise subprocess.CalledProcessError(1, ["git", *args])
        return _completed()

    monkeypatch.setattr(propose_page, "git", fake_git)
    monkeypatch.setattr(propose_page, "run_lint", lambda: True)
    monkeypatch.setattr(propose_page, "run_verify", lambda: True)

    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1

    assert not any(call[0] in {"reset", "switch", "branch"} for call in calls)


def test_push_failure_preserves_verified_local_commit_for_retry(
    proposal_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return _completed(" M wiki/concepts/target.md\n")
        if args[:2] == ("diff", "--cached"):
            return _completed("")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _completed("feature/current\n")
        if args[0] == "push":
            raise subprocess.CalledProcessError(1, ["git", *args])
        return _completed()

    monkeypatch.setattr(propose_page, "git", fake_git)
    monkeypatch.setattr(propose_page, "run_lint", lambda: True)
    monkeypatch.setattr(propose_page, "run_verify", lambda: True)

    assert propose_page.main(["--page", "wiki/concepts/target.md", "--push"]) == 1

    assert any(call[0] == "commit" for call in calls)
    assert not any(call[0] in {"reset", "switch", "branch"} for call in calls)
