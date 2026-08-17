---
type: concept
title: Speculative Decoding
summary: Accelerates autoregressive LLM inference by generating candidate tokens with a fast draft model and validating them in parallel with a target model.
entities: [speculative-decoding, latency-optimization, draft-model, inference]
department: ai_eng
sources:
  - path: raw/reports/vllm_high_throughput_serving.pdf
    loc: p.2
    hint: PagedAttention KV-Cache Virtual Block Allocation
  - path: raw/data/llm_inference_slo_benchmarks.csv
    loc: Rows 1-10
    hint: gemini-3.5-flash
last_compiled: 2026-08-17
---

## TL;DR
Speculative decoding reduces inter-token latency by 2x to 3x without changing output probability distributions by turning sequential generation into parallel verification.

## Technical Specifications
1. **Draft Generation**: A smaller, highly quantized draft model generates $\gamma$ speculative tokens sequentially in low compute time.
2. **Parallel Target Evaluation**: The large target model evaluates all $\gamma + 1$ token positions in a single forward pass.
3. **Modified Rejection Sampling**: Tokens are accepted or rejected based on target/draft probability ratios, ensuring identical output fidelity.
4. **Hardware Synergy**: Leverages [[paged-attention-engine]] for unified KV-cache tracking on [[vllm-inference-cluster]].

## Provenance
Compiled from inference benchmark telemetry in `raw/data/llm_inference_slo_benchmarks.csv` and report `raw/reports/vllm_high_throughput_serving.pdf`.

## Cross-References
- [[paged-attention-engine]]
- [[vllm-inference-cluster]]
- [[model-routing-gateway]]
- [[llm-outage-failover]]
