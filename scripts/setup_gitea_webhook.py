"""Automated Gitea Webhook & Repository Setup Utility.

Provisions the `snp-memory` repository and registers the HMAC-SHA256 push webhook
pointing to `http://host-sync:9000/hooks/wiki-update` using the Gitea REST API.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any, cast

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("setup-gitea")


def create_gitea_webhook(
    gitea_url: str,
    token: str,
    owner: str,
    repo: str,
    webhook_url: str,
    secret: str,
) -> dict[str, Any]:
    """Register or update a repository push webhook in Gitea via REST API."""
    clean_base = gitea_url.rstrip("/")
    api_url = f"{clean_base}/api/v1/repos/{owner}/{repo}/hooks"

    # List existing hooks to avoid duplicates
    req_list = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req_list, timeout=10) as resp:
            existing_hooks = json.load(resp)
            for hook in existing_hooks:
                config = hook.get("config", {})
                if config.get("url") == webhook_url:
                    logger.info(
                        f"Webhook already exists with ID {hook['id']}. Updating configuration..."
                    )
                    patch_url = f"{api_url}/{hook['id']}"
                    patch_payload = {
                        "config": {
                            "url": webhook_url,
                            "content_type": "json",
                            "secret": secret,
                        },
                        "events": ["push"],
                        "active": True,
                    }
                    req_patch = urllib.request.Request(
                        patch_url,
                        data=json.dumps(patch_payload).encode("utf-8"),
                        headers={
                            "Authorization": f"token {token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        method="PATCH",
                    )
                    with urllib.request.urlopen(req_patch, timeout=10) as patch_resp:
                        return cast(dict[str, Any], json.load(patch_resp))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning(
                f"Failed to query existing hooks ({e.code}): {e.read().decode('utf-8', errors='ignore')}"
            )

    # Create new webhook
    payload = {
        "type": "gitea",
        "config": {
            "url": webhook_url,
            "content_type": "json",
            "secret": secret,
        },
        "events": ["push"],
        "active": True,
    }

    req_create = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req_create, timeout=10) as resp:
        result = cast(dict[str, Any], json.load(resp))
        logger.info(
            f"Successfully registered Gitea webhook (ID: {result.get('id')}) -> {webhook_url}"
        )
        return result


def send_test_ping(target_webhook_url: str, secret: str) -> bool:
    """Send an HMAC-signed test ping webhook directly to the host-sync receiver."""
    logger.info(f"Sending test ping to {target_webhook_url}...")
    body_dict = {
        "zen": "Zero-Credential Git synchronization verified.",
        "hook_id": 1,
        "hook": {"type": "gitea"},
        "repository": {"name": "snp-memory", "full_name": "snp-admin/snp-memory"},
    }
    raw_body = json.dumps(body_dict).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        target_webhook_url,
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Signature": signature,
            "X-Gitea-Event": "ping",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp_body = json.load(resp)
            logger.info(f"Ping response (HTTP {resp.status}): {resp_body}")
            return bool(resp.status == 200 and resp_body.get("status") == "ok")
    except Exception as e:
        logger.error(f"Test ping failed: {e}")
        return False


#: Values that have appeared as placeholders and must never sign a real webhook.
#: Compared case-insensitively after stripping.
KNOWN_DEVELOPMENT_SECRETS = frozenset(
    {"dev-secret", "devsecret", "changeme", "change-me", "secret", "password", "test"}
)

#: Short enough to brute-force is the same as absent for an HMAC secret.
MIN_SECRET_LENGTH = 16


def _reject_insecure_secret(secret: str, *, development: bool) -> str | None:
    """Return an error message when `secret` must not be used, else None.

    This script previously defaulted to the literal string `dev-secret`. Compose
    already requires a real secret, so the default was not even a convenience —
    it was only a way to configure a webhook that silently accepts forged
    payloads. 2026 guidance is unambiguous that a signing secret must never be
    hardcoded or committed; a fallback default is the same failure with an extra
    step.

    Nothing here ever logs the secret itself, only what is wrong with it.
    """
    value = (secret or "").strip()
    if not value:
        return (
            "A webhook signing secret is required. Set WEBHOOK_SECRET or pass "
            "--secret. There is no default: an unsigned or guessably-signed "
            "webhook accepts forged payloads."
        )
    if value.lower() in KNOWN_DEVELOPMENT_SECRETS:
        if development:
            logger.warning(
                "Using a known-development webhook secret because --development "
                "was given. Never do this against a deployment anyone else can "
                "reach."
            )
            return None
        return (
            "That secret is a known development placeholder and is refused. "
            "Generate a real one, or pass --development to accept it knowingly."
        )
    if len(value) < MIN_SECRET_LENGTH:
        if development:
            logger.warning(
                "Webhook secret is shorter than %d characters; permitted only "
                "because --development was given.",
                MIN_SECRET_LENGTH,
            )
            return None
        return (
            f"The webhook secret is shorter than {MIN_SECRET_LENGTH} characters. "
            "A secret short enough to brute-force signs nothing."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Configure Gitea Webhook for Zero-Credential Host-Sync."
    )
    parser.add_argument(
        "--gitea-url", default=os.environ.get("GITEA_URL", "http://localhost:3000")
    )
    parser.add_argument("--token", default=os.environ.get("GITEA_TOKEN", ""))
    parser.add_argument("--owner", default=os.environ.get("GITEA_OWNER", "snp-admin"))
    parser.add_argument("--repo", default=os.environ.get("GITEA_REPO", "snp-memory"))
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get(
            "WEBHOOK_TARGET_URL", "http://host-sync:9000/hooks/wiki-update"
        ),
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("WEBHOOK_SECRET", ""),
        help="HMAC signing secret. No default: a webhook configured with a "
        "guessable secret accepts forged payloads, and the receiver cannot tell "
        "the difference.",
    )
    parser.add_argument(
        "--development",
        action="store_true",
        help="permit a known-development secret. Prints a warning; never use "
        "against a deployment anyone else can reach.",
    )
    parser.add_argument(
        "--test-ping",
        action="store_true",
        help="Send a test ping directly to the receiver",
    )

    args = parser.parse_args(argv)

    secret_error = _reject_insecure_secret(args.secret, development=args.development)
    if secret_error is not None:
        logger.error(secret_error)
        return 2

    if args.test_ping:
        ping_target = args.webhook_url
        # If running from host, map host-sync to localhost:9000
        if "host-sync:9000" in ping_target:
            ping_target = ping_target.replace("host-sync:9000", "localhost:9000")
        success = send_test_ping(ping_target, args.secret)
        return 0 if success else 1

    if not args.token:
        logger.error(
            "Gitea personal access token is required. Provide --token or set GITEA_TOKEN."
        )
        return 2

    try:
        create_gitea_webhook(
            gitea_url=args.gitea_url,
            token=args.token,
            owner=args.owner,
            repo=args.repo,
            webhook_url=args.webhook_url,
            secret=args.secret,
        )
        return 0
    except Exception as e:
        logger.error(f"Failed to configure Gitea webhook: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
