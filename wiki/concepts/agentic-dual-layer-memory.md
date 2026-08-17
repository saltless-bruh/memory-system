---
type: concept
title: Agentic Dual-Layer Memory Architecture
summary: Decouples high-density semantic navigation maps in Git/SQLite from deep verbatim multi-modal evidence warehouses in PostgreSQL pgvector.
entities: [agentic-memory, basic-memory, scout, pgvector]
department: ai_eng
sources:
  - path: raw/architecture/agentic_memory_systems_rfc.md
    loc: Section System Architecture Overview
    hint: Hierarchical Dual-Layer Agentic Memory
  - path: raw/images/agent_memory_architecture.svg
    loc: Image Asset
    hint: "Layer 1: Knowledge Vault (Wiki)"
last_compiled: 2026-08-17
---

## TL;DR
The SNP Memory System splits cognitive memory into a lightweight working-context knowledge graph and a high-scale vector warehouse to prevent context bloat and eliminate LLM hallucinations.

## Technical Specifications
Modern autonomous agents degrade in reasoning capability when monolithic conversation buffers exceed working context limits. The dual-layer architecture resolves this by establishing:
1. **Layer 1 (The Map / Knowledge Vault)**: A Git-backed Markdown wiki indexed by `basic-memory` into a local SQLite graph. Navigated using [[model-routing-gateway]] and structured via [[paged-attention-engine]].
2. **Layer 2 (The Warehouse / Evidence Store)**: PostgreSQL 16 with `pgvector` managed by Scout. Performs hybrid dense cosine and sparse full-text search fused via Reciprocal Rank Fusion (RRF).

Security governance is enforced using Row-Level Security (RLS) policies to ensure strict departmental isolation as described in [[indirect-injection-defense]].

## Provenance
Compiled from the foundational architecture specification in `raw/architecture/agentic_memory_systems_rfc.md` and topology diagram `raw/images/agent_memory_architecture.svg`.

## Cross-References
- [[model-routing-gateway]]
- [[paged-attention-engine]]
- [[indirect-injection-defense]]
- [[gemini-embedding-pipeline]]
