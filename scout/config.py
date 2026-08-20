"""Fail-closed runtime configuration helpers.

Database callers use distinct query, ingestion, and migration credentials.  A
password may come from a Docker secret file or an explicit environment value;
there are deliberately no credential fallbacks in source control.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DatabaseRole = Literal["query", "ingest", "migration"]


class ConfigError(ValueError):
    """Raised when required runtime configuration is absent or malformed."""


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str


_ROLE_PREFIX: dict[DatabaseRole, str] = {
    "query": "POSTGRES_QUERY",
    "ingest": "POSTGRES_INGEST",
    "migration": "POSTGRES_MIGRATION",
}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"required configuration {name} is missing or empty")
    return value


def read_secret(
    env: Mapping[str, str],
    *,
    value_name: str,
    file_name: str,
) -> str:
    """Read a secret file preferentially, with an explicit env fallback."""
    secret_file = env.get(file_name, "").strip()
    if secret_file:
        path = Path(secret_file)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"unable to read configured secret file for {file_name}") from exc
        if not value:
            raise ConfigError(f"configured secret file for {file_name} is empty")
        return value
    return _required(env, value_name)


def postgres_settings(
    role: DatabaseRole,
    env: Mapping[str, str] | None = None,
) -> PostgresSettings:
    """Load credentials for exactly one database responsibility."""
    source = os.environ if env is None else env
    prefix = _ROLE_PREFIX[role]
    try:
        port = int(source.get("POSTGRES_PORT", "5432"))
    except ValueError as exc:
        raise ConfigError("POSTGRES_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("POSTGRES_PORT must be between 1 and 65535")

    return PostgresSettings(
        host=_required(source, "POSTGRES_HOST"),
        port=port,
        database=_required(source, "POSTGRES_DB"),
        user=_required(source, f"{prefix}_USER"),
        password=read_secret(
            source,
            value_name=f"{prefix}_PASSWORD",
            file_name=f"{prefix}_PASSWORD_FILE",
        ),
    )
