"""Comprehensive test suite for PgVectorRlsBackend (Hybrid Search & RLS Isolation)."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio

from scout.backends.pgvector import PgVectorRlsBackend
from scout.core import rag_fetch
from scout.ingest import get_pg_connection, ingest_document
from scout.types import Address, FetchStatus, Scope


@pytest_asyncio.fixture(autouse=True)
async def setup_test_documents() -> AsyncGenerator[None, None]:
    """Ingests isolated fixture documents for PgVector tests and cleans them up."""
    try:
        conn = await get_pg_connection()
    except Exception as e:
        pytest.skip(f"Postgres not reachable: {e}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        tcp_doc = tmp_path / "rfc793-tcp.md"
        tcp_doc.write_text(
            "# RFC 793 TCP\n\nTCP three-way handshake sliding window flow control and reliable delivery.",
            encoding="utf-8",
        )
        tls_doc = tmp_path / "rfc8446-tls13.md"
        tls_doc.write_text(
            "# RFC 8446 TLS 1.3\n\nTLS 1.3 1-RTT handshake AEAD ciphers and encrypted extensions.",
            encoding="utf-8",
        )

        res_tcp = await ingest_document(
            file_path=tcp_doc,
            allowed_depts=["all", "networking"],
            conn=conn,
            base_dir=tmp_path,
        )
        res_tls = await ingest_document(
            file_path=tls_doc,
            allowed_depts=["all", "security"],
            conn=conn,
            base_dir=tmp_path,
        )

        try:
            yield
        finally:
            await conn.execute(
                "DELETE FROM rag_documents WHERE source_uri IN ($1, $2);",
                res_tcp["source_uri"],
                res_tls["source_uri"],
            )
            await conn.close()


@pytest.mark.asyncio
async def test_pgvector_hybrid_search_retrieval() -> None:
    backend = PgVectorRlsBackend()
    try:
        scope = Scope(roles=frozenset(["all", "networking"]))
        chunks = await backend.retrieve(
            hint="TCP three-way handshake sliding window flow control",
            path="rfc793-tcp.md",
            scope=scope,
            k=3,
        )
        assert len(chunks) > 0
        assert any("rfc793-tcp.md" in c.file_path for c in chunks)
        assert all(c.score > 0 for c in chunks)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_pgvector_pre_filter_path() -> None:
    backend = PgVectorRlsBackend()
    try:
        scope = Scope(roles=frozenset(["all", "security"]))
        chunks = await backend.retrieve(
            hint="TLS 1.3 1-RTT handshake AEAD ciphers",
            path="rfc8446-tls13.md",
            scope=scope,
            k=5,
        )
        assert len(chunks) > 0
        for chunk in chunks:
            assert "rfc8446-tls13.md" in chunk.file_path
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_pgvector_rls_security_fail_closed() -> None:
    backend = PgVectorRlsBackend()
    try:
        # Case A: Missing/empty scope -> Fail-Closed (0 chunks)
        empty_chunks = await backend.retrieve(
            hint="TCP three-way handshake",
            path="rfc793-tcp.md",
            scope=None,
            k=3,
        )
        assert len(empty_chunks) == 0

        # Case B: Non-matching scope -> 0 chunks
        unauthorized_scope = Scope(roles=frozenset(["unauthorized_dept"]))
        unauth_chunks = await backend.retrieve(
            hint="TCP three-way handshake",
            path="rfc793-tcp.md",
            scope=unauthorized_scope,
            k=3,
        )
        assert len(unauth_chunks) == 0

        # Case C: Authorized scope -> Successfully retrieved
        authorized_scope = Scope(roles=frozenset(["all"]))
        auth_chunks = await backend.retrieve(
            hint="TCP three-way handshake",
            path="rfc793-tcp.md",
            scope=authorized_scope,
            k=3,
        )
        assert len(auth_chunks) > 0
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_pgvector_scout_core_rag_fetch_live_address() -> None:
    backend = PgVectorRlsBackend()
    try:
        address = Address(
            path="rfc793-tcp.md",
            hint="TCP three-way handshake sliding window flow control",
            loc="Section Key Specifications",
        )
        scope = Scope(roles=frozenset(["all"]))
        result = await rag_fetch(backend, address, scope=scope, k=3)

        assert result.status == FetchStatus.OK
        assert len(result.context) > 0
        assert len(result.citations) > 0
        assert "rfc793-tcp.md" in result.citations[0].file_path
        assert "rfc793-tcp.md" in result.context[0].file_path
    finally:
        await backend.close()
