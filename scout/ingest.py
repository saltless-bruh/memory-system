"""Multi-modal ingestion engine for SNP Memory System V2.

Core ingestion logic for PostgreSQL 16 + pgvector with Anthropic Contextual Chunking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

from scout.chunker import ContextualChunker, LiteLLMBatchEmbedder
from scout.parsers import parse_file


async def get_pg_connection() -> asyncpg.Connection:
    """Creates a database connection with environment credentials."""
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    db = os.environ.get("POSTGRES_DB", "snp_rag")
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "postgres_master_secret")

    return await asyncpg.connect(
        host=host,
        port=port,
        database=db,
        user=user,
        password=password,
    )


async def ingest_document(
    file_path: Path,
    allowed_depts: list[str],
    conn: asyncpg.Connection | None = None,
    chunker: ContextualChunker | None = None,
    embedder: LiteLLMBatchEmbedder | None = None,
    base_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Parses, chunks, embeds, and ingests a single document into PostgreSQL."""
    chunker = chunker or ContextualChunker()
    embedder = embedder or LiteLLMBatchEmbedder()

    # 1. Parse document
    parsed_doc = parse_file(file_path, base_dir=base_dir)
    chunks = chunker.chunk_document(parsed_doc)

    if dry_run:
        return {
            "source_uri": parsed_doc.source_uri,
            "title": parsed_doc.title,
            "chunks_count": len(chunks),
            "allowed_depts": allowed_depts,
            "status": "dry_run_ok",
        }

    if not chunks:
        return {
            "source_uri": parsed_doc.source_uri,
            "title": parsed_doc.title,
            "chunks_count": 0,
            "status": "skipped_empty",
        }

    # 2. Batch generate dense embeddings
    texts_to_embed = [c.contextual_text for c in chunks]
    embeddings = embedder.embed_texts(texts_to_embed)
    for c, emb in zip(chunks, embeddings, strict=False):
        c.embedding = emb

    # 3. Transactional Upsert into PostgreSQL
    close_conn = False
    if conn is None:
        conn = await get_pg_connection()
        close_conn = True

    try:
        async with conn.transaction():
            # Upsert document record
            row = await conn.fetchrow(
                """
                INSERT INTO rag_documents (source_uri, allowed_depts, title, ingested_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (source_uri) DO UPDATE
                SET allowed_depts = EXCLUDED.allowed_depts,
                    title = EXCLUDED.title,
                    ingested_at = EXCLUDED.ingested_at
                RETURNING doc_id;
                """,
                parsed_doc.source_uri,
                allowed_depts,
                parsed_doc.title,
            )
            if not row:
                raise RuntimeError(f"Failed to upsert document {parsed_doc.source_uri}")

            doc_id = row["doc_id"]

            # Idempotent: delete previous chunks for this document
            await conn.execute("DELETE FROM rag_chunks WHERE doc_id = $1;", doc_id)

            # Insert all chunks
            for c in chunks:
                emb_str = (
                    f"[{','.join(str(x) for x in c.embedding)}]"
                    if c.embedding
                    else None
                )
                meta_json = json.dumps({"loc": c.loc, **c.metadata})

                await conn.execute(
                    """
                    INSERT INTO rag_chunks (
                        doc_id, chunk_index, chunk_text, context_prefix, embedding, metadata
                    ) VALUES (
                        $1, $2, $3, $4, $5::vector, $6::jsonb
                    );
                    """,
                    doc_id,
                    c.chunk_index,
                    c.chunk_text,
                    c.context_prefix,
                    emb_str,
                    meta_json,
                )

        return {
            "source_uri": parsed_doc.source_uri,
            "title": parsed_doc.title,
            "doc_id": str(doc_id),
            "chunks_count": len(chunks),
            "status": "ingested_ok",
        }
    finally:
        if close_conn:
            await conn.close()


async def reconcile_deletions(
    dir_path: Path,
    conn: asyncpg.Connection | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Finds and deletes database documents whose files no longer exist on disk."""
    close_conn = False
    if conn is None:
        conn = await get_pg_connection()
        close_conn = True

    deleted_uris: list[str] = []
    try:
        rows = await conn.fetch("SELECT doc_id, source_uri FROM rag_documents;")
        dir_path_resolved = dir_path.resolve()  # noqa: ASYNC240
        base_parent = dir_path_resolved.parent
        dir_name = dir_path_resolved.name

        # Collect existing relative URIs in this directory tree
        existing_disk_uris = {
            p.relative_to(base_parent).as_posix()
            for p in dir_path_resolved.rglob("*")  # noqa: ASYNC240
            if p.is_file()
        }

        for row in rows:
            uri = row["source_uri"]
            # Target documents scoped under this directory
            if uri.startswith(f"{dir_name}/"):
                on_disk_path = base_parent / uri
                if uri not in existing_disk_uris and not on_disk_path.exists():
                    deleted_uris.append(uri)
                    if not dry_run:
                        async with conn.transaction():
                            await conn.execute(
                                "DELETE FROM rag_documents WHERE doc_id = $1;",
                                row["doc_id"],
                            )
        return deleted_uris
    finally:
        if close_conn:
            await conn.close()


async def ingest_directory(
    dir_path: Path,
    allowed_depts: list[str],
    dry_run: bool = False,
    reconcile: bool = True,
    embedder: LiteLLMBatchEmbedder | None = None,
) -> list[dict[str, Any]]:
    """Recursively ingests all supported files in a directory and purges deleted records."""
    supported_exts = {
        ".md",
        ".txt",
        ".markdown",
        ".pdf",
        ".csv",
        ".tsv",
        ".py",
        ".sh",
        ".json",
        ".yaml",
        ".yml",
        ".sql",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
    }

    files = [
        p
        for p in dir_path.rglob("*")  # noqa: ASYNC240
        if p.is_file() and p.suffix.lower() in supported_exts
    ]

    results: list[dict[str, Any]] = []
    chunker = ContextualChunker()
    embedder = embedder or LiteLLMBatchEmbedder()

    conn = None if dry_run else await get_pg_connection()
    try:
        for file_path in files:
            res = await ingest_document(
                file_path=file_path,
                allowed_depts=allowed_depts,
                conn=conn,
                chunker=chunker,
                embedder=embedder,
                base_dir=dir_path.parent,
                dry_run=dry_run,
            )
            results.append(res)

        if reconcile and conn is not None:
            deleted = await reconcile_deletions(
                dir_path=dir_path, conn=conn, dry_run=dry_run
            )
            for d in deleted:
                results.append({"source_uri": d, "status": "purged_deleted"})
    finally:
        if conn:
            await conn.close()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SNP V2 RAG Ingestion Pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--path", type=str, help="Path to a single file to ingest")
    group.add_argument("--dir", type=str, help="Directory to recursively ingest")

    parser.add_argument(
        "--allowed-depts",
        "--dept",
        dest="allowed_depts",
        type=str,
        default="all",
        help="Comma-separated list of allowed departments (e.g. redteam,blueteam,all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk without writing to PostgreSQL",
    )

    args = parser.parse_args()
    depts = [d.strip() for d in args.allowed_depts.split(",") if d.strip()]
    if not depts:
        depts = ["all"]

    if args.path:
        target = Path(args.path)
        if not target.is_file():
            print(f"Error: File not found: {target}", file=sys.stderr)
            sys.exit(1)
        res = asyncio.run(
            ingest_document(
                file_path=target,
                allowed_depts=depts,
                base_dir=Path.cwd(),
                dry_run=args.dry_run,
            )
        )
        print(json.dumps(res, indent=2))
    elif args.dir:
        target_dir = Path(args.dir)
        if not target_dir.is_dir():
            print(f"Error: Directory not found: {target_dir}", file=sys.stderr)
            sys.exit(1)
        results = asyncio.run(
            ingest_directory(
                dir_path=target_dir,
                allowed_depts=depts,
                dry_run=args.dry_run,
            )
        )
        for r in results:
            print(
                f"[{r['status']}] {r['source_uri']} ({r.get('chunks_count', 0)} chunks)"
            )
        print(f"\nCompleted ingestion for {len(results)} files.")


if __name__ == "__main__":
    main()
