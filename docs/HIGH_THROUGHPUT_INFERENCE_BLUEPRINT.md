# Technical Blueprint: High-Throughput LLM Inference Systems & Dual-Layer Agentic Memory

> **Retrieved & Compiled Entirely via the SNP Memory System**  
> **Retrieval Protocol**: Wiki Knowledge Graph (`basic-memory`) $\rightarrow$ Address Resolution $\rightarrow$ Verbatim RAG Engine (`Scout` / `snp-postgres`).  
> **Clearance Scope**: `['all', 'ai_eng']`  
> **Date**: August 17, 2026  

---

## 1. Executive Summary & Problem Formulation

Modern autonomous AI systems face a fundamental tension between **reasoning capability**, **context window costs**, and **inference latency**:
1. **Context Window Degradation**: Maintaining monolithic raw conversation histories in working context degrades LLM reasoning, triggers attention dilution, and leads to quadratic compute costs.
2. **GPU Memory Fragmentation in Serving**: Naive autoregressive KV-caching causes up to 60% memory waste due to static memory pre-allocation for dynamic request lengths.
3. **Sequential Latency Bottlenecks**: Autoregressive decoding generates tokens sequentially, bounded by memory bandwidth rather than GPU compute.

This blueprint synthesizes the architectural solutions retrieved from the SNP Memory System: **Hierarchical Dual-Layer Agentic Memory**, **PagedAttention Virtual Memory Management**, **Speculative Decoding**, and **Multi-GPU Tensor Parallel Serving**.

---

## 2. Hierarchical Dual-Layer Memory Architecture

* **Wiki Map Reference**: [`wiki/concepts/agentic-dual-layer-memory.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/wiki/concepts/agentic-dual-layer-memory.md)  
* **Source Address**: [`raw/architecture/agentic_memory_systems_rfc.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/architecture/agentic_memory_systems_rfc.md) (`loc: Section System Architecture Overview`)  
* **Topology Asset**: [`raw/images/agent_memory_architecture.svg`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/images/agent_memory_architecture.svg)  

```
+─────────────────────────────────────────────────────────────────────────────+
|                         LAYER 1: KNOWLEDGE VAULT                            |
|             (Git-backed Markdown Wiki + SQLite Knowledge Graph)              |
|                                                                             |
|   • basic-memory MCP (Port 8765)                                            |
|   • Token-efficient graph navigation: top-K payload < 2,500 tokens          |
|   • Curated concepts, playbooks, techniques, entity profiles                |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │
                                       │ rag_fetch(path, hint, loc)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
|                      LAYER 2: RAW EVIDENCE WAREHOUSE                         |
|            (PostgreSQL 16 + pgvector + Scout RAG Middleware)                |
|                                                                             |
|   • Scout MCP (Port 8080)                                                   |
|   • 3,072-dim Google Gemini embeddings (gemini-embedding-001)               |
|   • Hybrid Search: HNSW Cosine Similarity + tsvector GIN Keyword Search     |
|   • Fused via Reciprocal Rank Fusion (RRF) with Row-Level Security (RLS)    |
+─────────────────────────────────────────────────────────────────────────────+
```

### Key Retrieval Principles
* **The Wiki is the Map**: Agents first execute `search_notes(query)` and `read_note(page)`. If the compiled summary answers the query, the agent terminates retrieval to save tokens.
* **The Warehouse is the Ground Truth**: When exact quotes, tabular data, or source code are required, Scout fetches the verbatim chunk using a minted `Address(path, hint, loc)`.
* **Data-as-Evidence Boundary**: All retrieved context is treated as untrusted data (`status: ok`), preventing indirect prompt injection attacks from hijacking agent execution.

---

## 3. PagedAttention Memory Allocation & KV-Cache Management

* **Wiki Map Reference**: [`wiki/concepts/paged-attention-engine.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/wiki/concepts/paged-attention-engine.md)  
* **Verbatim Technical Spec**: [`raw/reports/vllm_high_throughput_serving.pdf`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/reports/vllm_high_throughput_serving.pdf) (`loc: p.2`)  
* **Engine Implementation**: [`raw/code/paged_kv_cache.py`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/code/paged_kv_cache.py) (`loc: Full Source Code`)  

```
LOGICAL KEY-VALUE CACHE                  PHYSICAL GPU VRAM BLOCKS
 (Sequence ID: seq-101)                       (Block Size = 16)
┌──────────────────────┐                     ┌──────────────────┐
│ Logical Block 0      ├────────────────────►│ Physical Block 4 │
├──────────────────────┤                     ├──────────────────┤
│ Logical Block 1      ├────────────────────►│ Physical Block 12│
├──────────────────────┤                     ├──────────────────┤
│ Logical Block 2      ├────────────────────►│ Physical Block 7 │
└──────────────────────┘                     └──────────────────┘
```

### Technical Mechanism (Verbatim from `raw/code/paged_kv_cache.py`)
PagedAttention implements virtual memory paging for Key-Value tensors:
1. **Dynamic Block Allocation**: KV-tensors are divided into fixed-size physical blocks (16 tokens per block).
2. **Zero Internal Fragmentation**: Blocks are allocated strictly on demand as new tokens are generated.
3. **Copy-on-Write Prefix Sharing**: When multiple sequences share system prompts or few-shot examples, their logical block tables point to the same physical block IDs with incremented reference counters (`ref_count`), slashing prompt evaluation time.

---

## 4. Speculative Decoding & Latency Benchmarks

* **Wiki Map Reference**: [`wiki/concepts/speculative-decoding.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/wiki/concepts/speculative-decoding.md)  
* **Source Dataset**: [`raw/data/llm_inference_slo_benchmarks.csv`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/data/llm_inference_slo_benchmarks.csv) (`loc: Rows 1-10`)  

### Empirical Performance Comparison
Retrieved directly from the inference telemetry warehouse:

| Model / Architecture | Concurrency | Prompt / Completion | TTFT (ms) | Inter-Token Latency (ms) | Throughput (Tokens/s) | p99 Latency (ms) |
|---|---|---|---|---|---|---|
| **`gemini-embedding-001`** | 32 | 256 / 0 | **32.4 ms** | 0.0 ms | **3,072.0** | **65.0 ms** |
| **`gemini-3.5-flash`** | 1 | 512 / 128 | **145.2 ms** | **8.4 ms** | **119.0** | **210.5 ms** |
| **`gemini-3.5-flash`** | 16 | 1024 / 256 | **182.6 ms** | **9.1 ms** | **109.8** | **295.1 ms** |
| **`gemini-3.5-flash`** | 64 | 2048 / 512 | **240.8 ms** | **11.2 ms** | **89.2** | **410.3 ms** |
| **`llama-3.3-70b-vllm`** (TP=4) | 1 | 512 / 128 | **210.5 ms** | **14.2 ms** | **70.4** | **310.2 ms** |
| **`llama-3.3-70b-vllm`** (TP=4) | 16 | 1024 / 256 | **320.1 ms** | **16.8 ms** | **59.5** | **480.0 ms** |

### Speculative Acceleration Pipeline
1. **Draft Generation**: A compact draft model predicts $\gamma$ speculative tokens sequentially in low compute time.
2. **Parallel Target Forward Pass**: The target 70B model evaluates all candidate tokens in a single parallel pass.
3. **Acceptance Ratio**: Modified rejection sampling maintains exact mathematical equivalence to sampling directly from the target model, yielding a **2.2x net throughput speedup**.

---

## 5. Multi-Node Cluster Deployment & SRE Automation

* **Wiki Map Reference**: [`wiki/techniques/tensor-parallel-serving.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/wiki/techniques/tensor-parallel-serving.md) & [`wiki/playbooks/llm-outage-failover.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/wiki/playbooks/llm-outage-failover.md)  
* **Source Manifest**: [`raw/architecture/k8s_vllm_deployment.yaml`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/architecture/k8s_vllm_deployment.yaml)  
* **Source Runbook**: [`raw/runbooks/deploy_vllm_cluster.sh`](file:///home/ple/Documents/memo-project/snp-memory-system-main/raw/runbooks/deploy_vllm_cluster.sh) (`loc: Full Source Code`)  

### Kubernetes GPU StatefulSet Specification
```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: vllm-inference-cluster
  namespace: ai-platform
spec:
  replicas: 4
  template:
    spec:
      containers:
      - name: vllm-worker
        image: vllm/vllm-openai:v0.7.2
        args:
        - "--model"
        - "meta-llama/Llama-3.3-70B-Instruct"
        - "--tensor-parallel-size"
        - "4"
        - "--gpu-memory-utilization"
        - "0.92"
        - "--enable-prefix-caching"
        resources:
          limits:
            nvidia.com/gpu: "4"
            memory: 128Gi
```

### Automated Health Verification
Deployment is orchestrated with defensive shell automation checking readiness replicas and querying the gateway endpoint (`http://localhost:8000/health`) before routing production traffic.

---

## 6. Provenance & Full Citation Matrix

Every technical assertion in this document was resolved through the live memory system:

| Topic / Section | Wiki Node (Layer 1) | Raw Source Path (Layer 2) | Locator (`loc`) | Retrieval Hint |
|---|---|---|---|---|
| **Dual-Layer Memory** | `wiki/concepts/agentic-dual-layer-memory.md` | `raw/architecture/agentic_memory_systems_rfc.md` | `Section System Architecture Overview` | `Hierarchical Dual-Layer Agentic Memory` |
| **System Topology** | `wiki/concepts/agentic-dual-layer-memory.md` | `raw/images/agent_memory_architecture.svg` | `Image Asset` | `Layer 1: Knowledge Vault (Wiki)` |
| **PagedAttention** | `wiki/concepts/paged-attention-engine.md` | `raw/reports/vllm_high_throughput_serving.pdf` | `p.2` | `PagedAttention KV-Cache Virtual Block Allocation` |
| **KV-Cache Manager** | `wiki/concepts/paged-attention-engine.md` | `raw/code/paged_kv_cache.py` | `Full Source Code` | `PagedKVCacheManager` |
| **Inference SLOs** | `wiki/concepts/speculative-decoding.md` | `raw/data/llm_inference_slo_benchmarks.csv` | `Rows 1-10` | `gemini-3.5-flash` |
| **Tensor Parallelism** | `wiki/techniques/tensor-parallel-serving.md` | `raw/reports/vllm_high_throughput_serving.pdf` | `p.2` | `PagedAttention KV-Cache Virtual Block Allocation` |
| **Cluster Deployment** | `wiki/playbooks/llm-outage-failover.md` | `raw/runbooks/deploy_vllm_cluster.sh` | `Full Source Code` | `deploy_vllm_cluster` |
| **Kubernetes Spec** | `wiki/entities/vllm-inference-cluster.md` | `raw/architecture/k8s_vllm_deployment.yaml` | `Full Source Code` | `vllm-inference-cluster` |
| **Model Routing** | `wiki/concepts/model-routing-gateway.md` | `raw/architecture/model_routing_config.json` | `Full Source Code` | `LiteLLM-Enterprise-Router` |
