---
title: "RFC-2026-08: Hierarchical Dual-Layer Agentic Memory Architecture"
department: "ai_eng,all"
status: "APPROVED"
author: "Platform AI Architecture Working Group"
date: "2026-08-17"
---

# Introduction
This RFC defines the architectural blueprint for the SNP Hierarchical Dual-Layer Agentic Memory System. Modern autonomous AI agents operate under severe context window cost constraints and latency degradation when forced to maintain monolithic conversation histories. This architecture decouples working short-term context from long-term verbatim knowledge retrieval.

## System Architecture Overview
The system is partitioned into two distinct physical and logical layers:

1. **The Knowledge Vault (Layer 1 — The Map)**:
   - Implemented via a curated Git-backed Markdown wiki and SQLite-based basic-memory graph.
   - Houses high-density, concise entity descriptions, system playbooks, and architectural concepts.
   - Utilizes bidirectional `[[wikilinks]]` for deterministic graph traversal and relation resolution.
   - Enforces strict token budgets: Top-K navigation payload is capped at under 2,500 tokens.

2. **The Raw Evidence Warehouse (Layer 2 — The Warehouse)**:
   - Implemented via PostgreSQL 16 with pgvector and Scout RAG middleware.
   - Stores raw multi-modal source documents (PDF whitepapers, CSV datasets, codebases, and audio/image assets).
   - Chunks documents with contextual header injection (`[Document: Title | Source: URI | Loc: P.N]`).
   - Executes SQL-native Hybrid Search combining dense HNSW cosine similarity (Google Gemini 3072-dim embeddings) and sparse GIN full-text search (`tsvector`), fused via Reciprocal Rank Fusion (RRF).

## Retrieval and Citation Protocol
Agents interact with memory through a disciplined query lifecycle:
- Step 1: Query basic-memory `search_notes(query)` to identify relevant wiki nodes.
- Step 2: Read the compiled wiki page body and its `sources[]` address table.
- Step 3: If the compiled note answers the query, stop and cite the note.
- Step 4: If verbatim source quotes or raw tables are needed, pass the address (`path`, `loc`, `hint`) to `Scout.rag_fetch`.
- Step 5: Scout retrieves the exact text chunk from PostgreSQL with zero vault hallucinations.

## Security and Department Governance
All documents in PostgreSQL are protected by Row-Level Security (RLS) policies. When an agent requests context, Scout opens a transaction and executes `SELECT set_config('scout.current_depts', $1, true)`. Unprivileged roles cannot view or retrieve chunks from restricted departments.
