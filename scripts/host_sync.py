"""Hardened Host-Sync Webhook Receiver (Phase 1, Technical_Blueprint_Basic_Memory_Gitea.md).

Listens for Gitea push webhooks on port 9000, validates HMAC-SHA256 signatures in
constant time, and executes `git fetch && git reset --hard origin/main` asynchronously
in `/vault` to maintain the zero-credential read-replica for basic-memory (R-2.5).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
from typing import Any

from fastapi import (
    BackgroundTasks,
    FastAPI,
    Header,
    HTTPException,
    Request,
    status,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("host-sync")

app = FastAPI(title="SNP Host-Sync Service", version="2.0.0")

SECRET = os.environ.get("WEBHOOK_SECRET", "dev-secret").encode("utf-8")
VAULT_DIR = os.environ.get("VAULT_DIR", "/repo")

_sync_lock = threading.Lock()


def _ensure_safe_git_directory(vault_path: str = VAULT_DIR) -> None:
    """Configure git safe.directory to prevent container ownership mismatch errors."""
    try:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", vault_path],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", "*"],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        logger.warning(f"Failed to set git safe.directory: {e}")


# Initialize git safe directory on module load
_ensure_safe_git_directory()


def _perform_git_sync(vault_path: str = VAULT_DIR) -> None:
    """Asynchronously execute git fetch and reset --hard origin/main in vault directory."""
    with _sync_lock:
        remote = os.environ.get("GIT_REMOTE", "origin")
        branch = os.environ.get("GIT_BRANCH", "main")
        logger.info(f"Executing git sync in {vault_path} for remote '{remote}' branch '{branch}'...")
        try:
            fetch_res = subprocess.run(
                ["git", "fetch", remote],
                cwd=vault_path,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"git fetch output: {fetch_res.stdout.strip()}")

            reset_res = subprocess.run(
                ["git", "reset", "--hard", f"{remote}/{branch}"],
                cwd=vault_path,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"git reset output: {reset_res.stdout.strip()}")
            logger.info("Host-sync completed successfully. Vault read replica is up to date.")
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Git sync failed with code {e.returncode}: {e.stderr.strip() if e.stderr else e}"
            )
        except Exception as e:
            logger.error(f"Unexpected error during git sync: {e}")


@app.get("/")
@app.get("/hooks/wiki-update")
async def health_check() -> dict[str, str]:
    """Health check endpoint for browser and container probes."""
    return {"status": "online", "service": "host-sync"}


@app.post("/hooks/wiki-update")
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitea_signature: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    x_gitea_event: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, Any]:
    """Verify HMAC signature and dispatch git synchronization in background."""
    raw_body = await request.body()

    # Extract signature from Gitea or GitHub header
    signature = x_gitea_signature or x_hub_signature_256 or ""
    if signature.startswith("sha256="):
        signature = signature[7:]

    if not signature:
        logger.warning("Rejected webhook request: Missing signature header.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing signature header",
        )

    # Constant-time HMAC-SHA256 verification against raw request body
    expected_hex = hmac.new(SECRET, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_hex):
        logger.warning("Rejected webhook request: Invalid HMAC signature.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature",
        )

    event_type = x_gitea_event or x_github_event or "push"

    # Handle Ping / Test Webhooks
    if event_type == "ping":
        logger.info("Received valid ping/test webhook from Gitea.")
        return {"status": "ok", "event": "ping", "message": "Pong! Webhook verified successfully."}

    # Inspect payload for branch filtering if present
    target_branch = "main"
    should_sync = True
    try:
        if raw_body:
            payload = json.loads(raw_body.decode("utf-8"))
            ref = payload.get("ref", "")
            if ref and not (ref == f"refs/heads/{target_branch}" or ref.endswith(f"/{target_branch}")):
                logger.info(f"Skipping sync for non-target ref '{ref}' (watching '{target_branch}').")
                should_sync = False
    except Exception:
        # If payload parsing fails, proceed with sync safely if it's a push event
        should_sync = True

    if should_sync:
        logger.info("Valid push webhook received. Queuing background git sync...")
        background_tasks.add_task(_perform_git_sync, VAULT_DIR)
        return {
            "status": "success",
            "event": event_type,
            "message": "Git synchronization queued successfully.",
        }

    return {
        "status": "skipped",
        "event": event_type,
        "message": "Push event on non-target branch ignored.",
    }
