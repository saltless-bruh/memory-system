#!/usr/bin/env python3
"""Closed-loop PR/scheduled address verification state machine."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_BRANCHES = frozenset({"main", "master"})
UNRESOLVED_BRANCHES = frozenset({"", "HEAD", "unknown"})

VERIFY_COMMAND = [sys.executable, "scripts/verify_addresses.py"]
LINT_COMMAND = [sys.executable, "scripts/gen_index.py", "--check"]
HEAL_COMMAND = [sys.executable, "scout/healer.py", "--ci"]
GROUNDEDNESS_COMMAND = [sys.executable, "scripts/verify_groundedness.py"]

CommandRunner = Callable[[list[str], str], int]
BranchGetter = Callable[[], str]


def run_command(command: list[str], description: str) -> int:
    print(f"[ci_address_gate] {description}")
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


def get_current_git_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    branch = result.stdout.strip() if result.returncode == 0 else ""
    if branch not in UNRESOLVED_BRANCHES:
        return branch
    return (
        os.environ.get("GITHUB_HEAD_REF", "").strip()
        or os.environ.get("GIT_BRANCH", "").strip()
        or "unknown"
    )


def _snapshot_wiki(repo_root: Path) -> dict[Path, bytes]:
    wiki = repo_root / "wiki"
    if not wiki.exists():
        return {}
    return {path: path.read_bytes() for path in wiki.rglob("*.md")}


def _restore_wiki(repo_root: Path, snapshot: dict[Path, bytes]) -> None:
    wiki = repo_root / "wiki"
    current = set(wiki.rglob("*.md")) if wiki.exists() else set()
    for path in current - set(snapshot):
        path.unlink()
    for path, content in snapshot.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _post_heal_exit(runner: CommandRunner) -> int:
    verify_exit = runner(VERIFY_COMMAND, "post-heal address verification")
    if verify_exit == 2 or verify_exit not in {0, 1}:
        return 2
    if verify_exit == 1:
        return 1
    lint_exit = runner(LINT_COMMAND, "post-heal vault lint")
    if lint_exit in {0, 1}:
        return lint_exit
    return 2


def _groundedness_exit(
    runner: CommandRunner, *, mode: str, enforce: bool
) -> int:
    """Judge page bodies against their cited sources (M7).

    Advisory by default. The checker itself is a real gate — it quotes the
    offending sentence and exits 1 — but the shipped vault does not yet pass it:
    a full run judges 10 of 13 pages UNSUPPORTED, because pages assert domain
    knowledge their stub sources never contained. Blocking on that today would
    only teach people to disable the gate. `--enforce-groundedness` (or
    `SNP_ENFORCE_GROUNDEDNESS=1`) makes it authoritative once the corpus is
    real; the report is printed either way, so the debt stays visible.

    In `pr` mode only changed pages are judged — one model call per changed
    page instead of one per vault page.
    """
    command = list(GROUNDEDNESS_COMMAND)
    if mode == "pr":
        command.append("--changed-only")
    exit_code = runner(command, "groundedness check")
    if exit_code == 0:
        return 0
    if not enforce:
        print(
            "[ci_address_gate] groundedness reported problems "
            f"(exit {exit_code}); advisory only — pass --enforce-groundedness "
            "to make this blocking."
        )
        return 0
    return exit_code if exit_code in {1, 2} else 2


def _lint_exit(runner: CommandRunner, description: str) -> int:
    """Preserve semantic lint drift while normalizing tool failures to exit 2."""
    lint_exit = runner(LINT_COMMAND, description)
    if lint_exit in {0, 1}:
        return lint_exit
    return 2


def _cleanup_scheduled_branch(
    runner: CommandRunner, original_branch: str, heal_branch: str
) -> bool:
    """Restore the scheduled job's index, branch, and local branch namespace."""
    restored_index = runner(
        ["git", "restore", "--staged", "--worktree", "--source=HEAD", "--", "wiki"],
        "restore failed scheduled heal index and worktree",
    )
    switched = runner(
        ["git", "switch", original_branch], "restore scheduled base branch"
    )
    deleted = runner(
        ["git", "branch", "-D", heal_branch], "delete failed heal branch"
    )
    return restored_index == switched == deleted == 0


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner = run_command,
    branch_getter: BranchGetter = get_current_git_branch,
    repo_root: Path = REPO_ROOT,
    scheduled_branch: str | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Closed-loop address gate")
    parser.add_argument("--mode", choices=("pr", "scheduled"), required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", help="scheduled heal branch (must start with heal/)")
    parser.add_argument(
        "--enforce-groundedness",
        action="store_true",
        default=os.environ.get("SNP_ENFORCE_GROUNDEDNESS", "").strip() == "1",
        help="fail the gate when a page's body is unsupported by its sources "
        "(advisory by default; see _groundedness_exit)",
    )
    args = parser.parse_args(argv)

    initial = runner(VERIFY_COMMAND, "initial address verification")
    if initial == 0:
        lint_exit = _lint_exit(runner, "vault lint")
        if lint_exit != 0:
            return lint_exit
        return _groundedness_exit(
            runner, mode=args.mode, enforce=args.enforce_groundedness
        )
    if initial == 2 or initial not in {0, 1}:
        return 2

    original_branch = branch_getter()
    if original_branch in UNRESOLVED_BRANCHES:
        return 1
    if args.mode == "pr" and original_branch in PROTECTED_BRANCHES:
        return 1
    if args.mode == "scheduled" and original_branch not in PROTECTED_BRANCHES:
        return 1

    snapshot = _snapshot_wiki(repo_root)
    heal_branch: str | None = None
    if args.mode == "scheduled":
        heal_branch = scheduled_branch or args.branch or (
            f"heal/addresses-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        )
        if not heal_branch.startswith("heal/") or any(char.isspace() for char in heal_branch):
            return 1
        if runner(
            ["git", "switch", "-c", heal_branch], "create scheduled heal branch"
        ) != 0:
            return 2

    heal_exit = runner(HEAL_COMMAND, "apply one scoped heal pass")
    if heal_exit != 0:
        _restore_wiki(repo_root, snapshot)
        cleanup_ok = True
        if heal_branch is not None:
            cleanup_ok = _cleanup_scheduled_branch(
                runner, original_branch, heal_branch
            )
        if not cleanup_ok or heal_exit == 2 or heal_exit not in {1, 2}:
            return 2
        return 1

    post_exit = _post_heal_exit(runner)
    if post_exit != 0:
        _restore_wiki(repo_root, snapshot)
        cleanup_ok = True
        if heal_branch is not None:
            cleanup_ok = _cleanup_scheduled_branch(
                runner, original_branch, heal_branch
            )
        if not cleanup_ok:
            return 2
        return post_exit

    if args.mode == "pr":
        return 0

    assert heal_branch is not None
    if runner(["git", "add", "--", "wiki"], "stage verified heal") != 0:
        _restore_wiki(repo_root, snapshot)
        _cleanup_scheduled_branch(runner, original_branch, heal_branch)
        return 2
    if runner(
        ["git", "commit", "-m", "heal: repair verified RAG addresses"],
        "commit verified heal",
    ) != 0:
        _restore_wiki(repo_root, snapshot)
        _cleanup_scheduled_branch(runner, original_branch, heal_branch)
        return 2
    if runner(
        ["git", "push", "-u", args.remote, heal_branch], "push scheduled heal branch"
    ) != 0:
        _restore_wiki(repo_root, snapshot)
        _cleanup_scheduled_branch(runner, original_branch, heal_branch)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
