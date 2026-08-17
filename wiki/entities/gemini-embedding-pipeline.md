---
type: entity
title: Google Gemini Cloud Embedding Pipeline
summary: Production embedding pipeline generating 3072-dimensional semantic vectors via LiteLLM for PostgreSQL HNSW indexing.
entities: [gemini, embeddings, litellm, pgvector]
department: ai_eng
sources:
  - path: raw/architecture/model_routing_config.json
    loc: Full Source Code
    hint: LiteLLM-Enterprise-Router
  - path: raw/data/llm_inference_slo_benchmarks.csv
    loc: Rows 1-10
    hint: gemini-3.5-flash
last_compiled: 2026-08-17
---

## TL;DR
The core semantic indexing engine of the SNP Memory System, transforming multi-modal raw text into 3072-dimensional vector embeddings for HNSW search in PostgreSQL.

## Technical Specifications
- **Model Target**: Google Gemini `models/gemini-embedding-001` configured via [[model-routing-gateway]].
- **Throughput & Latency**: Generates embeddings at 32ms p99 latency per batch, providing semantic vectors for [[agentic-dual-layer-memory]].
- **Storage Target**: Indexed in `snp-postgres` using cosine distance operator `<=>` and HNSW graph parameters ($m=16, ef\_construction=64$).
- **Telemetry Integration**: Monitored in real time via [[zero-downtime-db-migration]].

## Provenance
Compiled from LiteLLM routing configuration `raw/architecture/model_routing_config.json` and latency benchmark data `raw/data/llm_inference_slo_benchmarks.csv`.

## Cross-References
- [[model-routing-gateway]]
- [[agentic-dual-layer-memory]]
- [[zero-downtime-db-migration]]
- [[indirect-injection-defense]]
