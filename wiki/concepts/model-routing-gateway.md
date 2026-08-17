---
type: concept
title: Model Routing Gateway
summary: Unified enterprise LLM proxy that handles multi-provider load balancing, fallbacks, semantic caching, and token budget management.
entities: [litellm, gateway, proxy, load-balancing]
department: ai_eng
sources:
  - path: raw/architecture/model_routing_config.json
    loc: Full Source Code
    hint: LiteLLM-Enterprise-Router
  - path: raw/images/inference_dashboard.png
    loc: Image Asset
    hint: inference_dashboard
last_compiled: 2026-08-17
---

## TL;DR
The routing gateway sits between client agents and LLM backends to guarantee 99.99% uptime via automatic model failover, rate limiting, and embedding standardization.

## Technical Specifications
- **Proxy Engine**: Powered by LiteLLM on port 4000, abstracting Google Gemini, Anthropic, and local [[vllm-inference-cluster]] instances.
- **Embedding Standardization**: Standardizes on 3072-dimensional Gemini embeddings routed through [[gemini-embedding-pipeline]].
- **Operational Failover**: Automatically triggers [[llm-outage-failover]] upon upstream HTTP 429 or 503 errors.
- **Telemetry**: Tracks per-request tokens, TTFT, and latency metrics ingested into PostgreSQL via [[zero-downtime-db-migration]].

## Provenance
Compiled from router configuration manifest `raw/architecture/model_routing_config.json` and dashboard visual asset `raw/images/inference_dashboard.png`.

## Cross-References
- [[agentic-dual-layer-memory]]
- [[gemini-embedding-pipeline]]
- [[llm-outage-failover]]
- [[vllm-inference-cluster]]
