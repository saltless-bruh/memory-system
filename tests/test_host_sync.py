"""Tests for the isolated, commit-addressed host-sync replica."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient, Response

import scripts.host_sync as host_sync

TEST_SECRET = b"unit-test-webhook-secret"


@pytest.fixture(autouse=True)
def reset_host_sync_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(host_sync, "SECRET", TEST_SECRET)
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setenv("GIT_REMOTE", "origin")
    monkeypatch.setenv("GIT_SYNC_URL", "https://example.invalid/wiki.git")
    monkeypatch.setenv("SNAPSHOT_RETENTION", "2")
    host_sync._reset_sync_state_for_tests()
    yield
    host_sync._reset_sync_state_for_tests()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # Starlette's synchronous TestClient portal hangs on Python 3.14 with the
    # installed AnyIO version. ASGITransport drives the same app directly.
    transport = ASGITransport(app=host_sync.app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_upstream(tmp_path: Path) -> Path:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run_git(upstream, "init", "--initial-branch=main")
    _run_git(upstream, "config", "user.name", "Host Sync Test")
    _run_git(upstream, "config", "user.email", "host-sync@example.invalid")
    (upstream / "wiki").mkdir()
    (upstream / "src").mkdir()
    (upstream / "wiki" / "page.md").write_text("# Wiki v1\n", encoding="utf-8")
    (upstream / "wiki" / "delete.md").write_text("delete me\n", encoding="utf-8")
    (upstream / "src" / "app.py").write_text("print('v1')\n", encoding="utf-8")
    (upstream / "src" / "rename.py").write_text("rename me\n", encoding="utf-8")
    _run_git(upstream, "add", ".")
    _run_git(upstream, "commit", "-m", "initial")
    return upstream


def _commit_wiki(upstream: Path, content: str) -> str:
    (upstream / "wiki" / "page.md").write_text(content, encoding="utf-8")
    _run_git(upstream, "add", "wiki/page.md")
    _run_git(upstream, "commit", "-m", content.strip())
    return _run_git(upstream, "rev-parse", "HEAD")


def _sign(body: bytes) -> str:
    return hmac.new(TEST_SECRET, body, hashlib.sha256).hexdigest()


async def _post(client: AsyncClient, payload: object, *, event: str = "push") -> Response:
    body = json.dumps(payload).encode("utf-8")
    return await client.post(
        "/hooks/wiki-update",
        content=body,
        headers={
            "X-Gitea-Signature": _sign(body),
            "X-Gitea-Event": event,
            "Content-Type": "application/json",
        },
    )


def _workspace_bytes(root: Path) -> dict[str, bytes | str]:
    captured: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            captured[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            captured[relative] = path.read_bytes()
    return captured


async def test_liveness_is_independent_from_initial_readiness(client: AsyncClient) -> None:
    live = await client.get("/live")
    ready = await client.get("/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "alive", "service": "host-sync"}
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert ready.json()["published_commit"] is None


async def test_ready_reports_published_commit(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))

    assert host_sync._perform_git_sync(str(replica)) is True

    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["published_commit"] == _run_git(upstream, "rev-parse", "HEAD")


def test_startup_begins_not_ready_and_queues_one_sync() -> None:
    with patch("scripts.host_sync.threading.Thread") as thread:
        host_sync._start_initial_sync()

    thread.assert_called_once()
    thread.return_value.start.assert_called_once_with()
    assert host_sync._read_state()["ready"] is False
    assert host_sync._read_state()["syncing"] is True


async def test_webhook_rejects_missing_or_invalid_signature(client: AsyncClient) -> None:
    unsigned = await client.post("/hooks/wiki-update", json={"ref": "refs/heads/main"})
    invalid = await client.post(
        "/hooks/wiki-update",
        content=b"{}",
        headers={"X-Gitea-Signature": "not-valid"},
    )

    assert unsigned.status_code == 403
    assert invalid.status_code == 403


@pytest.mark.parametrize("payload", [[], "text", 7, None])
async def test_webhook_rejects_non_object_payloads(client: AsyncClient, payload: object) -> None:
    response = await _post(client, payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Payload must be a JSON object"


async def test_webhook_rejects_malformed_json_without_scheduling(client: AsyncClient) -> None:
    body = b"{not-json"
    with patch("scripts.host_sync._perform_git_sync") as sync:
        response = await client.post(
            "/hooks/wiki-update",
            content=body,
            headers={"X-Gitea-Signature": _sign(body), "X-Gitea-Event": "push"},
        )

    assert response.status_code == 400
    sync.assert_not_called()


@pytest.mark.parametrize(
    "ref",
    [
        "refs/tags/main",
        "refs/heads/feature/main",
        "main",
        "refs/heads/main/extra",
        "",
    ],
)
async def test_webhook_requires_exact_target_branch_ref(client: AsyncClient, ref: str) -> None:
    with patch("scripts.host_sync._perform_git_sync") as sync:
        response = await _post(client, {"ref": ref})

    assert response.status_code == 200 if ref else 400
    if ref:
        assert response.json()["status"] == "skipped"
    sync.assert_not_called()


async def test_webhook_queues_target_push_before_completion(client: AsyncClient) -> None:
    with patch("scripts.host_sync._start_daemon_sync") as start:
        response = await _post(client, {"ref": "refs/heads/main"})

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["published_commit"] is None
    start.assert_called_once_with(host_sync.VAULT_DIR)


async def test_ping_and_non_push_events_never_schedule_sync(client: AsyncClient) -> None:
    with patch("scripts.host_sync._perform_git_sync") as sync:
        ping = await _post(client, {"zen": "test"}, event="ping")
        issue = await _post(client, {"ref": "refs/heads/main"}, event="issues")

    assert ping.status_code == 200
    assert ping.json()["status"] == "ok"
    assert issue.status_code == 200
    assert issue.json()["status"] == "skipped"
    sync.assert_not_called()


def test_sync_publishes_complete_commit_addressed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    commit = _run_git(upstream, "rev-parse", "HEAD")

    assert host_sync._perform_git_sync(str(replica)) is True

    assert (replica / host_sync.REPLICA_MARKER).read_text(encoding="utf-8")
    assert (replica / "repository" / ".git").is_dir()
    snapshot = replica / "snapshots" / commit
    assert (snapshot / host_sync.SNAPSHOT_MARKER).is_file()
    assert (snapshot / "wiki" / "page.md").read_text(encoding="utf-8") == "# Wiki v1\n"
    assert (replica / "current").is_symlink()
    assert os.readlink(replica / "current") == f"snapshots/{commit}"
    assert not (snapshot / "src").exists()


def test_sync_never_changes_a_separate_developer_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    developer = tmp_path / "developer"
    replica = tmp_path / "replica"
    _run_git(tmp_path, "clone", str(upstream), str(developer))
    _run_git(developer, "config", "user.name", "Developer")
    _run_git(developer, "config", "user.email", "developer@example.invalid")

    (developer / "src" / "app.py").write_text("tracked edit\n", encoding="utf-8")
    (developer / "wiki" / "delete.md").unlink()
    _run_git(developer, "mv", "src/rename.py", "src/renamed.py")
    (developer / "src" / "untracked.py").write_text("untracked\n", encoding="utf-8")
    (developer / "wiki" / "untracked.md").write_text("untracked wiki\n", encoding="utf-8")
    before_bytes = _workspace_bytes(developer)
    before_status = _run_git(developer, "status", "--porcelain=v1", "-z")
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    git_cwds: list[Path] = []
    real_run_git = host_sync._run_git

    def recording_run_git(args: list[str], *, cwd: Path) -> str:
        git_cwds.append(cwd.resolve())
        return real_run_git(args, cwd=cwd)

    monkeypatch.setattr(host_sync, "_run_git", recording_run_git)

    assert host_sync._perform_git_sync(str(replica)) is True

    assert developer.resolve() not in git_cwds
    assert _workspace_bytes(developer) == before_bytes
    assert _run_git(developer, "status", "--porcelain=v1", "-z") == before_status


def test_failed_fetch_keeps_last_known_good_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    assert host_sync._perform_git_sync(str(replica)) is True
    current_before = os.readlink(replica / "current")
    content_before = (replica / "current" / "wiki" / "page.md").read_bytes()
    monkeypatch.setenv("GIT_SYNC_URL", str(tmp_path / "missing"))

    assert host_sync._perform_git_sync(str(replica)) is False

    assert os.readlink(replica / "current") == current_before
    assert (replica / "current" / "wiki" / "page.md").read_bytes() == content_before
    state = host_sync._read_state()
    assert state["ready"] is True
    assert state["published_commit"] == Path(current_before).name
    assert state["last_error"] is not None


def test_existing_repository_origin_is_bound_to_configured_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    assert host_sync._perform_git_sync(str(replica)) is True

    replacement = tmp_path / "replacement"
    _run_git(tmp_path, "clone", str(upstream), str(replacement))
    _run_git(replacement, "config", "user.name", "Host Sync Replacement")
    _run_git(replacement, "config", "user.email", "replacement@example.invalid")
    expected_commit = _commit_wiki(replacement, "# Wiki replacement\n")
    monkeypatch.setenv("GIT_SYNC_URL", str(replacement))

    assert host_sync._perform_git_sync(str(replica)) is True

    repository = replica / "repository"
    assert _run_git(repository, "remote", "get-url", "origin") == str(replacement)
    assert os.readlink(replica / "current") == f"snapshots/{expected_commit}"
    assert (replica / "current" / "wiki" / "page.md").read_text(
        encoding="utf-8"
    ) == "# Wiki replacement\n"


def test_publish_failure_keeps_previous_current_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    assert host_sync._perform_git_sync(str(replica)) is True
    current_before = os.readlink(replica / "current")
    _commit_wiki(upstream, "# Wiki v2\n")

    with patch("scripts.host_sync._publish_current", side_effect=OSError("injected")):
        assert host_sync._perform_git_sync(str(replica)) is False

    assert os.readlink(replica / "current") == current_before
    assert (replica / "current" / "wiki" / "page.md").read_text(encoding="utf-8") == "# Wiki v1\n"


def test_prune_failure_keeps_previous_current_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    assert host_sync._perform_git_sync(str(replica)) is True
    current_before = os.readlink(replica / "current")
    _commit_wiki(upstream, "# Wiki v2\n")

    with patch("scripts.host_sync._prune_snapshots", side_effect=OSError("injected")):
        assert host_sync._perform_git_sync(str(replica)) is False

    assert os.readlink(replica / "current") == current_before
    assert (replica / "current" / "wiki" / "page.md").read_text(
        encoding="utf-8"
    ) == "# Wiki v1\n"


def test_snapshot_pruning_retains_current_and_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _make_upstream(tmp_path)
    replica = tmp_path / "replica"
    monkeypatch.setenv("GIT_SYNC_URL", str(upstream))
    first = _run_git(upstream, "rev-parse", "HEAD")
    assert host_sync._perform_git_sync(str(replica)) is True
    second = _commit_wiki(upstream, "# Wiki v2\n")
    assert host_sync._perform_git_sync(str(replica)) is True
    third = _commit_wiki(upstream, "# Wiki v3\n")
    assert host_sync._perform_git_sync(str(replica)) is True

    snapshots = {path.name for path in (replica / "snapshots").iterdir() if path.is_dir()}
    assert snapshots == {second, third}
    assert first not in snapshots
    assert os.readlink(replica / "current") == f"snapshots/{third}"


def test_nonempty_unmarked_replica_root_is_rejected_without_changes(tmp_path: Path) -> None:
    unsafe = tmp_path / "developer-like-directory"
    unsafe.mkdir()
    file = unsafe / "important.txt"
    file.write_text("do not touch\n", encoding="utf-8")

    assert host_sync._perform_git_sync(str(unsafe)) is False

    assert file.read_text(encoding="utf-8") == "do not touch\n"
    assert not (unsafe / host_sync.REPLICA_MARKER).exists()


def test_project_workspace_is_rejected() -> None:
    with pytest.raises(host_sync.ReplicaSafetyError):
        host_sync._validate_replica_root(host_sync.PROJECT_ROOT)


def test_sync_calls_are_serialized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    active = 0
    maximum_active = 0
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    release_second = threading.Event()
    calls = 0

    def fake_sync_once(_root: Path) -> str:
        nonlocal active, maximum_active, calls
        calls += 1
        active += 1
        maximum_active = max(maximum_active, active)
        if calls == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        else:
            second_entered.set()
            assert release_second.wait(timeout=2)
        active -= 1
        return "a" * 40

    # The first worker waits only long enough for the second to attempt the lock.
    monkeypatch.setattr(host_sync, "_sync_once", fake_sync_once)
    results: list[bool] = []
    first = threading.Thread(
        target=lambda: results.append(host_sync._perform_git_sync(str(tmp_path / "replica")))
    )
    second = threading.Thread(
        target=lambda: results.append(host_sync._perform_git_sync(str(tmp_path / "replica")))
    )
    threads = [first, second]
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    release_first.set()
    assert second_entered.wait(timeout=2)
    still_syncing = host_sync._read_state()["syncing"]
    release_second.set()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(results) == [True, True]
    assert maximum_active == 1
    assert still_syncing is True
