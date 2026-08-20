"""Table-driven PR and scheduled address-gate state machines."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.ci_address_gate import get_current_git_branch, main


class Runner:
    def __init__(self, returns: Sequence[int]) -> None:
        self.returns = iter(returns)
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], description: str) -> int:
        del description
        self.commands.append(tuple(command))
        return next(self.returns)


VERIFY = (sys.executable, "scripts/verify_addresses.py")
LINT = (sys.executable, "scripts/gen_index.py", "--check")
HEAL = (sys.executable, "scout/healer.py", "--ci")
GROUND = (sys.executable, "scripts/verify_groundedness.py")


@pytest.mark.parametrize("verify_exit", [2, 3, 127])
def test_infrastructure_or_unexpected_initial_exit_is_2_with_zero_mutation(
    tmp_path: Path, verify_exit: int
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "page.md"
    page.write_bytes(b"original bytes")
    runner = Runner([verify_exit])
    assert (
        main(
            ["--mode", "pr"],
            runner=runner,
            branch_getter=lambda: "feat/pr",
            repo_root=tmp_path,
        )
        == 2
    )
    assert runner.commands == [VERIFY]
    assert page.read_bytes() == b"original bytes"


GROUND_PR = (*GROUND, "--changed-only")


def test_initial_pass_runs_lint_then_groundedness_without_heal() -> None:
    runner = Runner([0, 0, 0])
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr") == 0
    assert runner.commands == [VERIFY, LINT, GROUND_PR]


def test_groundedness_is_advisory_by_default() -> None:
    """An unsupported page reports but does not fail the gate (see M7)."""
    runner = Runner([0, 0, 1])
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr") == 0


def test_groundedness_blocks_when_enforced() -> None:
    runner = Runner([0, 0, 1])
    assert (
        main(
            ["--mode", "pr", "--enforce-groundedness"],
            runner=runner,
            branch_getter=lambda: "feat/pr",
        )
        == 1
    )


def test_enforced_groundedness_infrastructure_failure_is_2() -> None:
    runner = Runner([0, 0, 2])
    assert (
        main(
            ["--mode", "pr", "--enforce-groundedness"],
            runner=runner,
            branch_getter=lambda: "feat/pr",
        )
        == 2
    )


def test_scheduled_mode_judges_the_whole_vault_not_just_changed_pages() -> None:
    runner = Runner([0, 0, 0])
    assert main(["--mode", "scheduled"], runner=runner, branch_getter=lambda: "main") == 0
    assert runner.commands[-1] == GROUND


@pytest.mark.parametrize("lint_exit", [2, 3, 127])
def test_initial_lint_infrastructure_or_unexpected_exit_is_2(lint_exit: int) -> None:
    runner = Runner([0, lint_exit])
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr") == 2
    assert runner.commands == [VERIFY, LINT]


def test_pr_drift_runs_exactly_one_heal_then_full_verify_and_lint() -> None:
    runner = Runner([1, 0, 0, 0])
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr") == 0
    assert runner.commands == [VERIFY, HEAL, VERIFY, LINT]


@pytest.mark.parametrize("branch", ["main", "master", "HEAD", "unknown", ""])
def test_pr_mode_rejects_protected_detached_or_unknown_branch(branch: str) -> None:
    runner = Runner([1])
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: branch) == 1
    assert runner.commands == [VERIFY]


@pytest.mark.parametrize(
    ("returns", "expected"),
    [
        ([1, 1], 1),
        ([1, 0, 1], 1),
        ([1, 0, 2], 2),
        ([1, 0, 0, 1], 1),
        ([1, 0, 0, 2], 2),
        ([1, 0, 0, 127], 2),
    ],
)
def test_pr_heal_reverify_or_lint_failure_never_commits(
    returns: list[int], expected: int
) -> None:
    runner = Runner(returns)
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr") == expected
    assert not any(command[:2] == ("git", "commit") for command in runner.commands)


@pytest.mark.parametrize("heal_exit", [2, 3, 127])
def test_pr_healer_infrastructure_failure_propagates_exit_2(heal_exit: int) -> None:
    runner = Runner([1, heal_exit])
    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr") == 2
    assert runner.commands == [VERIFY, HEAL]


def test_detached_head_uses_ci_head_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = type(
        "Completed",
        (),
        {"returncode": 0, "stdout": "HEAD\n"},
    )()
    monkeypatch.setattr("scripts.ci_address_gate.subprocess.run", lambda *a, **kw: completed)
    monkeypatch.setenv("GITHUB_HEAD_REF", "feature/from-ci")
    assert get_current_git_branch() == "feature/from-ci"


def test_failed_pr_heal_restores_preexisting_page_bytes(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "page.md"
    page.write_bytes(b"user bytes")
    calls = 0

    def runner(command: list[str], description: str) -> int:
        nonlocal calls
        del description
        calls += 1
        if tuple(command) == HEAL:
            page.write_bytes(b"partial healer bytes")
            return 1
        return 1

    assert main(["--mode", "pr"], runner=runner, branch_getter=lambda: "feat/pr", repo_root=tmp_path) == 1
    assert calls == 2
    assert page.read_bytes() == b"user bytes"


def test_scheduled_mode_branches_then_verifies_before_commit_and_push() -> None:
    runner = Runner([1, 0, 0, 0, 0, 0, 0, 0])
    assert (
        main(
            ["--mode", "scheduled"],
            runner=runner,
            branch_getter=lambda: "main",
            scheduled_branch="heal/test",
        )
        == 0
    )
    assert runner.commands == [
        VERIFY,
        ("git", "switch", "-c", "heal/test"),
        HEAL,
        VERIFY,
        LINT,
        ("git", "add", "--", "wiki"),
        ("git", "commit", "-m", "heal: repair verified RAG addresses"),
        ("git", "push", "-u", "origin", "heal/test"),
    ]
    assert runner.commands.index(VERIFY, 2) < next(
        i for i, command in enumerate(runner.commands) if command[:2] == ("git", "commit")
    )


def test_scheduled_mode_requires_protected_base_branch() -> None:
    runner = Runner([1])
    assert main(["--mode", "scheduled"], runner=runner, branch_getter=lambda: "feat/pr") == 1
    assert runner.commands == [VERIFY]


def test_scheduled_branch_creation_failure_is_infrastructure_exit_2() -> None:
    runner = Runner([1, 1])
    assert (
        main(
            ["--mode", "scheduled", "--branch", "heal/test"],
            runner=runner,
            branch_getter=lambda: "main",
        )
        == 2
    )


def test_scheduled_post_verify_failure_cleans_branch_without_commit() -> None:
    runner = Runner([1, 0, 0, 1, 0, 0, 0])
    assert (
        main(
            ["--mode", "scheduled", "--branch", "heal/test"],
            runner=runner,
            branch_getter=lambda: "main",
        )
        == 1
    )
    assert ("git", "commit", "-m", "heal: repair verified RAG addresses") not in runner.commands
    assert runner.commands[-3:] == [
        (
            "git",
            "restore",
            "--staged",
            "--worktree",
            "--source=HEAD",
            "--",
            "wiki",
        ),
        ("git", "switch", "main"),
        ("git", "branch", "-D", "heal/test"),
    ]


@pytest.mark.parametrize(
    ("failure_command", "returns"),
    [
        ("add", [1, 0, 0, 0, 0, 1, 0, 0, 0]),
        ("commit", [1, 0, 0, 0, 0, 0, 1, 0, 0, 0]),
        ("push", [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0]),
    ],
)
def test_scheduled_git_failure_restores_index_branch_and_worktree(
    failure_command: str, returns: list[int], tmp_path: Path
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "page.md"
    page.write_bytes(b"original")

    def mutating_runner(command: list[str], description: str) -> int:
        if tuple(command) == HEAL:
            page.write_bytes(b"healed")
        return runner(command, description)

    runner = Runner(returns)
    assert (
        main(
            ["--mode", "scheduled", "--branch", "heal/test"],
            runner=mutating_runner,
            branch_getter=lambda: "main",
            repo_root=tmp_path,
        )
        == 2
    )
    assert any(
        len(command) > 1 and command[1] == failure_command
        for command in runner.commands
    )
    assert page.read_bytes() == b"original"
    assert runner.commands[-3:] == [
        (
            "git",
            "restore",
            "--staged",
            "--worktree",
            "--source=HEAD",
            "--",
            "wiki",
        ),
        ("git", "switch", "main"),
        ("git", "branch", "-D", "heal/test"),
    ]


def test_failed_scheduled_cleanup_is_infrastructure_exit_2() -> None:
    runner = Runner([1, 0, 1, 1, 0, 0])
    assert (
        main(
            ["--mode", "scheduled", "--branch", "heal/test"],
            runner=runner,
            branch_getter=lambda: "main",
        )
        == 2
    )


@pytest.mark.parametrize("failed_git", ["commit", "push"])
def test_scheduled_cleanup_restores_real_git_index_and_branch(
    failed_git: str, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-b", "main")
    git("config", "user.name", "Gate Test")
    git("config", "user.email", "gate@example.invalid")
    wiki = repo / "wiki"
    wiki.mkdir()
    page = wiki / "page.md"
    page.write_text("original\n", encoding="utf-8")
    git("add", "wiki/page.md")
    git("commit", "-m", "base")
    verify_calls = 0

    def runner(command: list[str], description: str) -> int:
        nonlocal verify_calls
        del description
        if tuple(command) == VERIFY:
            verify_calls += 1
            return 1 if verify_calls == 1 else 0
        if tuple(command) == HEAL:
            page.write_text("healed\n", encoding="utf-8")
            return 0
        if tuple(command) == LINT:
            return 0
        if len(command) > 1 and command[1] == failed_git:
            return 1
        return subprocess.run(command, cwd=repo, check=False).returncode

    assert (
        main(
            ["--mode", "scheduled", "--branch", "heal/test"],
            runner=runner,
            branch_getter=lambda: "main",
            repo_root=repo,
        )
        == 2
    )
    assert git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert git("status", "--porcelain").stdout == ""
    assert page.read_text(encoding="utf-8") == "original\n"
    assert "heal/test" not in git("branch", "--list").stdout


@pytest.mark.parametrize("branch", ["main", "feature/nope", "heal/bad branch"])
def test_scheduled_branch_name_is_heal_namespaced(branch: str) -> None:
    runner = Runner([1])
    assert (
        main(
            ["--mode", "scheduled", "--branch", branch],
            runner=runner,
            branch_getter=lambda: "main",
        )
        == 1
    )
    assert runner.commands == [VERIFY]
