"""The history-hygiene and no-push gates must measure the branch being worked on.

Both gates were pinned to the literal `feat/v3-retrieval-inversion`, a branch
last committed on 2026-09-09. Every commit made after it -- fifteen of them on
the branch that exposed this -- was invisible to the only history path scan
the repository has, and no-push asked whether that old branch had reached a
remote rather than the one about to.

These tests build a throwaway repository with exactly that shape: an old
feature branch that is clean and local, and a newer branch on top of it that
is the checked-out work. The gates run through their real `git` calls with
only `REPO_ROOT` pointed at the throwaway repository.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "v3" / "checks"))

import webhook_delivery as wd  # noqa: E402

OLD_BRANCH = "feat/v3-retrieval-inversion"
NEW_BRANCH = "fix/current-work"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(repo: Path, files: dict[str, str | None], message: str) -> None:
    for relative, body in files.items():
        target = repo / relative
        if body is None:
            _git(repo, "rm", "-q", relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """main, then a clean 60-file old branch, then the checked-out new branch."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "gate@example.invalid")
    _git(root, "config", "user.name", "gate")
    _commit(root, {"README.md": "base\n"}, "base")
    _git(root, "switch", "-q", "-c", OLD_BRANCH)
    _commit(
        root,
        {f"src/module_{n}.py": f"VALUE = {n}\n" for n in range(60)},
        "the old branch's week of work",
    )
    _git(root, "switch", "-q", "-c", NEW_BRANCH)
    monkeypatch.setattr(wd, "REPO_ROOT", root)
    monkeypatch.setattr(wd, "_branch_argument", None)
    return root


def test_history_hygiene_scans_commits_past_the_old_branch(repo: Path) -> None:
    _commit(repo, {".secrets/scout_static_tokens.json": "{}\n"}, "leak")

    with pytest.raises(wd.GateFailure, match=r"\.secrets/scout_static_tokens"):
        wd.group_history_hygiene()


def test_history_hygiene_sees_a_file_added_then_removed(repo: Path) -> None:
    """A net diff forgets a deleted file; the pushed history does not."""
    _commit(repo, {"Untitled.md": "scratch\n"}, "scratch")
    _commit(repo, {"Untitled.md": None}, "tidy")

    with pytest.raises(wd.GateFailure, match=r"Untitled\.md"):
        wd.group_history_hygiene()


def test_history_hygiene_passes_a_clean_current_branch(repo: Path) -> None:
    _commit(repo, {"src/fix.py": "FIXED = True\n"}, "a small clean fix")

    assert wd.group_history_hygiene() == "HISTORY HYGIENE VERIFIED"


def test_history_hygiene_refuses_a_branch_with_nothing_on_it(repo: Path) -> None:
    """HEAD equal to its base is an empty set, and a pass over it means nothing."""
    _git(repo, "switch", "-q", "main")

    with pytest.raises(wd.GateFailure):
        wd.group_history_hygiene()


def test_branch_argument_selects_another_branch(repo: Path) -> None:
    _commit(repo, {".obsidian/app.json": "{}\n"}, "editor state")
    _git(repo, "switch", "-q", OLD_BRANCH)
    wd._branch_argument = NEW_BRANCH

    with pytest.raises(wd.GateFailure, match=r"\.obsidian/app\.json"):
        wd.group_history_hygiene()


def test_no_push_checks_the_checked_out_branch(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", NEW_BRANCH)

    with pytest.raises(wd.GateFailure, match=NEW_BRANCH):
        wd.group_no_push()


def test_no_push_passes_an_unpushed_branch(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")

    assert wd.group_no_push() == "NO PUSH VERIFIED"
