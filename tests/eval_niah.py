"""Needle-in-a-Haystack (NIAH) Benchmark for SNP Memory System V2.

Evaluates retrieval recall for buried facts across multiple depth positions
(Beginning, Middle, End) using PgVectorRlsBackend Hybrid Search.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from scout.backends.pgvector import PgVectorRlsBackend
from scout.ingest import get_pg_connection, ingest_document
from scout.types import Scope


async def run_niah_depth(depth_pct: int) -> bool:
    """Runs a NIAH test with needle placed at depth_pct (0 to 100)."""
    needle = "The secret passphrase for the V2 database is 'PurpleMonkeyDishwasher'."

    filler_sentence = (
        "The system infrastructure relies on distributed consensus protocols. "
        "Network packets are monitored using eBPF probes for latency analysis. "
        "Database connections use connection pooling to maximize concurrent query throughput. "
    )

    # Generate 40 blocks of filler
    total_blocks = 40
    needle_block = int((depth_pct / 100.0) * total_blocks)

    blocks: list[str] = []
    for i in range(total_blocks):
        if i == needle_block:
            blocks.append(
                f"\n\n### Section {i}\n{filler_sentence}\n\n{needle}\n\n{filler_sentence}\n\n"
            )
        else:
            blocks.append(f"\n\n### Section {i}\n{filler_sentence * 2}\n\n")

    content = f"# Network & Database Operations Manual\n\n{''.join(blocks)}"

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(content)
        temp_path = Path(f.name)

    backend = PgVectorRlsBackend()
    conn = await get_pg_connection()

    try:
        # Ingest document
        ingest_res = await ingest_document(
            file_path=temp_path,
            allowed_depts=["all"],
            conn=conn,
            base_dir=temp_path.parent,
        )
        doc_id = ingest_res.get("doc_id")

        # Query needle
        query = "What is the secret passphrase for the V2 database?"
        chunks = await backend.retrieve(
            hint=query,
            path=ingest_res.get("source_uri"),
            scope=Scope(roles=frozenset(["all"])),
            k=3,
        )

        found = any("PurpleMonkeyDishwasher" in c.text for c in chunks)
        return found
    finally:
        # Cleanup
        if doc_id:
            await conn.execute(
                "DELETE FROM rag_documents WHERE doc_id = $1;", uuid.UUID(doc_id)
            )
        await conn.close()
        await backend.close()
        if temp_path.exists():
            temp_path.unlink()


async def run_niah() -> bool:
    print("==================================================")
    print("   SNP V2 Needle-in-a-Haystack (NIAH) Benchmark   ")
    print("==================================================")

    depths = [10, 50, 90]
    all_passed = True

    for d in depths:
        passed = await run_niah_depth(d)
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"[{status}] Depth {d}%: Needle retrieved successfully.")
        if not passed:
            all_passed = False

    print("--------------------------------------------------")
    if all_passed:
        print("🎯 Overall NIAH Result: 100% PASS (All depths retrieved)")
    else:
        print("💥 Overall NIAH Result: FAILED")
    print("==================================================")
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_niah())
    if not success:
        raise SystemExit(1)
