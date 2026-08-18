"""Tests for scripts.host_sync — Zero-Credential Host-Sync Webhook Receiver (R-2.5)."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from scripts.host_sync import SECRET, VAULT_DIR, _perform_git_sync, app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _sign(body: bytes, secret: bytes = SECRET) -> str:
    """Generate valid HMAC-SHA256 hex digest for body."""
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_health_check_endpoints(client: TestClient) -> None:
    """GET / and GET /hooks/wiki-update must return online status (HTTP 200)."""
    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert resp_root.json() == {"status": "online", "service": "host-sync"}

    resp_hook = client.get("/hooks/wiki-update")
    assert resp_hook.status_code == 200
    assert resp_hook.json() == {"status": "online", "service": "host-sync"}


def test_webhook_rejects_missing_signature(client: TestClient) -> None:
    """POST without X-Gitea-Signature or X-Hub-Signature-256 returns HTTP 403."""
    resp = client.post("/hooks/wiki-update", json={"ref": "refs/heads/main"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing signature header"


def test_webhook_rejects_invalid_signature(client: TestClient) -> None:
    """POST with incorrect HMAC signature returns HTTP 403."""
    payload = json.dumps({"ref": "refs/heads/main"}).encode("utf-8")
    resp = client.post(
        "/hooks/wiki-update",
        content=payload,
        headers={"X-Gitea-Signature": "invalid_signature_hex", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Invalid signature"


def test_webhook_handles_ping_event_without_git_sync(client: TestClient) -> None:
    """Gitea ping events return HTTP 200 pong without triggering git sync."""
    payload = json.dumps({"zen": "Zero-Credential sync", "hook_id": 1}).encode("utf-8")
    sig = _sign(payload)

    with patch("scripts.host_sync._perform_git_sync") as mock_sync:
        resp = client.post(
            "/hooks/wiki-update",
            content=payload,
            headers={
                "X-Gitea-Signature": sig,
                "X-Gitea-Event": "ping",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["event"] == "ping"
        mock_sync.assert_not_called()


def test_webhook_filters_non_target_branch_push(client: TestClient) -> None:
    """Push events on feature branches are skipped."""
    payload = json.dumps({"ref": "refs/heads/feature/some-branch"}).encode("utf-8")
    sig = _sign(payload)

    with patch("scripts.host_sync._perform_git_sync") as mock_sync:
        resp = client.post(
            "/hooks/wiki-update",
            content=payload,
            headers={
                "X-Gitea-Signature": sig,
                "X-Gitea-Event": "push",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        mock_sync.assert_not_called()


def test_webhook_accepts_valid_main_push_and_executes_sync(client: TestClient) -> None:
    """Valid push to main queues background git sync."""
    payload = json.dumps({"ref": "refs/heads/main"}).encode("utf-8")
    sig = _sign(payload)

    with patch("scripts.host_sync._perform_git_sync") as mock_sync:
        resp = client.post(
            "/hooks/wiki-update",
            content=payload,
            headers={
                "X-Gitea-Signature": sig,
                "X-Gitea-Event": "push",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        mock_sync.assert_called_once_with(VAULT_DIR)


def test_webhook_accepts_github_signature_prefix(client: TestClient) -> None:
    """Supports X-Hub-Signature-256 with sha256= prefix."""
    payload = json.dumps({"ref": "refs/heads/main"}).encode("utf-8")
    sig = f"sha256={_sign(payload)}"

    with patch("scripts.host_sync._perform_git_sync") as mock_sync:
        resp = client.post(
            "/hooks/wiki-update",
            content=payload,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_sync.assert_called_once_with(VAULT_DIR)


def test_perform_git_sync_runs_safe_wiki_checkout() -> None:
    """_perform_git_sync executes scoped git fetch, checkout -- wiki/, and clean -- wiki/."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
        _perform_git_sync("/test/vault")

        assert mock_run.call_count == 3
        mock_run.assert_any_call(
            ["git", "fetch", "origin", "main"],
            cwd="/test/vault",
            check=True,
            capture_output=True,
            text=True,
        )
        mock_run.assert_any_call(
            ["git", "checkout", "origin/main", "--", "wiki/"],
            cwd="/test/vault",
            check=True,
            capture_output=True,
            text=True,
        )
        mock_run.assert_any_call(
            ["git", "clean", "-fd", "--", "wiki/"],
            cwd="/test/vault",
            check=True,
            capture_output=True,
            text=True,
        )


def test_git_sync_preserves_external_files_and_uncommitted_edits(tmp_path: pytest.TempPathFactory) -> None:
    """Uncommitted edits and untracked files outside wiki/ are preserved during sync."""
    import subprocess
    from pathlib import Path

    base_dir = Path(str(tmp_path))
    remote_repo = base_dir / "remote_repo"
    local_repo = base_dir / "local_repo"

    remote_repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(remote_repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(remote_repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(remote_repo), check=True)

    # Initial commit in remote repo
    (remote_repo / "wiki").mkdir()
    (remote_repo / "src").mkdir()
    (remote_repo / "wiki" / "page.md").write_text("# Wiki v1\n", encoding="utf-8")
    (remote_repo / "src" / "file.py").write_text("print('original code')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(remote_repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(remote_repo), check=True)

    # Clone to local repo
    subprocess.run(["git", "clone", str(remote_repo), str(local_repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(local_repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(local_repo), check=True)

    # Remote pushes update to wiki
    (remote_repo / "wiki" / "page.md").write_text("# Wiki v2 Updated\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(remote_repo), check=True)
    subprocess.run(["git", "commit", "-m", "update wiki"], cwd=str(remote_repo), check=True)

    # Local has uncommitted changes outside wiki/ and untracked files
    (local_repo / "src" / "file.py").write_text("print('local uncommitted change')\n", encoding="utf-8")
    (local_repo / "src" / "untracked.py").write_text("print('untracked script')\n", encoding="utf-8")
    (local_repo / "wiki" / "stray.tmp").write_text("stray wiki file\n", encoding="utf-8")

    # Run _perform_git_sync on local repo
    _perform_git_sync(str(local_repo))

    # Verify wiki is updated and stray cleaned
    assert (local_repo / "wiki" / "page.md").read_text(encoding="utf-8") == "# Wiki v2 Updated\n"
    assert not (local_repo / "wiki" / "stray.tmp").exists()

    # Verify external files are preserved intact
    assert (local_repo / "src" / "file.py").read_text(encoding="utf-8") == "print('local uncommitted change')\n"
    assert (local_repo / "src" / "untracked.py").read_text(encoding="utf-8") == "print('untracked script')\n"


def test_send_test_ping_utility() -> None:
    """send_test_ping sends HMAC signed ping payload."""
    from scripts.setup_gitea_webhook import send_test_ping

    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"status": "ok", "event": "ping"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_open.return_value = mock_resp

        success = send_test_ping("http://localhost:9000/hooks/wiki-update", "dev-secret")
        assert success is True


