---
type: entity
title: Production vLLM GPU Inference Cluster
summary: Kubernetes-hosted multi-node GPU inference cluster serving Llama 3.3 70B with Tensor Parallelism 4 and PagedAttention memory management.
entities: [vllm, kubernetes, gpu-cluster, llama-3]
department: ai_eng
sources:
  - path: raw/architecture/k8s_vllm_deployment.yaml
    loc: Full Source Code
    hint: vllm-inference-cluster
  - path: raw/reports/vllm_high_throughput_serving.pdf
    loc: p.2
    hint: PagedAttention KV-Cache Virtual Block Allocation
last_compiled: 2026-08-17
---

## TL;DR
The primary self-hosted inference cluster deployed across 4 nodes equipped with NVIDIA A100/H100 GPUs, executing high-throughput LLM workloads.

## Technical Specifications
- **Cluster Deployment**: Defined in `raw/architecture/k8s_vllm_deployment.yaml` with 4 worker replicas.
- **Engine Capabilities**: Powered by [[paged-attention-engine]] and [[tensor-parallel-serving]] to achieve p99 latency under 500ms.
- **Failover Role**: Acts as the secondary backup provider in [[llm-outage-failover]] behind [[model-routing-gateway]].

## Provenance
Compiled from Kubernetes manifest `raw/architecture/k8s_vllm_deployment.yaml` and vLLM Serving Technical Report `raw/reports/vllm_high_throughput_serving.pdf`.

## Cross-References
- [[paged-attention-engine]]
- [[tensor-parallel-serving]]
- [[model-routing-gateway]]
- [[llm-outage-failover]]
