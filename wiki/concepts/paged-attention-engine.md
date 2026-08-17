---
type: concept
title: PagedAttention Engine
summary: Allocates non-contiguous physical GPU VRAM blocks for KV-caches to eliminate memory fragmentation in high-throughput LLM serving.
entities: [paged-attention, vllm, kv-cache, memory-management]
department: ai_eng
sources:
  - path: raw/reports/vllm_high_throughput_serving.pdf
    loc: p.2
    hint: PagedAttention KV-Cache Virtual Block Allocation
  - path: raw/code/paged_kv_cache.py
    loc: Full Source Code
    hint: PagedKVCacheManager
last_compiled: 2026-08-17
---

## TL;DR
PagedAttention adapts OS virtual memory paging to LLM key-value caching, enabling dynamic block allocation and zero-waste prefix sharing across concurrent requests.

## Technical Specifications
Traditional KV-caches allocate contiguous VRAM tensors proportional to maximum context length, wasting up to 60% of GPU memory. PagedAttention segments KV-caches into fixed-size physical blocks (e.g. 16 tokens per block).
- **Logical to Physical Mapping**: Maintained by a per-sequence Block Table.
- **Prefix Sharing**: Shared system prompts reference identical physical block IDs with incremented reference counters, reducing prompt evaluation overhead in [[speculative-decoding]].
- **Deployment**: Integrated into [[vllm-inference-cluster]] running [[tensor-parallel-serving]].

## Provenance
Compiled from vLLM Serving Technical Report `raw/reports/vllm_high_throughput_serving.pdf` and Python reference implementation `raw/code/paged_kv_cache.py`.

## Cross-References
- [[agentic-dual-layer-memory]]
- [[speculative-decoding]]
- [[vllm-inference-cluster]]
- [[tensor-parallel-serving]]
