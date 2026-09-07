"""The W-2 acceptance gate must survive `main` moving under it.

The gate pushes a sentinel to the vault repository's `main` and removes it
again. That is the same branch the vault lane merges into, so a lane landing a
batch between the gate's clone and its push makes the push a non-fast-forward.
Measured on 2026-09-07: it did, and the gate failed on work that was perfectly
correct.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "v3" / "checks"))

import engine_acceptance as ea  # noqa: E402


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin with a `main`, plus a clone of it."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "main", cwd=seed)
    (seed / "wiki").mkdir()
    (seed / "wiki" / "seed.md").write_text("# seed\n", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "seed", cwd=seed)
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(seed), str(origin)],
        check=True,
        capture_output=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--branch", "main", str(origin), str(clone)],
        check=True,
        capture_output=True,
    )
    return origin, clone


def _land_a_concurrent_commit(origin: Path, tmp_path: Path) -> None:
    """Another lane pushes to `main` after our clone was taken."""
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", "--branch", "main", str(origin), str(other)],
        check=True,
        capture_output=True,
    )
    (other / "wiki" / "batch.md").write_text("# a vault batch\n", encoding="utf-8")
    _run("add", "-A", cwd=other)
    _run("commit", "-q", "-m", "vault: a batch from another lane", cwd=other)
    _run("push", "-q", "origin", "HEAD:main", cwd=other)


def test_publish_survives_a_concurrent_push(
    origin_and_clone: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe lands even though `main` moved after the clone."""
    origin, clone = origin_and_clone
    monkeypatch.setattr(ea, "VAULT_BRANCH", "main")
    monkeypatch.setattr(ea, "VAULT_SUBDIR", "wiki")

    _land_a_concurrent_commit(origin, tmp_path)

    ea._publish(clone, "probe.md", "# probe\n", "test(w2): probe")

    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main"],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # Both survive: the gate's probe, and the batch it must not have discarded.
    assert "wiki/probe.md" in listed
    assert "wiki/batch.md" in listed


def test_publish_does_not_discard_the_other_lane(
    origin_and_clone: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A force-push would make the probe land and lose the batch. Never that."""
    origin, clone = origin_and_clone
    monkeypatch.setattr(ea, "VAULT_BRANCH", "main")
    monkeypatch.setattr(ea, "VAULT_SUBDIR", "wiki")

    _land_a_concurrent_commit(origin, tmp_path)
    ea._publish(clone, "probe.md", "# probe\n", "test(w2): probe")

    subjects = subprocess.run(
        ["git", "log", "--format=%s", "main"],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "vault: a batch from another lane" in subjects
