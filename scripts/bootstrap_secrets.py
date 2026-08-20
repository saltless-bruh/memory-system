#!/usr/bin/env python3
"""Create missing local development secret files without overwriting values."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from collections.abc import Sequence
from pathlib import Path

SECRET_NAMES = (
    "postgres_admin_password",
    "postgres_query_password",
    "postgres_ingest_password",
    "scout_test_token",
)
SCOUT_TOKEN_MAP_NAME = "scout_static_tokens.json"


def ensure_secrets(
    directory: Path,
    *,
    rotate: bool = False,
    refresh_token_map: bool = False,
) -> list[Path]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    created: list[Path] = []
    for name in SECRET_NAMES:
        path = directory / name
        if rotate:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.chmod(path, 0o600)
        else:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))
            handle.write("\n")
        created.append(path)

    token_map = directory / SCOUT_TOKEN_MAP_NAME
    if rotate or refresh_token_map or not token_map.exists():
        token = (directory / "scout_test_token").read_text(encoding="utf-8").strip()
        replace_map = rotate or refresh_token_map
        flags = os.O_WRONLY | os.O_CREAT | (
            os.O_TRUNC if replace_map else os.O_EXCL
        )
        descriptor = os.open(token_map, flags, 0o600)
        os.chmod(token_map, 0o600)
        payload = {
            token: {
                "subject": "integration-test",
                "departments": ["infra"],
            }
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
        created.append(token_map)
    return created


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path(".secrets"))
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--rotate",
        action="store_true",
        help="replace every managed local test secret without printing values",
    )
    actions.add_argument(
        "--refresh-static-token-map",
        action="store_true",
        help="rewrite only the static-token identity map from the existing token",
    )
    args = parser.parse_args(argv)
    created = ensure_secrets(
        args.directory,
        rotate=args.rotate,
        refresh_token_map=args.refresh_static_token_map,
    )
    if args.rotate:
        action = "rotated"
    elif args.refresh_static_token_map:
        action = "refreshed"
    else:
        action = "created"
    print(f"[bootstrap] {action} {len(created)} local secret file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
