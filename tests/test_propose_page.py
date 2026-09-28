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
    # Nothing that writes may name the whole of wiki/; the push guard reads the
    # wiki/ history on purpose, and reading it stages nothing.
    writes = {"add", "commit", "reset", "checkout", "switch", "push"}
    assert not any(
        argument in {"wiki", "wiki/"}
        for call in calls
        if call[0] in writes
        for argument in call
    )


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


# ── pushing against real remotes ──────────────────────────────────────────
#
# `origin` is public GitHub and carries twelve sample pages; `gitea` is private
# and carries the real vault. These tests stand up that shape with bare
# repositories, because the defect being guarded is which bytes reach which
# remote, and only real git can show that.

_VAULT_PAGES = 14


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit_wiki(repo: Path, message: str) -> None:
    _run(repo, "add", "--", "wiki")
    _run(repo, "commit", "--no-verify", "-q", "-m", message)


@pytest.fixture
def two_remotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout on a vault-bearing branch, with a public and a private remote.

    `main` holds the public sample and is on both remotes. `vault` adds the
    private pages and is on `gitea` only, exactly as the integration branches
    are. The caller stands on `vault` with one new page to propose.
    """
    work = tmp_path / "work"
    work.mkdir()
    for name in ("origin", "gitea"):
        _run(tmp_path, "init", "-q", "--bare", "-b", "main", f"{name}.git")
    _run(work, "init", "-q", "-b", "main")
    _run(work, "config", "user.name", "test")
    _run(work, "config", "user.email", "test@example.invalid")
    _run(work, "config", "commit.gpgsign", "false")
    _run(work, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _run(work, "remote", "add", "gitea", str(tmp_path / "gitea.git"))

    (work / "wiki" / "concepts").mkdir(parents=True)
    (work / "wiki" / "index.md").write_text("index\n", encoding="utf-8")
    (work / "wiki" / "log.md").write_text("log\n", encoding="utf-8")
    (work / "wiki" / "concepts" / "sample.md").write_text("s\n", encoding="utf-8")
    _commit_wiki(work, "public sample")
    _run(work, "push", "-q", "origin", "main")
    _run(work, "push", "-q", "gitea", "main")

    _run(work, "switch", "-q", "-c", "vault")
    for number in range(_VAULT_PAGES):
        page = work / "wiki" / "concepts" / f"private-{number}.md"
        page.write_text(f"private {number}\n", encoding="utf-8")
    _commit_wiki(work, "private vault")
    _run(work, "push", "-q", "gitea", "vault")
    _run(work, "fetch", "-q", "origin")
    _run(work, "fetch", "-q", "gitea")

    proposed = work / "wiki" / "concepts" / "proposed.md"
    proposed.write_text("new\n", encoding="utf-8")
    monkeypatch.setattr(propose_page, "REPO_ROOT", work)
    monkeypatch.setattr(propose_page, "run_lint", lambda: True)
    monkeypatch.setattr(propose_page, "run_verify", lambda: True)
    return tmp_path


def _remote_branches(tmp_path: Path, name: str) -> set[str]:
    listed = _run(tmp_path / f"{name}.git", "branch", "--format=%(refname:short)")
    return set(listed.splitlines())


def test_default_push_never_sends_the_vault_to_the_public_remote(
    two_remotes: Path,
) -> None:
    """The default remote is the private one, so a bare `--push` is safe."""
    result = propose_page.main(
        ["--page", "wiki/concepts/proposed.md", "--base", "vault", "--push"]
    )

    assert _remote_branches(two_remotes, "origin") == {"main"}
    assert result == 0
    pushed = _remote_branches(two_remotes, "gitea") - {"main", "vault"}
    assert len(pushed) == 1 and next(iter(pushed)).startswith("wiki/")


def test_explicit_push_of_the_vault_to_the_public_remote_is_refused(
    two_remotes: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = propose_page.main(
        [
            "--page",
            "wiki/concepts/proposed.md",
            "--base",
            "vault",
            "--remote",
            "origin",
            "--push",
        ]
    )

    assert _remote_branches(two_remotes, "origin") == {"main"}
    assert result != 0
    assert "origin" in capsys.readouterr().err
    # Refused before a branch was cut: the caller is left where they stood.
    assert _run(two_remotes / "work", "branch", "--show-current") == "vault"


def test_a_public_proposal_is_refused_even_from_the_sample_base(
    two_remotes: Path,
) -> None:
    """Any wiki/ tree that differs from the public one is not the public sample."""
    work = two_remotes / "work"
    _run(work, "switch", "-q", "main")

    result = propose_page.main(
        ["--page", "wiki/concepts/proposed.md", "--remote", "origin", "--push"]
    )

    assert _remote_branches(two_remotes, "origin") == {"main"}
    assert result != 0
    # The verified commit is kept locally so it can be pushed to gitea by hand.
    kept = _run(work, "branch", "--list", "wiki/*", "--format=%(refname:short)")
    assert kept.startswith("wiki/")


def test_a_remote_without_a_known_main_is_judged_by_page_count(
    two_remotes: Path,
) -> None:
    """With no `<remote>/main` to compare, more than the sample is refused."""
    work = two_remotes / "work"
    _run(two_remotes, "init", "-q", "--bare", "-b", "main", "mirror.git")
    _run(work, "remote", "add", "mirror", str(two_remotes / "mirror.git"))

    result = propose_page.main(
        [
            "--page",
            "wiki/concepts/proposed.md",
            "--base",
            "vault",
            "--remote",
            "mirror",
            "--push",
        ]
    )

    assert _remote_branches(two_remotes, "mirror") == set()
    assert result != 0


def test_the_proposal_branch_is_cut_from_base_not_from_head(
    two_remotes: Path,
) -> None:
    """`--base` names the PR target, so the branch must descend from it alone."""
    work = two_remotes / "work"
    _run(work, "switch", "-q", "-c", "feature")
    (work / "unrelated.txt").write_text("feature work\n", encoding="utf-8")
    _run(work, "add", "--", "unrelated.txt")
    _run(work, "commit", "--no-verify", "-q", "-m", "unrelated feature work")

    result = propose_page.main(
        ["--page", "wiki/concepts/proposed.md", "--base", "vault"]
    )

    assert result == 0
    branch = _run(work, "branch", "--show-current")
    assert branch.startswith("wiki/")
    assert _run(work, "rev-parse", f"{branch}~1") == _run(work, "rev-parse", "vault")


def _scrubbed_base(work: Path, pages: int = 30) -> None:
    """A `scrubbed` branch off `main` that once added vault pages, then removed them.

    Its tip wiki/ tree is the public sample again, but `git push` sends every
    commit reachable from it, and one of those commits holds the vault.
    """
    _run(work, "switch", "-q", "-c", "scrubbed", "main")
    secrets = [f"wiki/concepts/secret-{number}.md" for number in range(pages)]
    for number, secret in enumerate(secrets):
        (work / secret).write_text(f"secret {number}\n", encoding="utf-8")
    # Staged by name: the caller's uncommitted proposal must stay uncommitted.
    _run(work, "add", "--", *secrets)
    _run(work, "commit", "--no-verify", "-q", "-m", "vault pages")
    _run(work, "rm", "-q", "--", *secrets)
    _run(work, "commit", "--no-verify", "-q", "-m", "scrub vault pages")
    _run(work, "switch", "-q", "main")


def test_a_scrubbed_tip_does_not_hide_the_vault_in_its_history(
    two_remotes: Path,
) -> None:
    """Only the tip matches `origin/main:wiki`; the push would carry the rest."""
    work = two_remotes / "work"
    _scrubbed_base(work)
    assert _run(work, "rev-parse", "scrubbed:wiki") == _run(
        work, "rev-parse", "origin/main:wiki"
    )

    refusal = propose_page.public_push_refusal("origin", "scrubbed")

    assert refusal is not None and "scrubbed" in refusal


@pytest.mark.parametrize("target", ["mirror", "url"])
def test_an_unknown_remote_is_judged_by_history_not_by_the_tip(
    two_remotes: Path, target: str
) -> None:
    """With nothing known published, every reachable commit is bounded."""
    work = two_remotes / "work"
    _scrubbed_base(work)
    _run(two_remotes, "init", "-q", "--bare", "-b", "main", "mirror.git")
    mirror_url = str(two_remotes / "mirror.git")
    if target == "mirror":
        _run(work, "remote", "add", "mirror", mirror_url)
    remote = "mirror" if target == "mirror" else mirror_url

    result = propose_page.main(
        [
            "--page",
            "wiki/concepts/proposed.md",
            "--base",
            "scrubbed",
            "--remote",
            remote,
            "--push",
        ]
    )

    assert _remote_branches(two_remotes, "mirror") == set()
    assert result != 0


def test_a_public_remote_named_by_its_url_is_still_the_public_remote(
    two_remotes: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`origin`'s own URL must be compared with `origin/main`, not page-counted."""
    work = two_remotes / "work"
    _run(work, "switch", "-q", "main")
    (work / "wiki" / "concepts" / "sample.md").write_text(
        "edited sample\n", encoding="utf-8"
    )
    origin_url = str(two_remotes / "origin.git")

    result = propose_page.main(
        ["--page", "wiki/concepts/sample.md", "--remote", origin_url, "--push"]
    )

    assert _remote_branches(two_remotes, "origin") == {"main"}
    assert result != 0
    assert "origin/main" in capsys.readouterr().err


def test_a_private_remote_named_by_its_url_still_pushes(two_remotes: Path) -> None:
    """Resolving URLs must not turn the legitimate private push into a refusal."""
    result = propose_page.main(
        [
            "--page",
            "wiki/concepts/proposed.md",
            "--base",
            "vault",
            "--remote",
            str(two_remotes / "gitea.git"),
            "--push",
        ]
    )

    assert result == 0
    pushed = _remote_branches(two_remotes, "gitea") - {"main", "vault"}
    assert len(pushed) == 1


def test_a_private_name_with_a_public_push_url_is_not_private(
    two_remotes: Path,
) -> None:
    """`git push gitea` reaches every push URL, so each one must be private."""
    work = two_remotes / "work"
    for url in (two_remotes / "gitea.git", two_remotes / "origin.git"):
        _run(work, "remote", "set-url", "--add", "--push", "gitea", str(url))

    refusal = propose_page.public_push_refusal("gitea", "vault")

    assert refusal is not None


def test_a_scrubbed_base_is_not_pushed_to_the_public_url(two_remotes: Path) -> None:
    """The reviewed reproduction: a scrubbed base, and `origin` named by URL."""
    work = two_remotes / "work"
    _scrubbed_base(work)
    (work / "wiki" / "concepts" / "s1.md").write_text("s1\n", encoding="utf-8")

    result = propose_page.main(
        [
            "--page",
            "wiki/concepts/s1.md",
            "--base",
            "scrubbed",
            "--remote",
            str(two_remotes / "origin.git"),
            "--push",
        ]
    )

    assert result != 0
    assert _remote_branches(two_remotes, "origin") == {"main"}
    # Refused on --base, before anything was branched.
    assert _run(work, "branch", "--show-current") == "main"


# ── stdout carries data only ─────────────────────────────────────────────────
#
# `snpmemory propose` calls `main` in-process, so every `print` here lands on
# the CLI's stdout ahead of the rendered payload and `propose -o json` stops
# parsing. The script has no data to emit -- its answer is the exit code -- so
# its prose is diagnostics, and diagnostics go to stderr (scout/cli/render.py).


def _git_by_command(args: tuple[str, ...], **_kwargs: object) -> object:
    if args[0] == "status":
        return _completed(" M wiki/concepts/target.md\n")
    return _completed("")


def test_the_script_writes_its_prose_to_stderr(
    proposal_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(propose_page, "git", lambda *a, **k: _git_by_command(a, **k))

    assert propose_page.main(["--page", "wiki/concepts/target.md", "--dry-run"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "New branch" in captured.err


def test_a_refusal_is_written_to_stderr(
    proposal_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(propose_page, "git", lambda *a, **k: _completed(""))

    assert propose_page.main(["--page", "wiki/concepts/target.md"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no working-tree change" in captured.err


@pytest.mark.parametrize(
    ("script", "check"),
    [("gen_index.py", "run_lint"), ("verify_addresses.py", "run_verify")],
)
def test_a_check_subprocess_never_writes_to_stdout(
    proposal_repo: Path,
    capfd: pytest.CaptureFixture[str],
    script: str,
    check: str,
) -> None:
    """A child inherits file descriptor 1 unless told otherwise, so its report
    lands on the caller's stdout whatever `sys.stdout` points at. Captured and
    relayed to stderr, it is still read by a human and never by a parser."""
    scripts = proposal_repo / "scripts"
    scripts.mkdir()
    (scripts / script).write_text(
        "import sys\nprint('CHILD REPORT')\nsys.exit(1)\n", encoding="utf-8"
    )

    assert getattr(propose_page, check)() is False

    captured = capfd.readouterr()
    assert "CHILD REPORT" not in captured.out
    assert "CHILD REPORT" in captured.err
