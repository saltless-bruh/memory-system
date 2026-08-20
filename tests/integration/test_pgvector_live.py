"""Integration tests for PgVectorRlsBackend querying live PostgreSQL."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from scout.backends.pgvector import PgVectorRlsBackend
from scout.chunker import LiteLLMBatchEmbedder
from scout.core import rag_fetch
from scout.ingest import get_pg_connection, ingest_document
from scout.types import Address, FetchStatus, Scope

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def setup_test_documents() -> AsyncIterator[None]:
    """Ingests isolated fixture documents for PgVector integration tests and cleans them up."""
    conn = await get_pg_connection()
    embedder = LiteLLMBatchEmbedder()

    res_tcp: dict[str, object] | None = None
    res_tls: dict[str, object] | None = None
    try:
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
                allowed_depts=["all", "infra"],
                conn=conn,
                base_dir=tmp_path,
                embedder=embedder,
            )
            res_tls = await ingest_document(
                file_path=tls_doc,
                allowed_depts=["all", "blueteam"],
                conn=conn,
                base_dir=tmp_path,
                embedder=embedder,
            )

            yield
    finally:
        source_uris = [
            result.get("source_uri")
            for result in (res_tcp, res_tls)
            if result is not None and isinstance(result.get("source_uri"), str)
        ]
        if source_uris:
            await conn.execute(
                "DELETE FROM rag_documents WHERE source_uri = ANY($1::text[]);",
                source_uris,
            )
        await conn.close()


@pytest.mark.asyncio
async def test_pgvector_hybrid_search_retrieval(
    setup_test_documents: None,
) -> None:
    embedder = LiteLLMBatchEmbedder()
    backend = PgVectorRlsBackend(embedder=embedder)

    try:
        scope = Scope(departments=frozenset({"infra"}))
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
async def test_pgvector_pre_filter_path(setup_test_documents: None) -> None:
    embedder = LiteLLMBatchEmbedder()
    backend = PgVectorRlsBackend(embedder=embedder)
    try:
        scope = Scope(departments=frozenset({"blueteam"}))
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
async def test_pgvector_rag_fetch_contract(setup_test_documents: None) -> None:
    embedder = LiteLLMBatchEmbedder()
    backend = PgVectorRlsBackend(embedder=embedder)
    try:
        scope = Scope(departments=frozenset({"infra"}))
        address = Address(
            path="rfc793-tcp.md",
            hint="TCP three-way handshake sliding window flow control",
            loc="Section 1",
        )
        result = await rag_fetch(backend, address, scope=scope)
        assert result.status == FetchStatus.OK
        assert len(result.context) > 0
        assert len(result.citations) > 0
        assert all(c.file_path == "rfc793-tcp.md" for c in result.citations)
        assert result.citations[0].loc is not None
        assert "Section" in result.citations[0].loc
    finally:
        await backend.close()
