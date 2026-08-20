"""Publish an isolated, commit-addressed wiki replica from signed webhooks.

The service owns one marked replica root.  Git data lives under ``repository``;
immutable wiki snapshots live under ``snapshots/<commit>``; and readers follow
the atomically replaced ``current`` symlink.  It never accepts a developer
checkout as its replica root.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import threading
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("host-sync")

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = (
    _SCRIPT_DIR.parent if (_SCRIPT_DIR.parent / "pyproject.toml").is_file() else _SCRIPT_DIR
)
VAULT_DIR = os.environ.get("VAULT_REPLICA_DIR", "/vault-replica")
SECRET = os.environ.get("WEBHOOK_SECRET", "").encode("utf-8")

REPLICA_MARKER = ".snp-host-sync-replica"
SNAPSHOT_MARKER = ".complete"
_REPLICA_MARKER_CONTENT = "snp-host-sync-replica-v1\n"
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
_REMOTE_RE = re.compile(r"[A-Za-z0-9._-]+")

_sync_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "ready": False,
    "syncing": False,
    "pending_syncs": 0,
    "published_commit": None,
    "last_error": "Initial synchronization has not completed",
}


class ReplicaSafetyError(RuntimeError):
    """Raised before an operation could escape the managed replica root."""


class SyncConfigurationError(RuntimeError):
    """Raised when required Git synchronization configuration is invalid."""


def _reset_sync_state_for_tests() -> None:
    """Reset process state; deliberately private and used by isolated tests."""
    with _state_lock:
        _state.update(
            ready=False,
            syncing=False,
            pending_syncs=0,
            published_commit=None,
            last_error="Initial synchronization has not completed",
        )


def _read_state() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def _mark_sync_queued() -> None:
    with _state_lock:
        _state["pending_syncs"] += 1
        _state["syncing"] = True


def _mark_success(commit: str) -> None:
    with _state_lock:
        _state["pending_syncs"] = max(0, _state["pending_syncs"] - 1)
        _state.update(
            ready=True,
            syncing=_state["pending_syncs"] > 0,
            published_commit=commit,
            last_error=None,
        )


def _mark_failure(message: str, fallback_commit: str | None = None) -> None:
    with _state_lock:
        _state["pending_syncs"] = max(0, _state["pending_syncs"] - 1)
        published = _state["published_commit"] or fallback_commit
        _state.update(
            ready=published is not None,
            syncing=_state["pending_syncs"] > 0,
            published_commit=published,
            last_error=message,
        )


def _validate_replica_root(raw_path: str | Path) -> Path:
    supplied = Path(raw_path).expanduser()
    if supplied.is_symlink():
        raise ReplicaSafetyError("Replica root must not be a symlink")
    root = supplied.resolve(strict=False)
    project = PROJECT_ROOT.resolve(strict=False)
    if root == Path(root.anchor):
        raise ReplicaSafetyError("Filesystem root cannot be used as a replica")
    if root == project or project in root.parents:
        raise ReplicaSafetyError("Project workspace cannot be used as a replica")
    return root


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _prepare_replica_root(raw_path: str | Path) -> Path:
    root = _validate_replica_root(raw_path)
    if root.exists() and not root.is_dir():
        raise ReplicaSafetyError("Replica root is not a directory")
    if not root.exists():
        root.mkdir(parents=True, mode=0o750)

    marker = root / REPLICA_MARKER
    if marker.exists():
        if marker.is_symlink() or marker.read_text(encoding="utf-8") != _REPLICA_MARKER_CONTENT:
            raise ReplicaSafetyError("Replica marker is invalid")
    else:
        if any(root.iterdir()):
            raise ReplicaSafetyError("Refusing a nonempty replica root without its marker")
        marker.write_text(_REPLICA_MARKER_CONTENT, encoding="utf-8")

    snapshots = root / "snapshots"
    if snapshots.is_symlink():
        raise ReplicaSafetyError("Snapshots directory must not be a symlink")
    snapshots.mkdir(mode=0o750, exist_ok=True)
    return root


def _safe_remove_tree(path: Path, root: Path) -> None:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=False)
    if resolved_path == resolved_root or not _is_within(resolved_path, resolved_root):
        raise ReplicaSafetyError("Cleanup target escapes the replica root")
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _git_timeout() -> float:
    raw = os.environ.get("GIT_TIMEOUT_SECONDS", "60")
    try:
        value = float(raw)
    except ValueError as exc:
        raise SyncConfigurationError("GIT_TIMEOUT_SECONDS must be numeric") from exc
    if not 1 <= value <= 600:
        raise SyncConfigurationError("GIT_TIMEOUT_SECONDS must be between 1 and 600")
    return value


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    """Run one bounded, non-interactive Git operation without logging credentials."""
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-c", f"safe.directory={cwd}", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=_git_timeout(),
        env=environment,
    )
    return result.stdout.strip()


def _validated_git_config() -> tuple[str, str, str]:
    git_url = os.environ.get("GIT_SYNC_URL", "").strip()
    branch = os.environ.get("GIT_BRANCH", "").strip()
    remote = os.environ.get("GIT_REMOTE", "origin").strip()
    if not git_url:
        raise SyncConfigurationError("GIT_SYNC_URL is required")
    if (
        not branch
        or branch.startswith(("/", "-"))
        or branch.endswith(("/", ".", ".lock"))
        or ".." in branch
        or "//" in branch
        or any(character.isspace() for character in branch)
        or any(character in branch for character in "~^:?*[\\")
    ):
        raise SyncConfigurationError("GIT_BRANCH is not a valid branch name")
    if not _REMOTE_RE.fullmatch(remote):
        raise SyncConfigurationError("GIT_REMOTE is invalid")
    return git_url, branch, remote


def _ensure_repository(root: Path, git_url: str, remote: str) -> Path:
    repository = root / "repository"
    if repository.is_symlink():
        raise ReplicaSafetyError("Repository path must not be a symlink")
    if repository.exists():
        if not (repository / ".git").is_dir():
            raise ReplicaSafetyError("Managed repository is not a Git worktree")
        _bind_repository_remote(repository, git_url, remote)
        return repository

    staging = root / f".repository-staging-{uuid.uuid4().hex}"
    try:
        _run_git(
            ["clone", "--no-checkout", "--origin", remote, git_url, staging.name],
            cwd=root,
        )
        if not (staging / ".git").is_dir():
            raise ReplicaSafetyError("Clone did not create the expected repository")
        os.replace(staging, repository)
    except Exception:
        _safe_remove_tree(staging, root)
        raise
    _bind_repository_remote(repository, git_url, remote)
    return repository


def _bind_repository_remote(repository: Path, git_url: str, remote: str) -> None:
    """Make the managed fetch remote exactly match current configuration."""
    remotes = set(_run_git(["remote"], cwd=repository).splitlines())
    if remote not in remotes:
        _run_git(["remote", "add", remote, git_url], cwd=repository)
    configured = tuple(
        line
        for line in _run_git(["remote", "get-url", "--all", remote], cwd=repository).splitlines()
        if line
    )
    if configured != (git_url,):
        _run_git(
            ["config", "--replace-all", f"remote.{remote}.url", git_url],
            cwd=repository,
        )
    verified = tuple(
        line
        for line in _run_git(["remote", "get-url", "--all", remote], cwd=repository).splitlines()
        if line
    )
    if verified != (git_url,):
        raise ReplicaSafetyError("Managed repository remote does not match configuration")


def _read_current_commit(root: Path) -> str | None:
    current = root / "current"
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise ReplicaSafetyError("Current publication pointer must be a symlink")
    target = Path(os.readlink(current))
    if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != "snapshots":
        raise ReplicaSafetyError("Current publication pointer has an invalid target")
    commit = target.parts[1]
    if not _COMMIT_RE.fullmatch(commit):
        raise ReplicaSafetyError("Current publication pointer has an invalid commit")
    snapshot = root / target
    if not _is_within(snapshot.resolve(strict=False), (root / "snapshots").resolve(strict=True)):
        raise ReplicaSafetyError("Current publication pointer escapes snapshots")
    if not (snapshot / SNAPSHOT_MARKER).is_file():
        raise ReplicaSafetyError("Current snapshot is incomplete")
    return commit


def _materialize_snapshot(root: Path, repository: Path, commit: str) -> Path:
    if not _COMMIT_RE.fullmatch(commit):
        raise ReplicaSafetyError("Git returned an invalid commit identifier")
    snapshots = root / "snapshots"
    target = snapshots / commit
    if target.exists():
        marker = target / SNAPSHOT_MARKER
        if target.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8").strip() != commit:
            raise ReplicaSafetyError("Existing snapshot is incomplete or invalid")
        return target

    staging = snapshots / f".staging-{uuid.uuid4().hex}"
    archive = snapshots / f".archive-{uuid.uuid4().hex}.tar"
    staging.mkdir(mode=0o750)
    try:
        _run_git(
            ["archive", "--format=tar", f"--output={archive}", commit, "wiki"],
            cwd=repository,
        )
        with tarfile.open(archive, mode="r:") as bundle:
            bundle.extractall(staging, filter="data")
        if not (staging / "wiki").is_dir():
            raise ReplicaSafetyError("Commit does not contain a wiki directory")
        (staging / SNAPSHOT_MARKER).write_text(f"{commit}\n", encoding="utf-8")
        os.replace(staging, target)
    except Exception:
        _safe_remove_tree(staging, root)
        raise
    finally:
        if archive.exists():
            archive.unlink()
    return target


def _publish_current(root: Path, commit: str) -> str | None:
    previous = _read_current_commit(root)
    temporary = root / f".current-{uuid.uuid4().hex}"
    temporary.symlink_to(Path("snapshots") / commit, target_is_directory=True)
    try:
        current = root / "current"
        if current.exists() and not current.is_symlink():
            raise ReplicaSafetyError("Refusing to replace a non-symlink current path")
        os.replace(temporary, current)
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return previous


def _snapshot_retention() -> int:
    raw = os.environ.get("SNAPSHOT_RETENTION", "2")
    try:
        retention = int(raw)
    except ValueError as exc:
        raise SyncConfigurationError("SNAPSHOT_RETENTION must be an integer") from exc
    if not 2 <= retention <= 100:
        raise SyncConfigurationError("SNAPSHOT_RETENTION must be between 2 and 100")
    return retention


def _prune_snapshots(root: Path, *, current: str, previous: str | None) -> None:
    snapshots_root = (root / "snapshots").resolve(strict=True)
    candidates: list[Path] = []
    for candidate in snapshots_root.iterdir():
        if candidate.is_symlink() or not candidate.is_dir() or not _COMMIT_RE.fullmatch(candidate.name):
            continue
        marker = candidate / SNAPSHOT_MARKER
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != candidate.name:
            continue
        candidates.append(candidate)

    protected = {current}
    if previous is not None:
        protected.add(previous)
    newest = sorted(candidates, key=lambda item: item.stat().st_mtime_ns, reverse=True)
    keep = set(protected)
    for candidate in newest:
        if len(keep) >= _snapshot_retention():
            break
        keep.add(candidate.name)
    for candidate in candidates:
        if candidate.name not in keep:
            _safe_remove_tree(candidate, root)


def _sync_once(root: Path) -> str:
    managed_root = _prepare_replica_root(root)
    git_url, branch, remote = _validated_git_config()
    repository = _ensure_repository(managed_root, git_url, remote)
    _run_git(
        [
            "fetch",
            "--prune",
            remote,
            f"refs/heads/{branch}:refs/remotes/{remote}/{branch}",
        ],
        cwd=repository,
    )
    commit = _run_git(
        ["rev-parse", "--verify", f"refs/remotes/{remote}/{branch}^{{commit}}"],
        cwd=repository,
    )
    _materialize_snapshot(managed_root, repository, commit)
    previous = _read_current_commit(managed_root)
    _prune_snapshots(managed_root, current=commit, previous=previous)
    _publish_current(managed_root, commit)
    return commit


def _existing_published_commit(raw_root: str | Path) -> str | None:
    try:
        root = _validate_replica_root(raw_root)
        marker = root / REPLICA_MARKER
        if marker.read_text(encoding="utf-8") != _REPLICA_MARKER_CONTENT:
            return None
        return _read_current_commit(root)
    except (OSError, ReplicaSafetyError):
        return None


def _perform_git_sync(vault_path: str = VAULT_DIR, *, queued: bool = False) -> bool:
    """Serialize one sync and preserve a published snapshot on every failure."""
    if not queued:
        _mark_sync_queued()
    with _sync_lock:
        try:
            commit = _sync_once(Path(vault_path))
        except (OSError, subprocess.SubprocessError, tarfile.TarError, RuntimeError) as exc:
            # Do not expose command output or URLs, which can contain credentials.
            error = f"{type(exc).__name__}: synchronization failed"
            fallback = _existing_published_commit(vault_path)
            _mark_failure(error, fallback)
            logger.error("Host-sync failed (%s)", type(exc).__name__)
            return False
        _mark_success(commit)
        logger.info("Published wiki snapshot at commit %s", commit)
        return True


def _start_daemon_sync(vault_path: str) -> None:
    _mark_sync_queued()
    try:
        thread = threading.Thread(
            target=_perform_git_sync,
            args=(vault_path,),
            kwargs={"queued": True},
            name="host-sync-worker",
            daemon=True,
        )
        thread.start()
    except (OSError, RuntimeError) as exc:
        _mark_failure(f"{type(exc).__name__}: worker start failed")
        raise


def _start_initial_sync() -> None:
    _start_daemon_sync(VAULT_DIR)


@asynccontextmanager
async def _lifespan(_application: FastAPI) -> AsyncIterator[None]:
    _start_initial_sync()
    yield


app = FastAPI(title="SNP Host-Sync Service", version="3.0.0", lifespan=_lifespan)


@app.get("/")
@app.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive", "service": "host-sync"}


@app.get("/ready")
@app.get("/hooks/wiki-update")
async def readiness() -> JSONResponse:
    state_payload = _read_state()
    payload = {
        "status": "ready" if state_payload["ready"] else "not_ready",
        "service": "host-sync",
        "syncing": state_payload["syncing"],
        "published_commit": state_payload["published_commit"],
        "last_error": state_payload["last_error"],
    }
    code = status.HTTP_200_OK if state_payload["ready"] else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(payload, status_code=code)


@app.post("/hooks/wiki-update", status_code=status.HTTP_202_ACCEPTED)
async def handle_webhook(
    request: Request,
    x_gitea_signature: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    x_gitea_event: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> JSONResponse:
    """Validate an exact push event and queue a serialized synchronization."""
    raw_body = await request.body()
    if not SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook authentication is not configured",
        )
    signature = x_gitea_signature or x_hub_signature_256 or ""
    if signature.startswith("sha256="):
        signature = signature.removeprefix("sha256=")
    if not signature:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing signature header")
    expected = hmac.new(SECRET, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    event_type = x_gitea_event or x_github_event or ""
    if event_type == "ping":
        return JSONResponse({"status": "ok", "event": "ping"}, status_code=status.HTTP_200_OK)
    if event_type != "push":
        return JSONResponse(
            {"status": "skipped", "event": event_type, "message": "Event is not a push"},
            status_code=status.HTTP_200_OK,
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be a JSON object")

    try:
        _git_url, branch, _remote = _validated_git_config()
    except SyncConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Git synchronization is not configured",
        ) from exc
    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload requires a branch ref")
    expected_ref = f"refs/heads/{branch}"
    if ref != expected_ref:
        return JSONResponse(
            {"status": "skipped", "event": "push", "ref": ref},
            status_code=status.HTTP_200_OK,
        )

    before = _read_state()["published_commit"]
    _start_daemon_sync(VAULT_DIR)
    return JSONResponse(
        {
            "status": "queued",
            "event": "push",
            "published_commit": before,
        },
        status_code=status.HTTP_202_ACCEPTED,
    )
