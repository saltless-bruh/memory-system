"""Disposable-database migration coverage for fresh and corrective paths."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from scout.config import postgres_settings
from scripts.migrate_postgres import run_migrations
from scripts.provision_postgres_roles import provision_roles

pytestmark = pytest.mark.integration


async def _connect(database: str | None = None) -> asyncpg.Connection:
    settings = postgres_settings("migration")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=database or settings.database,
    )


async def _database_statement(conn: asyncpg.Connection, action: str, name: str) -> str:
    assert action in {"CREATE DATABASE", "DROP DATABASE"}
    statement = await conn.fetchval(
        "SELECT format($1::text || ' %I', $2::text)", action, name
    )
    assert isinstance(statement, str)
    return statement


@pytest.fixture
async def disposable_database() -> AsyncIterator[str]:
    if os.environ.get("SNP_INTEGRATION_PROJECT") != "snp-memory-it":
        pytest.fail("migration integration tests require SNP_INTEGRATION_PROJECT=snp-memory-it")
    name = f"snp_it_{uuid.uuid4().hex}"
    admin = await _connect()
    await admin.execute(await _database_statement(admin, "CREATE DATABASE", name))
    try:
        yield name
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(await _database_statement(admin, "DROP DATABASE", name))
        await provision_roles(admin)
        await admin.close()


@pytest.mark.asyncio
async def test_fresh_migrations_are_complete_and_idempotent(
    disposable_database: str,
) -> None:
    conn = await _connect(disposable_database)
    try:
        assert await run_migrations(conn) == [
            "001_initial_schema.sql",
            "002_rls_and_roles.sql",
            "003_ingest_role_rls.sql",
        ]
        assert await run_migrations(conn) == []
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 3
        policies = await conn.fetch(
            "SELECT tablename, policyname, roles FROM pg_policies "
            "WHERE tablename = ANY($1::text[])",
            ["rag_documents", "rag_chunks"],
        )
        names = {row["policyname"] for row in policies}
        assert names == {
            "doc_dept_overlap_select",
            "chunk_dept_overlap_select",
            "ingest_all_documents",
            "ingest_all_chunks",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_forward_migration_repairs_database_with_draft_002_recorded(
    disposable_database: str,
) -> None:
    migrations = Path(__file__).resolve().parents[2] / "config" / "postgres" / "migrations"
    conn = await _connect(disposable_database)
    try:
        await conn.execute((migrations / "001_initial_schema.sql").read_text(encoding="utf-8"))
        await conn.execute((migrations / "002_rls_and_roles.sql").read_text(encoding="utf-8"))
        await conn.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        await conn.executemany(
            "INSERT INTO schema_migrations(version) VALUES ($1)",
            [("001_initial_schema.sql",), ("002_rls_and_roles.sql",)],
        )
        assert await run_migrations(conn) == ["003_ingest_role_rls.sql"]
        ingest_policies = await conn.fetchval(
            "SELECT count(*) FROM pg_policies WHERE policyname LIKE 'ingest_all_%'"
        )
        assert ingest_policies == 2
    finally:
        await conn.close()
