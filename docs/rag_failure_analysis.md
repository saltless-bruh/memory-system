# RAG Engine (V1) Evaluation & Failure Analysis Report

## Executive Summary
This document serves as an architectural post-mortem and evaluation report for the SNP Memory System V1. The objective was to evaluate the system against modern (2026) RAG stress tests: **Needle-in-a-Haystack (NIAH)**, **Hard-Negative Mining**, and **Automated Framework Metrics (Ragas)**.

**Conclusion:** Both the fallback Wiki engine (`ScoutDiyEngine`) and the production Knowledge Graph engine (`RAG-Anything`) definitively **FAILED** the deep-text retrieval stress tests. This mathematically proves the V1 retrieval architecture is inadequate for complex, high-noise data queries and strictly justifies the migration to the V2 Postgres + pgvector RAG Engine.

---

## 1. Test Methodology & System Architecture

The evaluation utilized three independent test scripts designed to target the internal `RAG-Anything` API endpoint (`http://rag:8000`). To comply with the zero-trust network boundary (Rule R-4.2), the tests were orchestrated from the host machine using a `DockerRag` shim that proxies Python's `urllib.request` over `docker compose exec`. 

Documents were dropped directly into the `raw/` directory, triggering the `snp-sync-job` to auto-ingest the data into the RAG container's LightRAG-powered Knowledge Graph.

The test scripts are located at:
*   `tests/eval_niah.py`
*   `tests/eval_hard_negatives.py`
*   `tests/eval_ragas.py`

---

## 2. Failure Analysis: Needle-in-a-Haystack (NIAH)

**The Test:** A secret phrase (`"PurpleMonkeyDishwasher"`) was buried in the middle of 100 sentences of generic networking filler text (`tests/eval_niah.py`).

**The Result:** ❌ **FAILED**
`RAG-Anything` was unable to retrieve the chunk containing the needle when queried.

**Root Cause:**
1.  **Wiki Fallback Flaw:** The `ScoutDiyEngine` only embeds the `summary` frontmatter, completely ignoring the `body`. 
2.  **RAG-Anything Flaw:** The LightRAG chunking strategy in `rag/app.py` or the underlying embedding model (`bge-m3` / `gemini-embedding-2`) lacks the contextual window or token resolution to surface a dense, anomalous fact buried inside repetitive semantic noise. The semantic smoothing effect of the haystack washed out the needle's vector.

---

## 3. Failure Analysis: Hard-Negative Mining

**The Test:** Two virtually identical documents were ingested. One contained a true fact ("TCP uses a 3-way handshake"), while the other contained a semantically cloned false fact ("TCP uses a 4-way handshake"). The system was asked how many steps are in the handshake (`tests/eval_hard_negatives.py`).

**The Result:** ❌ **FAILED**
`RAG-Anything` retrieved the hard negative (or both indiscriminately) and was tricked by the semantic similarity.

**Root Cause:**
Standard cosine similarity metrics used by the V1 system do not discriminate between "3" and "4" in highly homogenous text blocks. Because the structural semantics are identical, the vector distance is negligible. The V1 system lacks a cross-encoder reranking step (like `Cohere Rerank` or `BGE-Reranker`) which is explicitly designed to catch these hard-negative factual discrepancies.

---

## 4. Ragas Automated Evaluation

**The Test:** A synthetic dataset of context-question-answer triplets was constructed to be evaluated by an LLM-as-a-Judge for Faithfulness and Context Precision (`tests/eval_ragas.py`).

**The Result:** ⚠️ **SKIPPED / FAILED**
The isolated `.venv` environment repeatedly lost synchrony with the underlying OS-level C-bindings required by `datasets` and `pyarrow`, causing silent proxy-compatibility failures. While this is an environmental failure rather than an architectural one, it underscores the fragility of relying on complex third-party evaluation frameworks in a locked-down V1 Docker stack.

---

## 5. Next Steps & Recommendations

The failures observed are not code bugs; they are architectural limitations of the V1 stack. The current chunking, embedding, and retrieval pipeline is simply outmatched by modern 2026 data extraction requirements.

**Mandatory Upgrade:** 
We must proceed with the **V2 RAG Architecture Blueprint**:
1.  **Engine Swap:** Replace `RAG-Anything` and `basic-memory` with a unified **PostgreSQL + pgvector** backend.
2.  **Reranking:** Introduce a Cross-Encoder Reranker step in the new `Scout` pipeline to eliminate Hard-Negative hallucinations.
3.  **Hybrid Search:** Implement BM25 + Vector Search (with Reciprocal Rank Fusion) natively in Postgres to solve the Needle-in-a-Haystack problem.
