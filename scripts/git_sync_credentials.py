#!/usr/bin/env python3
"""Git credential helper limited to one configured HTTP repository.

Git receives the password over the helper pipe. The remote URL, process
arguments and Git configuration contain only the credential file's path.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


def credentials(request: str, environment: Mapping[str, str]) -> dict[str, str]:
    """Return credentials only for an exact repository match, or no credentials."""
    if len(request) > 8192:
        return {}
    fields: dict[str, str] = {}
    for line in request.splitlines():
        if not line:
            break
        key, separator, value = line.partition("=")
        if not separator:
            return {}
        if key in {"protocol", "host", "path", "username"}:
            if key in fields:
                return {}
            fields[key] = value

    remote = urlsplit(environment.get("GIT_SYNC_URL", ""))
    username = environment.get("GIT_SYNC_USERNAME", "")
    if (
        remote.scheme not in {"http", "https"}
        or not remote.hostname
        or remote.username is not None
        or remote.password is not None
        or remote.query
        or remote.fragment
        or not remote.path.lstrip("/")
        or not username
        or any(char in username for char in "\r\n\0")
    ):
        raise ValueError("Invalid Git sync credential configuration")
    if (
        fields.get("protocol") != remote.scheme
        or fields.get("host", "").lower() != remote.netloc.lower()
        or fields.get("path") != remote.path.lstrip("/")
        or fields.get("username", username) != username
    ):
        return {}

    password_path = Path(environment["GIT_SYNC_PASSWORD_FILE"])
    if password_path.is_symlink() or not password_path.is_file():
        raise ValueError("Invalid Git sync credential file")
    with password_path.open("r", encoding="utf-8") as handle:
        password = handle.read(16385).rstrip("\r\n")
    if not password or len(password) > 16384 or any(c in password for c in "\r\n\0"):
        raise ValueError("Invalid Git sync credential value")
    return {"username": username, "password": password}


def main() -> int:
    # Store/erase deliberately do nothing: the mounted secret is authoritative.
    if len(sys.argv) != 2 or sys.argv[1] not in {"get", "store", "erase"}:
        return 1
    if sys.argv[1] != "get":
        return 0
    try:
        result = credentials(sys.stdin.read(8193), os.environ)
    except (KeyError, OSError, UnicodeError, ValueError):
        print("Git sync credential unavailable", file=sys.stderr)
        return 1
    for key, value in result.items():
        print(f"{key}={value}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
