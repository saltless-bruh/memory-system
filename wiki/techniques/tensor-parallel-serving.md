---
type: technique
title: Tensor Parallel LLM Serving
summary: Shards transformer attention heads across multiple GPUs via Megatron-LM tensor parallelism to enable sub-500ms time-to-first-token on 70B+ parameter models.
entities: [tensor-parallelism, gpu, vllm, distributed-inference]
department: ai_eng
sources:
  - path: raw/reports/vllm_high_throughput_serving.pdf
    loc: p.2
    hint: PagedAttention KV-Cache Virtual Block Allocation
  - path: raw/runbooks/deploy_vllm_cluster.sh
    loc: Full Source Code
    hint: deploy_vllm_cluster
last_compiled: 2026-08-17
---

## TL;DR
Tensor Parallelism partitions matrix multiplications across $N$ GPUs within a single node using high-speed NVLink interconnects, reducing per-GPU memory consumption and latency.

## Technical Specifications
- **Column & Row Parallelism**: Linear projection matrices in multi-head attention and feed-forward networks (FFN) are split into $N$ slices with All-Reduce communication barriers.
- **Cluster Deployment**: Configured with `--tensor-parallel-size 4` on [[vllm-inference-cluster]].
- **Operational Execution**: Managed via [[paged-attention-engine]] and automated using `deploy_vllm_cluster.sh` in [[llm-outage-failover]].

## Provenance
Compiled from vLLM production deployment script `raw/runbooks/deploy_vllm_cluster.sh` and technical specifications in `raw/reports/vllm_high_throughput_serving.pdf`.

## Cross-References
- [[paged-attention-engine]]
- [[vllm-inference-cluster]]
- [[llm-outage-failover]]
- [[speculative-decoding]]
