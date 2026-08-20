"""Regression tests for current-state and all-ref secret scanning."""

from __future__ import annotations

import random
import string
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import scan_secrets

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_WORKFLOW = REPO_ROOT / ".gitea" / "workflows" / "security.yaml"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Secret Scanner Test")
    _git(path, "config", "user.email", "scanner@example.invalid")
    return path


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _synthetic_token(length: int = 20) -> str:
    """Build candidates at runtime so this test file is not itself a finding."""
    return "".join(("s", "k", "-", "A" * length))


def _realistic_token(seed: int = 20260819, length: int = 48) -> str:
    """Build a high-entropy, provider-shaped candidate at runtime.

    Assembled rather than written literally so this file never carries a
    token-shaped string of its own. The alphabet is mixed case, which no
    placeholder pattern admits.
    """
    rng = random.Random(seed)
    alphabet = string.ascii_letters + string.digits
    return "".join(("s", "k", "-", *(rng.choice(alphabet) for _ in range(length))))


def _placeholder_token(suffix: str = "placeholder") -> str:
    """Assemble the project's documented placeholder at runtime."""
    return "".join(("s", "k", "-", "local", "-", "dev", "-", suffix))


def test_detector_covers_revoked_token_shape_and_near_misses() -> None:
    candidate = _synthetic_token(20)

    findings = scan_secrets.find_secrets(candidate, "notes.txt")

    assert [finding.label for finding in findings] == ["OpenAI / LiteLLM token"]
    assert scan_secrets.find_secrets(_synthetic_token(19), "notes.txt") == []


def test_placeholder_exemption_is_value_scoped_not_path_scoped() -> None:
    """Documented placeholders are exempt wherever they appear (finding M6)."""
    placeholders = (
        "".join(("s", "k", "-", "placeholder", "-", "x" * 20)),
        _placeholder_token(),
    )
    paths = (
        ".env.example",
        "docker-compose.yml",
        "application.py",
        "tests/eval_ragas.py",
        "artifacts/multimodal_system_analysis.md",
    )

    for placeholder in placeholders:
        for path in paths:
            assert scan_secrets.find_secrets(placeholder, path) == [], path


def test_placeholder_prefix_cannot_smuggle_real_token_material() -> None:
    """The exemption is a fullmatch; a placeholder prefix grants nothing."""
    prefix = "".join(("s", "k", "-", "local", "-", "dev", "-"))
    near_misses = (
        prefix + _realistic_token()[3:],
        prefix + "A" * 20,
        prefix + "_" * 20,
        _placeholder_token() + " " + _synthetic_token(),
    )

    for candidate in near_misses:
        assert scan_secrets.find_secrets(candidate, ".env.example"), candidate
        assert scan_secrets.find_secrets(candidate, "application.py"), candidate


def test_worktree_and_index_scan_different_bytes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    target = repo / "settings.txt"
    target.write_text("SETTING=not-a-secret\n", encoding="utf-8")
    _commit_all(repo, "initial")

    staged_candidate = _synthetic_token()
    target.write_text(f"TOKEN={staged_candidate}\n", encoding="utf-8")
    _git(repo, "add", "settings.txt")
    target.write_text("SETTING=safe-working-copy\n", encoding="utf-8")

    assert scan_secrets.scan_worktree(repo) == []
    index_findings = scan_secrets.scan_index(repo)
    assert len(index_findings) == 1
    assert index_findings[0].source == "INDEX"
    assert staged_candidate not in index_findings[0].format()


def test_worktree_scan_detects_unstaged_tracked_edit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    target = repo / "settings.txt"
    target.write_text("SETTING=safe\n", encoding="utf-8")
    _commit_all(repo, "initial")
    candidate = _synthetic_token()
    target.write_text(f"TOKEN={candidate}\n", encoding="utf-8")

    findings = scan_secrets.scan_worktree(repo)

    assert len(findings) == 1
    assert findings[0].source == "WORKTREE"
    assert candidate not in findings[0].format()


def test_untracked_scan_excludes_ignored_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("ignored.env\n", encoding="utf-8")
    _commit_all(repo, "ignore local credentials")
    candidate = _synthetic_token()
    (repo / "untracked.txt").write_text(candidate, encoding="utf-8")
    (repo / "ignored.env").write_text(candidate, encoding="utf-8")

    findings = scan_secrets.scan_untracked(repo)

    assert [finding.path for finding in findings] == ["untracked.txt"]
    assert candidate not in findings[0].format()


def test_all_current_combines_worktree_index_and_untracked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("safe\n", encoding="utf-8")
    _commit_all(repo, "initial")
    (repo / "tracked.txt").write_text(_synthetic_token(), encoding="utf-8")
    (repo / "new.txt").write_text(_synthetic_token(21), encoding="utf-8")

    sources = {finding.source for finding in scan_secrets.scan_all_current(repo)}

    assert sources == {"WORKTREE", "UNTRACKED"}


def test_nul_containing_bytes_are_scanned_for_ascii_tokens() -> None:
    candidate = _synthetic_token()

    findings = scan_secrets._scan_bytes(
        b"binary-prefix\0TOKEN=" + candidate.encode("ascii"),
        source="CONTENT",
        path="mixed.bin",
    )

    assert len(findings) == 1
    assert findings[0].label == "OpenAI / LiteLLM token"
    assert candidate not in findings[0].format()


def test_oversized_targets_fail_closed_in_every_repository_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    target = repo / "oversized.txt"
    target.write_text("x" * 64, encoding="utf-8")
    _commit_all(repo, "add oversized target")
    monkeypatch.setattr(scan_secrets, "MAX_FILE_BYTES", 16)

    for findings in (
        scan_secrets.scan_worktree(repo),
        scan_secrets.scan_index(repo),
        scan_secrets.scan_git_history(repo),
    ):
        assert len(findings) == 1
        assert findings[0].path == "oversized.txt"
        assert "incomplete" in findings[0].label
        assert "[REDACTED]" in findings[0].format()


def test_unaddressable_worktree_target_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("safe", encoding="utf-8")
    _commit_all(repo, "add tracked target")
    monkeypatch.setattr(scan_secrets, "_safe_worktree_path", lambda *_args: None)

    findings = scan_secrets.scan_worktree(repo)

    assert len(findings) == 1
    assert findings[0].path == "tracked.txt"
    assert "unaddressable" in findings[0].label


def test_unreadable_worktree_target_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("safe", encoding="utf-8")
    _commit_all(repo, "add tracked target")

    def deny_read(_path: Path) -> bytes:
        raise PermissionError("synthetic denied read")

    monkeypatch.setattr(Path, "read_bytes", deny_read)

    findings = scan_secrets.scan_worktree(repo)

    assert len(findings) == 1
    assert findings[0].path == "tracked.txt"
    assert "unreadable" in findings[0].label


def test_history_scans_deleted_blobs_reachable_from_all_refs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("safe\n", encoding="utf-8")
    _commit_all(repo, "initial")
    _git(repo, "switch", "-c", "feature")
    candidate = _synthetic_token()
    (repo / "removed.txt").write_text(candidate, encoding="utf-8")
    _commit_all(repo, "add candidate")
    _git(repo, "tag", "contains-candidate")
    (repo / "removed.txt").unlink()
    _commit_all(repo, "delete candidate")
    _git(repo, "switch", "main")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature history")

    findings = scan_secrets.scan_git_history(repo)

    assert len(findings) == 1
    assert findings[0].source == "HISTORY"
    assert findings[0].path == "removed.txt"
    assert candidate not in findings[0].format()


def test_history_reports_every_path_a_flagged_blob_occupies(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    candidate = _realistic_token()
    (repo / "settings.py").write_text(candidate, encoding="utf-8")
    (repo / "application.py").write_text(candidate, encoding="utf-8")
    _commit_all(repo, "identical bytes at two paths")

    findings = scan_secrets.scan_git_history(repo)

    assert sorted(finding.path for finding in findings) == [
        "application.py",
        "settings.py",
    ]
    assert all(candidate not in finding.format() for finding in findings)


def test_history_only_placeholder_no_longer_fails_the_gate(tmp_path: Path) -> None:
    """The M6 regression: a placeholder surviving only in history must pass."""
    repo = _init_repo(tmp_path / "repo")
    placeholder = _placeholder_token()
    (repo / "notes.md").write_text(placeholder, encoding="utf-8")
    _commit_all(repo, "add placeholder")
    (repo / "notes.md").unlink()
    _commit_all(repo, "remove placeholder")

    assert scan_secrets.scan_all_current(repo) == []
    assert scan_secrets.scan_git_history(repo) == []


def test_real_token_in_tracked_file_still_fails_every_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative control: the value-scoped exemption must not neuter the gate."""
    candidate = _realistic_token()
    assert not any(
        pattern.fullmatch(candidate) for pattern in scan_secrets.PLACEHOLDER_VALUES
    )
    repo = _init_repo(tmp_path / "repo")
    (repo / "application.py").write_text(f'TOKEN = "{candidate}"\n', encoding="utf-8")
    _commit_all(repo, "add credential")

    for findings in (
        scan_secrets.scan_worktree(repo),
        scan_secrets.scan_index(repo),
        scan_secrets.scan_git_history(repo),
    ):
        assert [finding.path for finding in findings] == ["application.py"]
        assert candidate not in findings[0].format()

    monkeypatch.setattr(
        sys,
        "argv",
        ["scan_secrets.py", "--repo", str(repo), "--all-current", "--history"],
    )

    assert scan_secrets.main() == 1
    assert candidate not in capsys.readouterr().err


def test_repository_has_no_secret_in_current_tracked_bytes() -> None:
    findings = scan_secrets.scan_worktree(REPO_ROOT)
    assert findings == [], "\n".join(finding.format() for finding in findings)


def test_repository_passes_the_scan_the_ci_gate_runs() -> None:
    """Mirror `.gitea/workflows/security.yaml` locally so M6 cannot recur."""
    findings = scan_secrets.scan_all_current(REPO_ROOT) + scan_secrets.scan_git_history(
        REPO_ROOT
    )
    assert findings == [], "\n".join(finding.format() for finding in findings)


def test_diagnostics_are_fully_redacted() -> None:
    candidate = _synthetic_token(32)
    finding = scan_secrets.find_secrets(candidate, "sample.txt")[0]

    diagnostic = finding.format()

    assert candidate not in diagnostic
    assert candidate[:4] not in diagnostic
    assert candidate[-4:] not in diagnostic
    assert "[REDACTED]" in diagnostic


def test_security_workflow_runs_immutable_scanners_and_always_runs_gitleaks() -> None:
    content = SECURITY_WORKFLOW.read_text(encoding="utf-8")

    assert "path: target" in content
    assert "path: trusted-security" in content
    assert "github.base_ref || github.event.repository.default_branch" in content
    assert 'cd "$GITHUB_WORKSPACE/trusted-security"' in content
    assert (
        'scripts/scan_secrets.py --all-current --history --repo '
        '"$GITHUB_WORKSPACE/target"'
    ) in content
    assert 'trusted-security/.gitleaks.toml:/trusted/gitleaks.toml:ro' in content
    assert "--config=/trusted/gitleaks.toml" in content

    gitleaks_step = content.index("Independent Gitleaks all-history scan")
    assert "if: always()" in content[gitleaks_step : gitleaks_step + 200]
