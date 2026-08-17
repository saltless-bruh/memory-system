---
type: technique
title: Zero-Downtime Telemetry Database Migration
summary: Implements schema migrations using non-blocking DDL and generated columns to record high-frequency model inference telemetry without table locks.
entities: [postgresql, migrations, telemetry, database]
department: ai_eng
sources:
  - path: raw/architecture/init_telemetry_schema.sql
    loc: Full Source Code
    hint: model_inference_telemetry
last_compiled: 2026-08-17
---

## TL;DR
High-throughput inference logging requires non-blocking DDL operations, partitioned storage, and stored generated columns to avoid query stalls during database schema updates.

## Technical Specifications
- **Schema Design**: Utilizes stored generated columns `tokens_per_sec` calculated from `completion_tokens` and `total_latency_ms`.
- **Indexing Strategy**: B-Tree indices on `(model_name, request_timestamp DESC)` ensure sub-millisecond SLO queries for [[model-routing-gateway]].
- **Integration**: Connected to [[vllm-inference-cluster]] and [[gemini-embedding-pipeline]] telemetry emitters.

## Provenance
Compiled from database schema definition `raw/architecture/init_telemetry_schema.sql`.

## Cross-References
- [[model-routing-gateway]]
- [[vllm-inference-cluster]]
- [[gemini-embedding-pipeline]]
- [[llm-outage-failover]]
