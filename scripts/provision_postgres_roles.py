#!/usr/bin/env python3
"""Enable fixed application roles using secret-backed passwords."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from scout.config import ConfigError, postgres_settings
from scripts.migrate_postgres import get_connection

ROLE_SETTINGS = {
    "rag_app_role": "query",
    "rag_ingest_role": "ingest",
}


async def provision_roles(conn: Any) -> list[str]:
    """Provision only the two fixed roles; values are quoted server-side."""
    provisioned: list[str] = []
    for role_name, settings_name in ROLE_SETTINGS.items():
        settings = postgres_settings(settings_name)  # type: ignore[arg-type]
        if settings.user != role_name:
            raise ConfigError(f"{settings_name} database user must be {role_name}")
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
            role_name,
        )
        if not exists:
            raise ConfigError(f"database role {role_name} does not exist; run migrations first")
        statement = await conn.fetchval(
            "SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', $1::text, $2::text)",
            role_name,
            settings.password,
        )
        if not isinstance(statement, str):
            raise RuntimeError("PostgreSQL did not return a provisioning statement")
        await conn.execute(statement)
        provisioned.append(role_name)
    return provisioned


async def main_async() -> int:
    try:
        conn = await get_connection()
        try:
            roles = await provision_roles(conn)
        finally:
            await conn.close()
    except Exception as exc:
        print(f"[provision] failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"[provision] enabled {len(roles)} application role(s)")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
