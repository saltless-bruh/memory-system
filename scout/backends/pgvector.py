"""PostgreSQL 16 + pgvector & RLS Backend for Scout (V2 Production Adapter).

Implements `RagBackend` protocol with:
  1. Fail-closed Row-Level Security (RLS) via transaction-scoped `set_config('scout.current_depts', ...)`.
  2. SQL-native Hybrid Search: Dense Vector (HNSW cosine) + Sparse Keyword (GIN tsvector)
     fused via Reciprocal Rank Fusion (RRF).
  3. Pre-filtering by source file path and precise metadata extraction.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import asyncpg

from scout.chunker import AsyncEmbedder, LiteLLMBatchEmbedder
from scout.config import postgres_settings
from scout.types import RagBackend, RagChunk, Scope


class PgVectorRlsBackend(RagBackend):
    """Production V2 RAG backend querying PostgreSQL 16 + pgvector with RLS."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
        embedder: AsyncEmbedder | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.embedder = embedder or LiteLLMBatchEmbedder()
        self._pool = pool

    async def _get_pool(self) -> asyncpg.Pool:
        """Lazily creates and returns the connection pool."""
        if self._pool is None:
            settings = postgres_settings("query")
            self._pool = await asyncpg.create_pool(
                host=self.host or settings.host,
                port=self.port or settings.port,
                database=self.database or settings.database,
                user=self.user or settings.user,
                password=self.password or settings.password,
                min_size=2,
                max_size=20,
            )
        return self._pool

    async def close(self) -> None:
        """Closes the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _resolve_depts(self, scope: Scope | None) -> str:
        """Extracts and sanitizes allowed department string from caller Scope."""
        if scope is None:
            return ""
        return ",".join(sorted(scope.departments))

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        """Retrieves verbatim passages using Hybrid RRF Search enforced by Postgres RLS."""
        if not hint or not hint.strip():
            return ()

        # 1. Generate query embedding for dense search
        # Production retrieval uses the gateway's native async transport so a
        # slow embedding request cannot stall unrelated FastMCP requests.
        embeddings = await self.embedder.aembed_texts([hint])
        if not embeddings:
            return ()
        query_vec = embeddings[0]
        emb_str = f"[{','.join(str(x) for x in query_vec)}]"

        # 2. Extract department clearance string
        depts_str = self._resolve_depts(scope)

        # 3. Execute Hybrid SQL Query inside transaction with session clearance
        pool = await self._get_pool()
        async with (
            pool.acquire() as conn,
            conn.transaction(),
        ):
            # Set transaction-local clearance setting
            await conn.execute(
                "SELECT set_config('scout.current_depts', $1, true);",
                depts_str,
            )

            candidate_k = max(20, k * 2)

            query = """
            WITH vector_matches AS (
                SELECT c.chunk_id, c.doc_id, c.chunk_text, c.metadata, d.source_uri,
                       ROW_NUMBER() OVER (ORDER BY c.embedding <=> $1::vector) AS v_rank
                FROM rag_chunks c
                JOIN rag_documents d ON d.doc_id = c.doc_id
                WHERE ($2::text IS NULL OR d.source_uri = $2::text)
                ORDER BY c.embedding <=> $1::vector
                LIMIT $3
            ),
            text_matches AS (
                SELECT c.chunk_id, c.doc_id, c.chunk_text, c.metadata, d.source_uri,
                       ROW_NUMBER() OVER (ORDER BY ts_rank(c.tsv, plainto_tsquery('english', $4)) DESC) AS t_rank
                FROM rag_chunks c
                JOIN rag_documents d ON d.doc_id = c.doc_id
                WHERE c.tsv @@ plainto_tsquery('english', $4)
                  AND ($2::text IS NULL OR d.source_uri = $2::text)
                ORDER BY ts_rank(c.tsv, plainto_tsquery('english', $4)) DESC
                LIMIT $3
            ),
            combined AS (
                SELECT COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
                       COALESCE(v.chunk_text, t.chunk_text) AS chunk_text,
                       COALESCE(v.metadata, t.metadata) AS metadata,
                       COALESCE(v.source_uri, t.source_uri) AS source_uri,
                       (COALESCE(1.0 / (60 + v.v_rank), 0.0) + COALESCE(1.0 / (60 + t.t_rank), 0.0)) AS rrf_score
                FROM vector_matches v
                FULL OUTER JOIN text_matches t ON v.chunk_id = t.chunk_id
            )
            SELECT chunk_id, chunk_text, metadata, source_uri, rrf_score
            FROM combined
            ORDER BY rrf_score DESC
            LIMIT $5;
            """

            rows = await conn.fetch(
                query,
                emb_str,
                path,
                candidate_k,
                hint,
                k,
            )

        chunks: list[RagChunk] = []
        for row in rows:
            meta_val = row["metadata"]
            if isinstance(meta_val, str):
                try:
                    meta_dict = json.loads(meta_val)
                except Exception:
                    meta_dict = {}
            elif isinstance(meta_val, dict):
                meta_dict = meta_val
            else:
                meta_dict = {}

            loc = meta_dict.get("loc")
            chunks.append(
                RagChunk(
                    text=row["chunk_text"],
                    file_path=row["source_uri"],
                    score=float(row["rrf_score"]),
                    loc=str(loc) if loc else None,
                    meta={k: str(v) for k, v in meta_dict.items()},
                )
            )

        return chunks
