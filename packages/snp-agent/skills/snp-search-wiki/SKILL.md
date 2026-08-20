---
name: snp-search-wiki
description: >-
  Use this skill when you need to answer a technical question about the system, its infrastructure, or security policies.
  This skill instructs the agent on how to search and read the compiled Wiki knowledge map via the basic-memory MCP.
---

# snp-search-wiki

## Purpose
This skill guides you through searching and reading the "compiled map" of the SNP Memory System. The Knowledge Vault (`wiki/*.md`) contains human- and agent-compiled technical specifications, architectural patterns, and cross-references.

## When to use
**ALWAYS** use this skill first when asked a technical question about the system, its infrastructure, concepts, or security playbooks. Never go straight to raw RAG storage without consulting the wiki first.

---

## Tool Calling Specification (`basic-memory` MCP)

* **Server**: `basic-memory` (Port 8765)
* **Transport**: Streamable HTTP (`http://localhost:8765/mcp`)

### 1. `search_notes` — Semantic Discovery
* **Input**:
```json
{
  "query": "PagedAttention GPU memory allocation KV cache fragmentation"
}
```
* **Output**:
```json
[
  {
    "slug": "concepts/paged-attention-engine",
    "title": "PagedAttention Engine",
    "summary": "Allocates non-contiguous physical GPU VRAM blocks for KV-caches to eliminate memory fragmentation in high-throughput LLM serving.",
    "department": "ai_eng",
    "score": 0.942
  }
]
```

### 2. `read_note` — Inspect Compiled Knowledge
* **Input**:
```json
{
  "page_slug": "concepts/paged-attention-engine"
}
```
* **Output**:
```json
{
  "title": "PagedAttention Engine",
  "frontmatter": {
    "type": "concept",
    "summary": "Allocates non-contiguous physical GPU VRAM blocks for KV-caches to eliminate memory fragmentation in high-throughput LLM serving.",
    "entities": ["paged-attention", "vllm", "kv-cache"],
    "department": "ai_eng",
    "sources": [
      {
        "path": "raw/reports/vllm_high_throughput_serving.pdf",
        "loc": "p.2",
        "hint": "PagedAttention KV-Cache Virtual Block Allocation"
      }
    ]
  },
  "content": "## TL;DR\n..."
}
```

---

## 3-Step Decision Protocol (Rule R-5)

1. **Search Notes**: Call `basic-memory.search_notes(query)` to find top relevant candidate pages. Do not load the entire vault index.
2. **Read Body & Evaluate**: Read `## Technical Specifications`.
3. **Sufficiency Evaluation (Rule R-5.1)**:
   - If the note body answers the question $\rightarrow$ **STOP IMMEDIATELY**.
   - Respond with citation: `[[concepts/paged-attention-engine]]`. **DO NOT CALL RAG.**
   - If verbatim proof or raw code implementation is missing $\rightarrow$ proceed with `snp-rag-fetch` using the exact `sources[]` address.
