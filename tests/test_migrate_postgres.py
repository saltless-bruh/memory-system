from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.migrate_postgres import (
    MIGRATION_LOCK_ID,
    MigrationError,
    discover_migrations,
    pending_migrations,
    run_migrations,
)


def _migration(directory: Path, name: str, sql: str = "SELECT 1") -> Path:
    path = directory / name
    path.write_text(sql, encoding="utf-8")
    return path


def test_discover_migrations_is_stable_and_rejects_duplicate_versions(tmp_path: Path) -> None:
    _migration(tmp_path, "002_second.sql")
    _migration(tmp_path, "001_first.sql")
    assert [p.name for p in discover_migrations(tmp_path)] == [
        "001_first.sql",
        "002_second.sql",
    ]
    _migration(tmp_path, "002_duplicate.sql")
    with pytest.raises(MigrationError, match="duplicate"):
        discover_migrations(tmp_path)


@pytest.mark.asyncio
async def test_pending_check_does_not_create_ledger(tmp_path: Path) -> None:
    expected = _migration(tmp_path, "001_first.sql")
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    assert await pending_migrations(conn, tmp_path) == [expected]
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_migrations_locks_and_is_idempotent(tmp_path: Path) -> None:
    first = _migration(tmp_path, "001_first.sql")
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="schema_migrations")
    conn.fetch = AsyncMock(side_effect=[[], [{"version": first.name}]])
    conn.execute = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=None)
    conn.transaction.return_value = transaction

    assert await run_migrations(conn, tmp_path) == [first.name]
    assert await run_migrations(conn, tmp_path) == []
    assert conn.execute.await_args_list[0].args == (
        "SELECT pg_advisory_lock($1)",
        MIGRATION_LOCK_ID,
    )
    assert conn.execute.await_args_list[-1].args == (
        "SELECT pg_advisory_unlock($1)",
        MIGRATION_LOCK_ID,
    )


@pytest.mark.asyncio
async def test_failed_migration_is_redacted_and_unlocks(tmp_path: Path) -> None:
    _migration(tmp_path, "001_failure.sql", "SELECT synthetic_secret_value")
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="schema_migrations")
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(side_effect=[None, RuntimeError("synthetic_secret_value"), None])
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=None)
    conn.transaction.return_value = transaction

    with pytest.raises(MigrationError) as caught:
        await run_migrations(conn, tmp_path)
    assert "synthetic_secret_value" not in str(caught.value)
    assert conn.execute.await_args_list[-1].args[0] == "SELECT pg_advisory_unlock($1)"
