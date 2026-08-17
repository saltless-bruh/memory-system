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


def test_perform_git_sync_runs_fetch_and_reset() -> None:
    """_perform_git_sync calls git fetch --all and git reset --hard origin/main."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
        _perform_git_sync("/test/vault")

        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["git", "fetch", "origin"],
            cwd="/test/vault",
            check=True,
            capture_output=True,
            text=True,
        )
        mock_run.assert_any_call(
            ["git", "reset", "--hard", "origin/main"],
            cwd="/test/vault",
            check=True,
            capture_output=True,
            text=True,
        )


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

