# Brainstorm: Replacing the Embedding Model in SNP Memory System V3

## Goal
Identify the optimal replacement strictly for the "embedding model" component in the SNP Memory System V3 architecture, eliminating external cloud embedding dependencies (LiteLLM / Google Cloud / OpenAI) while keeping the surrounding ingestion pipeline, PostgreSQL 16 storage, Scout MCP service, ACL/RLS security enforcement, and `wiki_search`/`wiki_read` tool contracts identical.

## Constraints
1. **Interface Seam Preservation:** Preserve the async embedding seam [`AsyncEmbedder`](file:///home/ple/Documents/memo-project/snp-memory-system-main/scout/chunker.py#L30-L35) (`aembed_texts(texts: list[str]) -> list[list[float]]`) or provide a transparent adapter so neither [`scout/sync_job.py`](file:///home/ple/Documents/memo-project/snp-memory-system-main/scout/sync_job.py) nor [`scout/backends/pgvector.py`](file:///home/ple/Documents/memo-project/snp-memory-system-main/scout/backends/pgvector.py) requires an architectural rewrite.
2. **PostgreSQL RLS & Department Clearance:** Department-based access control (`scout.current_depts` transaction configuration) and Row-Level Security in PostgreSQL must remain the authoritative access boundary.
3. **Scout Retrieval Contract:** The external MCP tool envelope returned by `wiki_search` and `wiki_read` (defined in [`AGENTS.md`](file:///home/ple/Documents/memo-project/snp-memory-system-main/AGENTS.md)) must not change.
4. **Architectural Parity:** Keep the Markdown file structure, chunking algorithm, git watcher, and PostgreSQL 16 database stack intact.
5. **Workflow Boundary:** Brainstorm only; do not implement or modify production code in this workflow.

## Known Context
- **Current Seam:** Ingestion (`sync_job.py`) and retrieval (`scout/backends/pgvector.py`) both invoke `LiteLLMBatchEmbedder.aembed_texts()`, which currently makes HTTP POST requests to `http://litellm:4000/v1/embeddings` (configured with `gemini/gemini-embedding-001`, producing 1024-dimensional dense vectors).
- **Existing Fallback in Codebase:** In [`scout/backends/pgvector.py`](file:///home/ple/Documents/memo-project/snp-memory-system-main/scout/backends/pgvector.py#L250-L307), if the embedding call fails, times out, or returns `None`, the backend already falls back gracefully to `_SPARSE_QUERY`.
- **Existing Sparse Infrastructure:** PostgreSQL already maintains two full-text GIN search indexes on `rag_chunks`: `tsv` (English stemmer) and `tsv_simple` (simple unstemmed tokenizer added in [`config/postgres/migrations/007_vietnamese_sparse_arm.sql`](file:///home/ple/Documents/memo-project/snp-memory-system-main/config/postgres/migrations/007_vietnamese_sparse_arm.sql)).

## Risks
1. **Vector Dimension Migration (Dense Replacements):** If a local dense model produces a dimension other than 1024 (e.g. 384 for `all-MiniLM-L6-v2` or `bge-small-en-v1.5`), a database migration on `rag_chunks.embedding` and a re-index of all chunks is required.
2. **Semantic Recall Drop (Vectorless Option):** Switching to a purely lexical system removes the ability to match queries that share no vocabulary with the target documents (e.g. "auth failure" vs "401 unauthorized"), unless manual query expansion is added.
3. **Container CPU & Memory Footprint (Local Models):** Running an in-process embedding model inside Docker containers requires bundling ONNX Runtime and model weights (~100MB–300MB), increasing container image size and CPU consumption during burst ingestion.
4. **PostgreSQL Compatibility (Sparse Vector Models):** Neural sparse models like SPLADE require storing thousands of vocabulary token weights, which cannot easily be stored in a standard continuous `vector(N)` column without a specialized extension or custom JSONB/tsvector mapping.

## Options (2–4)

### Option 1: In-Process Local Dense Embedder (FastEmbed / ONNX Runtime)
* **What Replaces the Embedder:** Replace the `LiteLLMBatchEmbedder` class with a local CPU-optimized in-process embedder using `fastembed` (running e.g. `BAAI/bge-small-en-v1.5` or `sentence-transformers/all-MiniLM-L6-v2` via ONNX Runtime).
* **Architecture Change:**
  - Plugs directly into the existing `AsyncEmbedder.aembed_texts()` seam.
  - Generates dense vectors on the host/container CPU without external HTTP calls.
  - The `litellm` Docker container and cloud API keys (`GEMINI_API_KEY`, etc.) are completely removed from `docker-compose.yml`.
  - Database schema adjusts `rag_chunks.embedding` to the model dimension (e.g., `vector(384)`).
  - `_HYBRID_QUERY` and RRF fusion continue operating with zero SQL changes.
* **Pros:** Drop-in compatibility; retains semantic matching; 100% offline and deterministic; eliminates cloud API costs and rate limits.
* **Cons:** Requires a migration for vector dimension and re-embedding existing chunks; adds ONNX runtime dependencies.

### Option 2: Pure PostgreSQL Lexical Engine (Zero-Model Vectorless Replacement)
* **What Replaces the Embedder:** Drop the embedding model completely. Replace the embedder with a `NoOpEmbedder` (or configuration flag `ENABLE_DENSE=false`).
* **Architecture Change:**
  - Ingestion skips the `aembed_texts()` step, leaving `rag_chunks.embedding` as `NULL`.
  - `scout/backends/pgvector.py` skips the embedding probe and routes all queries straight into the already-existing `_SPARSE_QUERY`.
  - Uses the existing `tsv` and `tsv_simple` GIN indexes in PostgreSQL for multi-lingual keyword ranking.
  - The `litellm` container is removed entirely.
* **Pros:** Zero model latency; zero external dependencies; 100% transparent and deterministic ranking; zero GPU/CPU inference overhead; instant ingestion; already 80% implemented in `scout/backends/pgvector.py`.
* **Cons:** Complete loss of semantic similarity and synonym matching without explicit query expansion.

### Option 3: Learned Sparse Lexical Expansion (SPLADE / BM42 via In-Process ONNX)
* **What Replaces the Embedder:** Replace continuous geometric dense vectors with learned sparse token weights (e.g. SPLADE).
* **Architecture Change:**
  - Instead of a fixed-dimension dense vector, the model outputs an expanded dictionary of `{token: weight}` for each chunk and query.
  - Terms are injected into PostgreSQL's full-text search as weighted terms or stored in a sparse vector table.
  - Retrieval scores text matches by summing learned token weights.
* **Pros:** Best of both worlds: captures semantic synonyms and exact keywords while remaining an inverted index without geometric vector drift.
* **Cons:** High integration complexity; does not map cleanly to the existing `vector(1024)` column; requires writing custom PostgreSQL weight-scoring functions or extensions.

### Option 4: Structural Outline & Heading Traversal (Deterministic Markdown Hierarchy)
* **What Replaces the Embedder:** Replace the dense vector arm with a structural TOC/Heading index in PostgreSQL.
* **Architecture Change:**
  - During ingestion, a new table `rag_headings` indexes document H1, H2, and H3 titles, locators, and outbound `[[wikilinks]]`.
  - At search time, `wiki_search` matches against titles, tags, and headings using PostgreSQL text search.
  - Agents navigate documents using Scout's existing hierarchical envelope (`mode="outline"` followed by `mode="section"`).
* **Pros:** Zero neural models; enforces clean document structure; perfectly aligned with Scout's V3 page reading contract.
* **Cons:** Requires structural schema additions (`rag_headings`); relies on well-structured Markdown pages.

## Recommendation
**Adopt Option 1 (In-Process FastEmbed / ONNX) if semantic recall is required; adopt Option 2 (Pure PostgreSQL Sparse) if zero-model vectorless simplicity is the primary goal.**

1. **Top Recommendation (Option 1 - FastEmbed / ONNX):** This is the purest "replacement *only* for the embedded model" solution. It satisfies the existing `AsyncEmbedder` protocol, leaves PostgreSQL's `_HYBRID_QUERY`, RRF scoring, and RLS filtering 100% intact, and enables the system to shut down the `litellm` service container without changing any downstream retrieval logic.
2. **Alternative (Option 2 - Pure PostgreSQL):** If the user's objective is to abandon vector embeddings completely and embrace "Vectorless RAG", Option 2 is already nearly finished in the codebase: `scout/backends/pgvector.py` already includes `_SPARSE_QUERY`. One simple configuration toggle can make Scout run purely on PostgreSQL full-text search.

## Acceptance Criteria
- [ ] The `AsyncEmbedder` seam in [`scout/chunker.py`](file:///home/ple/Documents/memo-project/snp-memory-system-main/scout/chunker.py) is satisfied by the new engine without altering downstream callers.
- [ ] PostgreSQL RLS policies and `scout.current_depts` transaction-level department isolation remain strictly enforced.
- [ ] Calling agents interact with `wiki_search` and `wiki_read` without any change to the response envelope or parameter contracts.
- [ ] Docker compose stack runs cleanly without the `litellm` container or external cloud API credentials.
- [ ] Offline test suites (`pytest tests/ -m "not live"`) pass with sockets disabled.
