"""Trusted materialization of untrusted PR wiki blobs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.materialize_pr_wiki as materializer
from scripts.materialize_pr_wiki import (
    MAX_MARKDOWN_BYTES,
    MaterializationError,
    export_wiki_markdown,
    materialize_pr_wiki,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "wiki").mkdir()
    (repo / "wiki" / "keep.md").write_text("base keep\n", encoding="utf-8")
    (repo / "wiki" / "delete.md").write_text("delete me\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_materialize_accepts_only_regular_wiki_markdown_changes(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "wiki" / "keep.md").write_text("PR bytes\n", encoding="utf-8")
    (repo / "wiki" / "delete.md").unlink()
    (repo / "wiki" / "new.md").write_text("new bytes\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "wiki only")
    head = _git(repo, "rev-parse", "HEAD")

    destination = tmp_path / "trusted"
    (destination / "wiki").mkdir(parents=True)
    (destination / "wiki" / "keep.md").write_text("base keep\n", encoding="utf-8")
    (destination / "wiki" / "delete.md").write_text("delete me\n", encoding="utf-8")

    changed = materialize_pr_wiki(repo, destination, base_sha=base, head_sha=head)

    assert changed == ("wiki/delete.md", "wiki/keep.md", "wiki/new.md")
    assert (destination / "wiki" / "keep.md").read_text() == "PR bytes\n"
    assert (destination / "wiki" / "new.md").read_text() == "new bytes\n"
    assert not (destination / "wiki" / "delete.md").exists()


def test_materialize_rejects_any_non_wiki_change_before_mutation(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "wiki" / "keep.md").write_text("PR bytes\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "attack.py").write_text("raise SystemExit\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "mixed")
    head = _git(repo, "rev-parse", "HEAD")
    destination = tmp_path / "trusted"
    (destination / "wiki").mkdir(parents=True)
    target = destination / "wiki" / "keep.md"
    target.write_text("trusted bytes\n", encoding="utf-8")

    with pytest.raises(MaterializationError, match="wiki Markdown"):
        materialize_pr_wiki(repo, destination, base_sha=base, head_sha=head)
    assert target.read_text() == "trusted bytes\n"


def test_materialize_rejects_symlink_markdown_blob(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    link = repo / "wiki" / "link.md"
    try:
        link.symlink_to("/etc/passwd")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    _git(repo, "add", "wiki/link.md")
    _git(repo, "commit", "-m", "symlink")
    head = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(MaterializationError, match="regular blob"):
        materialize_pr_wiki(repo, tmp_path / "trusted", base_sha=base, head_sha=head)


def test_materialize_rejects_oversized_blob_before_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path)
    oversized = repo / "wiki" / "oversized.md"
    oversized.write_bytes(b"x" * (MAX_MARKDOWN_BYTES + 1))
    _git(repo, "add", "wiki/oversized.md")
    _git(repo, "commit", "-m", "oversized")
    head = _git(repo, "rev-parse", "HEAD")

    original_git = materializer._git

    def guarded_git(source: Path, *args: str, check: bool = True) -> bytes:
        assert args[:2] != ("cat-file", "blob")
        return original_git(source, *args, check=check)

    monkeypatch.setattr("scripts.materialize_pr_wiki._git", guarded_git)
    with pytest.raises(MaterializationError, match="size limit"):
        materialize_pr_wiki(repo, tmp_path / "trusted", base_sha=base, head_sha=head)


def test_export_mirrors_only_markdown_and_preserves_other_pr_files(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    destination = tmp_path / "pr-source"
    (trusted / "wiki" / "nested").mkdir(parents=True)
    (trusted / "wiki" / "nested" / "page.md").write_text("healed\n", encoding="utf-8")
    (destination / "wiki" / "nested").mkdir(parents=True)
    (destination / "wiki" / "old.md").write_text("old\n", encoding="utf-8")
    (destination / "scripts").mkdir()
    script = destination / "scripts" / "untouched.py"
    script.write_text("untrusted but never executed\n", encoding="utf-8")

    export_wiki_markdown(trusted, destination)

    assert (destination / "wiki" / "nested" / "page.md").read_text() == "healed\n"
    assert not (destination / "wiki" / "old.md").exists()
    assert script.read_text() == "untrusted but never executed\n"
