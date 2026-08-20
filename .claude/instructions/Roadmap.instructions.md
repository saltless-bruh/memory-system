# Project Roadmap — SNP Memory System

> **Historical roadmap.** The PostgreSQL pgvector/RLS architecture described as
> future below has been implemented. Use `docs/ARCHITECTURE_STATUS.md`,
> `README.md`, and `AGENTS.md` for current operations.

- **V1 (Current)**:
  - Phase 0: Gates & Foundation
  - Phase 1: LLM-Wiki Engine (`basic-memory` + `gen_index.py`)
  - Phase 2: RAG + Scout Bridge (`rag_fetch` + `post_filter`)
  - Phase 3: End-to-End Query Workflow & Packaging
  - Phase 4: Agent Onboarding & Production Hardening

- **V2 (Future)**:
  - Swappable RAG Engine (R2R / pgvector)
  - Row-level RBAC enforcement
