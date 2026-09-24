# Technical Proposal: SNP Memory System V4 — Zero-Embedding Sparse-First & Graph-Augmented Retrieval Architecture

> **DOMAIN REFERENCE, NOT SNP DEPLOYMENT AUTHORITY** — active technical proposal for V4 Sparse-First & Zero-Embedding Hybrid Retrieval Architecture.

| Field | Value |
|---|---|
| **Document ID** | SNP-PROP-2026-V4 |
| **Title** | Zero-Embedding Sparse-First & Graph-Augmented Retrieval Architecture |
| **Status** | Active Technical Proposal |
| **Date** | 2026-09-12 |
| **Author** | Antigravity AI Engineering & Architecture Team |
| **Target Audience** | Vault Lead, AI Engineers, Systems Operators, Claude Code & Codex Agents |
| **Reference Implementation** | 433-Page Lead Obsidian Vault & SNP V3 Baseline |
| **Execution Window** | Post-Demo Rollout (Starting Tuesday, 2026-09-15) |

---

## 1. Executive Summary & Problem Motivation

### 1.1 The V3 Dense-Embedding Scaling Bottleneck
The SNP Memory System V3 establishes a disciplined retrieval contract (`wiki_search` $\rightarrow$ `wiki_read`) backed by PostgreSQL 16, pgvector, LiteLLM, and a cloud embedding model. While highly effective for a curated 433-page vault (~2,065 chunks), this architecture encounters severe operational and financial friction when scaling to multi-user environments with 2 GB–5 GB+ raw source corpora:

1. **Embedding API Cost & Rate Limits**: Embedding every chunk of large raw corpora (PDFs, transcripts, articles) across multiple concurrent users incurs continuous API token costs.
2. **Re-Indexing Latency**: Full rebuilds or mass re-embeddings of 5 GB corpora take hours and can stall ingestion workers.
3. **Semantic Drift & Hallucination**: Dense vector embeddings often conflate distinct technical acronyms (e.g., SS7 vs Diameter, or OPA vs Gatekeeper) based on cosine similarity in generalized vector spaces, causing false-positive routing snippets.
4. **Daemon Overhead**: Running PostgreSQL 16 + pgvector + LiteLLM + Uvicorn requires significant background container memory (~2 GB–4 GB RAM), hindering lightweight local developer environments.

### 1.2 The V4 Paradigm Shift: Zero-Embedding Sparse-First + Graph
SNP Memory V4 shifts the baseline retrieval philosophy from **dense-by-default** to **zero-embedding sparse-first augmented by knowledge graph topology**:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         V3 RETRIEVAL (Dense by Default)                     │
│   Every Chunk ──▶ 1024-dim LiteLLM Vector ──▶ PostgreSQL pgvector HNSW      │
│   (High API cost, multi-hour re-indexing, potential semantic drift)         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    V4 RETRIEVAL (Zero-Embedding Sparse-First)               │
│   Every Chunk ──▶ SQLite FTS5 (BM25) ──▶ Exact ID & Heading Match           │
│                          │                                                  │
│                          ▼                                                  │
│   Candidate Hits ──▶ 4-Signal Graph Traversal ──▶ Fast Top-K Fusion         │
│   (Zero API cost, <15ms cold latency, deterministic provenance citation)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. System Architecture Specification

### 2.1 End-to-End System Topology

```text
+─────────────────────────────────────────────────────────────────────────────+
|                         SNP MEMORY SYSTEM V4 TOPOLOGY                       |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  [ INPUT SURFACES ]                                                         |
|  ├── Obsidian Desktop Vault (Human Owner: markdown, wikilinks)              |
|  └── Multi-Source Raw Feed (PDFs, Markdown, Web Scrapes, Transcripts)       |
|                                                                             |
|                                     │                                       |
|                                     ▼                                       |
|  [ INGESTION ENGINE (2-Step CoT + Content-Addressed Store) ]                 |
|  ├── Step 1: Analyze Worker (SHA-256 CAS, FTS Tokenizer, Wikilink Extractor)|
|  └── Step 2: Synthesize Worker (Graph Compiler, Atomic Chunk Versioning)     |
|                                                                             |
|                                     │                                       |
|                                     ▼                                       |
|  [ CORE DATABASE & RETRIEVAL ENGINE ]                                       |
|  ├── Local Embedded DB: SQLite 3.45+ with FTS5 (BM25 k1=1.2, b=0.75)        |
|  ├── Graph Store: Directed Edges (Wikilinks, Inbound/Outbound, Co-citations) |
|  └── Multi-Signal Ranker: Lexical + Graph Proximity + Co-citation + Recency |
|                                                                             |
|                                     │                                       |
|                                     ▼                                       |
|  [ OUTPUT & QUERY SURFACES ]                                                |
|  ├── FastMCP Server (:8080/mcp HTTP + stdio for Claude Code, Codex, Cursor) |
|  ├── Direct Obsidian Vault Sync (Bidirectional via Git / Webhook)           |
|  └── REST / Web Explorer Endpoint (:19828/api)                              |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### 2.2 Dual-Tier Storage Layer
1. **Tier 1: Content-Addressed Raw Store (`raw/`)**:
   - Holds immutable original artifacts (PDFs, technical papers, raw Markdown).
   - Stored and indexed by SHA-256 hash digest: `raw/sha256/<hash[:2]>/<hash>.<ext>`.
   - Never modified in place; serves as ground truth evidence for provenance.
2. **Tier 2: Curated Knowledge Vault (`wiki/`)**:
   - Obsidian-compatible Markdown pages following the V3 frontmatter schema.
   - Lowercase hyphenated filenames (`techniques/ss7-interception.md`).
   - Grounded headings (`## TL;DR`, `## Provenance`, `## Cross-References`).
   - Outbound `[[wikilinks]]` form the explicit knowledge graph edges.

### 2.3 SQLite FTS5 & Graph Schema Design

```sql
-- 1. Curated and Raw Documents Table
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,                       -- Relative path (e.g. 'concepts/ss7.md')
    sha256 TEXT NOT NULL UNIQUE,               -- Content hash digest
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL,                    -- entity, concept, comparison, raw
    department_scope TEXT NOT NULL,            -- CSV: 'ai_eng,infra,blueteam'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Document Chunks Table
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,                 -- '<doc_id>#<chunk_index>'
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    heading TEXT,
    chunk_index INTEGER NOT NULL,
    byte_offset INTEGER NOT NULL,
    content TEXT NOT NULL
);

-- 3. FTS5 Full-Text Search Virtual Table
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    heading,
    content,
    tokenize = 'porter unicode61 remove_diacritics 1'
);

-- 4. Knowledge Graph Edges Table (Explicit Wikilinks & Sources)
CREATE TABLE IF NOT EXISTS graph_edges (
    source_doc TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_doc TEXT NOT NULL,
    link_type TEXT NOT NULL,                   -- 'wikilink', 'raw_source', 'parent'
    weight REAL DEFAULT 1.0,
    PRIMARY KEY (source_doc, target_doc, link_type)
);

CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_doc);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_doc);
```

---

## 3. Key Features & Competitive Synthesis

### 3.1 2026 Industry Best Practices for Enterprise Vaults
1. **Sparse-First Retrieval Baseline**: Full-text lexical search provides deterministic, explainable results with zero inference cost, eliminating vector hallucination for technical terms.
2. **Fail-Closed Department Isolation**: Clearance scopes (`redteam`, `blueteam`, `ai_eng`, `infra`) must be enforced at query execution time. Unauthorized records return zero rows, not errors.
3. **Read-Mode Granularity Ladder**: Retrieval APIs must offer tiered responses (`tldr` $\rightarrow$ `outline` $\rightarrow$ `section` $\rightarrow$ `full`) to minimize agent token usage.
4. **Strict R-8.5 Security Guard**: Retrieved content is treated strictly as untrusted data, never as executable instructions.

### 3.2 Top 5 Features Learned from `Tencent/WeKnora` and `nashsu/llm_wiki`

| # | Feature Name | Source Project | Architectural Impact |
|---|---|---|---|
| **1** | **2-Step CoT Ingestion Pipeline** | `Tencent/WeKnora` | Decouples lexical parsing from semantic knowledge graph synthesis. Enables background chunk extraction without saturating LLM context windows. |
| **2** | **4-Signal Graph Relevance Scoring** | `nashsu/llm_wiki` | Eliminates vector embeddings by scoring candidate documents using Lexical BM25 + Graph Distance + Co-citation + Recency decay. |
| **3** | **SHA-256 Content-Addressable Cache** | `Tencent/WeKnora` | Guarantees zero redundant compute. If a raw file digest matches the CAS, ingestion skips reprocessing instantly. |
| **4** | **Atomic Chunk Rollback & Multi-Revision Versioning** | `Tencent/WeKnora` | Wraps batch ingestion in transactional units. If an ingestion batch fails midway, state rolls back cleanly with zero index corruption. |
| **5** | **Multi-User Worker Pool Governance** | `Tencent/WeKnora` & `llm_wiki` | Implements dual-priority queues: Priority 1 for interactive agent searches (<50ms budget), Priority 2 for background bulk ingest jobs. |

#### Detailed Formulation: 4-Signal Graph Relevance Scoring
For any query $q$ and candidate page $p$, the relevance score $S(p, q)$ is computed directly on the graph without embedding calculations:

$$S(p, q) = \alpha \cdot \text{BM25}(p, q) + \beta \cdot \text{GraphProximity}(p, C) + \gamma \cdot \text{CoCitation}(p, C) + \delta \cdot \text{Recency}(p)$$

- $\text{BM25}(p, q)$: SQLite FTS5 rank score normalized to $[0, 1]$.
- $\text{GraphProximity}(p, C)$: Distance decay score from top-$K$ seed hits $C$:
  $$\text{GraphProximity}(p, C) = \sum_{c \in C} \frac{1}{2^{\text{hop}(p, c)}} \quad (\text{hop}=1 \implies 0.5, \text{hop}=2 \implies 0.25)$$
- $\text{CoCitation}(p, C)$: Boost ($+4.0$) if candidate $p$ shares a common raw provenance source with any seed hit $c \in C$.
- $\text{Recency}(p)$: Time-decay weight $e^{-\lambda \Delta t}$ based on page `updated` frontmatter.

---

## 4. Multi-User Workflow Specifications

```text
+-----------------------------------------------------------------------------+
|                        MULTI-USER OPERATING WORKFLOWS                       |
+-----------------------------------------------------------------------------+
|                                                                             |
|  WORKFLOW A: Human Knowledge Worker (Obsidian UI)                           |
|  - Edits Markdown in Obsidian Desktop.                                      |
|  - Commits & pushes to Git remote.                                          |
|  - Ingestion daemon auto-detects diff, updates SQLite FTS5 in <1s.          |
|                                                                             |
|  WORKFLOW B: Autonomous Coding Agent (Claude Code, Codex, OpenCode)         |
|  - Connects to Scout MCP via Streamable HTTP or stdio.                      |
|  - Calls wiki_search(query, department) -> reads TL;DR or section.          |
|  - Cites exact page path & heading; submits proposed changes via PR branch. |
|                                                                             |
|  WORKFLOW C: Bulk Researcher & Ingestion Operator                           |
|  - Drops raw PDFs / transcripts into raw/ directory.                        |
|  - Ingestion worker validates SHA-256 CAS -> extracts chunks -> updates KG.|
|                                                                             |
+-----------------------------------------------------------------------------+
```

---

## 5. Implementation Roadmap & Time Estimates

Execution is scheduled to begin immediately following Monday's Demo Day (2026-09-15):

```text
2026-09-14: Demo Day (Current V3 Container Stack - Frozen)
     │
     ▼
Phase 1: SQLite FTS5 Engine Scaffolding ────────────▶ 12h
     │
     ▼
Phase 2: Graph Engine & 4-Signal Ranking ───────────▶ 14h
     │
     ▼
Phase 3: 2-Step Ingestion Pipeline & CAS ───────────▶ 18h
     │
     ▼
Phase 4: Scout Wire Integration & MCP Surface ──────▶ 10h
     │
     ▼
Phase 5: Acceptance Testing & Benchmarking ─────────▶  8h
     │
     ▼
Total Engineering Effort: 62 Hours (~8 Working Days)
```

### Detailed Milestone Breakdown

| Phase | Milestone & Deliverables | Primary Files Touched | Est. Hours |
|---|---|---|:---:|
| **Phase 1** | **SQLite FTS5 Storage Backend**<br>• Implement `SqliteFtsBackend` implementing `RagBackend`<br>• Migration script from PostgreSQL `rag_chunks`<br>• Automated FTS5 indexing and BM25 tokenization | `scout/backends/sqlite_fts.py`<br>`scout/config.py`<br>`tests/test_sqlite_backend.py` | **12h** |
| **Phase 2** | **Graph Engine & 4-Signal Ranker**<br>• Wikilink extraction regex parser<br>• 2-hop graph expansion queries in SQLite<br>• 4-signal score fusion algorithm ($S_{final}$) | `scout/graph.py`<br>`scout/ranker.py`<br>`tests/test_graph_ranking.py` | **14h** |
| **Phase 3** | **2-Step Ingest Pipeline & SHA-256 CAS**<br>• Step 1: Lexical chunking & CAS hashing<br>• Step 2: Synthesis daemon with atomic rollback<br>• Concurrency worker pool governance | `scout/ingest_worker.py`<br>`scout/cas.py`<br>`tests/test_ingest_pipeline.py` | **18h** |
| **Phase 4** | **Scout MCP & FastMCP Unification**<br>• Wire `SqliteFtsBackend` into FastMCP server<br>• Streamable HTTP and stdio client exporters<br>• Department scope filtering enforcement | `scout/mcp_server.py`<br>`scripts/export_mcp_config.py`<br>`tests/test_mcp_sqlite.py` | **10h** |
| **Phase 5** | **Acceptance Verification & Benchmarks**<br>• Cold search latency verification ($<50\text{ms}$)<br>• Full regression against 1,422 offline tests<br>• Multi-agent concurrent query stress test | `tests/test_perf_benchmark.py`<br>`tests/test_regression_v4.py` | **8h** |

---

## 6. Verification & Acceptance Criteria

1. **Zero External Embedding Model Dependency**: Core search operates 100% locally with zero HTTP calls to LiteLLM or third-party embedding APIs.
2. **Cold Search Performance**: `wiki_search` execution returns top-5 ranked results in $< 15\text{ms}$ on a standard workstation.
3. **Graph Retrieval Recall**: The 4-signal graph ranker achieves $\ge 95\%$ recall against the curated test query benchmark.
4. **Fail-Closed Security**: Queries under any department token physically return zero rows for unpermitted documents.
5. **Preserved Test Suite**: All existing unit and contract tests in `tests/` pass with zero regressions.
