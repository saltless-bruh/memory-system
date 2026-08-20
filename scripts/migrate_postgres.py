#!/usr/bin/env python3
"""Transactional, forward-only PostgreSQL migration runner."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import asyncpg

from scout.config import ConfigError, postgres_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "config" / "postgres" / "migrations"
MIGRATION_LOCK_ID = 7_319_240_026


class MigrationError(RuntimeError):
    """A redacted migration failure safe to show in logs."""


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return migrations in stable filename order and reject duplicate versions."""
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    duplicates = sorted({version for version in versions if versions.count(version) > 1})
    if duplicates:
        raise MigrationError("duplicate migration version(s) detected")
    return files


async def get_connection() -> asyncpg.Connection:
    settings = postgres_settings("migration")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
    )


async def _ledger_exists(conn: Any) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('public.schema_migrations')"))


async def _applied_versions(conn: Any, *, create: bool) -> set[str]:
    exists = await _ledger_exists(conn)
    if not exists and not create:
        return set()
    if not exists:
        await conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    records = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    return {str(record["version"]) for record in records}


async def pending_migrations(
    conn: Any,
    directory: Path = MIGRATIONS_DIR,
) -> list[Path]:
    """List pending files without mutating database state."""
    applied = await _applied_versions(conn, create=False)
    return [path for path in discover_migrations(directory) if path.name not in applied]


async def run_migrations(
    conn: Any,
    directory: Path = MIGRATIONS_DIR,
) -> list[str]:
    """Apply each pending file transactionally under a session advisory lock."""
    applied_this_run: list[str] = []
    await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
    try:
        applied = await _applied_versions(conn, create=True)
        for sql_file in discover_migrations(directory):
            if sql_file.name in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)",
                        sql_file.name,
                    )
            except Exception as exc:
                raise MigrationError(f"migration {sql_file.name} failed") from exc
            applied_this_run.append(sql_file.name)
            applied.add(sql_file.name)
        return applied_this_run
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PostgreSQL migrations")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit nonzero when migrations are pending; never mutate the database",
    )
    return parser


async def main_async(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = await get_connection()
    except (ConfigError, OSError, asyncpg.PostgresError) as exc:
        print(f"[migrate] connection/configuration failed ({type(exc).__name__})", file=sys.stderr)
        return 2

    try:
        if args.check:
            pending = await pending_migrations(conn)
            if pending:
                print(f"[migrate] {len(pending)} pending migration(s)")
                return 1
            print("[migrate] 0 pending migrations")
            return 0

        applied = await run_migrations(conn)
        print(f"[migrate] applied {len(applied)} migration(s); 0 pending")
        return 0
    except MigrationError as exc:
        print(f"[migrate] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[migrate] unexpected failure ({type(exc).__name__})", file=sys.stderr)
        return 2
    finally:
        await conn.close()


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
