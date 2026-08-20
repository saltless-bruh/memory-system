"""Hard-Negative Semantic Discrimination Benchmark for SNP Memory System V2.

Evaluates the hybrid retriever's ability to rank ground-truth facts above
adversarial semantic clones and negated hard-negatives.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from scout.backends.pgvector import PgVectorRlsBackend
from scout.chunker import LiteLLMBatchEmbedder
from scout.ingest import get_pg_connection, ingest_document
from scout.types import Scope


async def run_hard_negatives() -> bool:
    print("==================================================")
    print("  SNP V2 Hard-Negative Discrimination Benchmark   ")
    print("==================================================")

    true_text = (
        "# Protocol 99X Quantum Key Handshake Specification\n\n"
        "Protocol 99X is a quantum-resistant transport mechanism designed to provide "
        "robust byte stream delivery. Protocol 99X quantum encryption uses a precise "
        "three-way handshake mechanism involving SYN, SYN-ACK, and ACK packets."
    )

    hard_negative_text = (
        "# Adversarial Misconceptions in Quantum Transport Protocols\n\n"
        "Many engineers erroneously assume Protocol 99X quantum encryption requires "
        "four separate steps. However, standard Protocol 99X never uses a four-way handshake "
        "for opening connections, although connection teardown uses four steps (FIN/ACK)."
    )

    with tempfile.NamedTemporaryFile("w", suffix="_true.md", delete=False) as f1:
        f1.write(true_text)
        true_path = Path(f1.name)

    with tempfile.NamedTemporaryFile("w", suffix="_neg.md", delete=False) as f2:
        f2.write(hard_negative_text)
        neg_path = Path(f2.name)

    embedder = LiteLLMBatchEmbedder()
    backend = PgVectorRlsBackend(embedder=embedder)
    conn = await get_pg_connection()
    doc_ids: list[str] = []

    try:
        res1 = await ingest_document(
            file_path=true_path,
            allowed_depts=["all"],
            conn=conn,
            embedder=embedder,
        )
        if res1.get("doc_id"):
            doc_ids.append(res1["doc_id"])

        res2 = await ingest_document(
            file_path=neg_path,
            allowed_depts=["all"],
            conn=conn,
            embedder=embedder,
        )
        if res2.get("doc_id"):
            doc_ids.append(res2["doc_id"])

        query = "Protocol 99X quantum encryption three-way handshake"
        chunks = await backend.retrieve(
            hint=query,
            scope=Scope(departments=frozenset({"infra"})),
            k=5,
        )

        true_uri = res1.get("source_uri")
        neg_uri = res2.get("source_uri")

        true_rank = next(
            (i for i, c in enumerate(chunks) if c.file_path == true_uri), None
        )
        neg_rank = next(
            (i for i, c in enumerate(chunks) if c.file_path == neg_uri), None
        )

        print(f"True Doc Rank: {true_rank}, Negative Doc Rank: {neg_rank}")
        if chunks:
            print(
                f"Top Result File: {chunks[0].file_path} (Score: {chunks[0].score:.4f})"
            )
            print(f"Top Result Snippet: {chunks[0].text[:80]}...")

        if true_rank is not None and (neg_rank is None or true_rank < neg_rank):
            print("✅ PASS: Ground-truth ranked above adversarial hard negative.")
            return True
        else:
            print(
                "❌ FAIL: Hard negative outranked ground truth or ground truth not found."
            )
            return False
    finally:
        for did in doc_ids:
            await conn.execute(
                "DELETE FROM rag_documents WHERE doc_id = $1;", uuid.UUID(did)
            )
        await conn.close()
        await backend.close()
        if true_path.exists():
            true_path.unlink()
        if neg_path.exists():
            neg_path.unlink()


if __name__ == "__main__":
    success = asyncio.run(run_hard_negatives())
    if not success:
        raise SystemExit(1)
