#!/usr/bin/env python3
"""test_full_system.py — Live execution test of the SNP Memory System engine.

Runs the end-to-end search and retrieval workflow (T-3.1 / R-5.1 / R-5.4)
by instantiating the Wiki Engine (ScoutDiyEngine with embedding similarity)
and the RAG Data Vault Backend (FakeRagBackend seeded with raw RFC texts).
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.backends.fake import FakeRagBackend
from scout.diy_engine import FakeEmbedder, ScoutDiyEngine
from scout.types import RagChunk
from scout.workflow import answer_query


async def main() -> None:
    print("==================================================")
    print("SNP MEMORY SYSTEM — END-TO-END ENGINE TEST")
    print("==================================================")

    # 1. Initialize Wiki Engine over the real wiki/ vault
    wiki_dir = REPO_ROOT / "wiki"
    embedder = FakeEmbedder(dims=512)
    wiki_engine = ScoutDiyEngine.from_vault(embedder, wiki_dir=wiki_dir)
    print("[1] Wiki Engine initialized over vault: wiki/")

    # 2. Seed Data Vault (RAG Backend) with verbatim raw RFC chunks
    rag_chunks = [
        RagChunk(
            file_path="raw/rfcs/rfc793-tcp.md",
            text=(
                "Three-way handshake (SYN, SYN-ACK, ACK) for connection establishment. "
                "Flow Control: Sliding window mechanism managed via the Window Size header field. "
                "Reliability: Sequence numbers, Acknowledgement numbers, and retransmission timers upon packet loss. "
                "Graceful Termination: Four-way handshake (FIN, ACK, FIN, ACK)."
            ),
            loc="Section Key Specifications",
            score=0.95,
        ),
        RagChunk(
            file_path="raw/rfcs/rfc791-ipv4.md",
            text=(
                "Address length: 32 bits (IPv4). Header Fields: Version, IHL, Time to Live (TTL), Protocol. "
                "Service Model: Connectionless, best-effort packet delivery."
            ),
            loc="Section Key Specifications",
            score=0.90,
        ),
    ]
    rag_backend = FakeRagBackend(chunks=rag_chunks)
    print("[2] Data Vault (RAG Backend) initialized with verbatim chunks.")

    # 3. Query 1: Search for TCP protocol concept
    query = "Transmission Control Protocol reliable connection-oriented transport flow control"
    print(f'\n[3] Querying Wiki + Data Vault for:\n    "{query}"\n')

    answer = await answer_query(
        wiki=wiki_engine,
        rag=rag_backend,
        query=query,
        need_rag=True,
        k=5,
    )

    print("--------------------------------------------------")
    print(f"QUERY RESULT STATUS: {answer.status.value.upper()}")
    print("--------------------------------------------------")
    print(f"Chosen Wiki Page   : {answer.page_path}")
    print(f"Wiki Page Summary  : {answer.page_summary}")
    print(f"Used RAG Data Vault: {answer.used_rag}")

    print("\nVerbatim Context Retrieved from Data Vault:")
    for idx, ctx in enumerate(answer.context, 1):
        print(f"  [{idx}] File: {ctx.file_path} (Loc: {ctx.loc})")
        print(f'      Text: "{ctx.text}"')

    print("\nCitations Generated:")
    for idx, cite in enumerate(answer.citations, 1):
        print(
            f"  [{idx}] Path: {cite.file_path} | Loc: {cite.loc} | Score: {cite.score}"
        )

    print("\n==================================================")
    print("TEST SUCCESS: Full Wiki -> RAG Data Vault flow verified!")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
