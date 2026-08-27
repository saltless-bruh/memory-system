---
name: snp-search-wiki
description: "Use this skill when you need to answer a technical question about the system, its infrastructure, or security policies. This skill instructs the agent on how to search and read the compiled Wiki knowledge map via the snp-wiki MCP server."
---

# snp-search-wiki

## Purpose
This skill guides you through searching and reading the "compiled map" of the SNP Memory System. The Knowledge Vault (`wiki/*.md`) contains human- and agent-compiled technical specifications, architectural patterns, and cross-references.

## When to use
Consult the wiki first for any technical question about the system, its infrastructure, concepts, or security playbooks. The reason is not protocol: the wiki carries the minted `sources[]` addresses that RAG needs, so descending to raw storage without a page means guessing an address, and a guessed hint retrieves nothing.

---

## Two constraints that change how you query

**Search in English.** The wiki embeds with FastEmbed `bge-small-en-v1.5` @384, measured at `recall@1 0.625` on Vietnamese paraphrases against `0.812` for a multilingual alternative. A non-English query returns near-random ordering, so ask in English and expect to check more than the first hit even when you do. Whether to adopt a multilingual model is an open owner decision — `docs/ARCHITECTURE_STATUS.md` §OD-1.

**Do not use the generic `search` and `fetch` tools.** The wiki server serves them, but this deployment runs with `disable_permalinks` on to keep the vault pristine, so `search` returns placeholder ids (`doc-0`, `doc-1`, …) that `fetch` cannot resolve. `search_notes` and `read_note` key on title and path and work correctly.

---

## Tool Calling Specification (`snp-wiki` MCP)

* **Server**: `snp-wiki` (Port 8765)
* **Transport**: Streamable HTTP (`http://localhost:8765/mcp`)

### 1. `search_notes` — Semantic Discovery

Input:
```json
{
  "query": "convolutional neural network shared weights feature extraction"
}
```

Returns candidate pages with their title, path and score. Read the summaries before choosing; the first hit is not reliably the best one.

### 2. `read_note` — Inspect Compiled Knowledge

The parameter is `identifier`, and it accepts a page **title or path** — there is no slug parameter.

Input:
```json
{
  "identifier": "Convolutional Neural Networks"
}
```

The page body carries `## TL;DR`, `## Technical Specifications`, `## Provenance` and `## Cross-References`. Its frontmatter carries the `sources[]` entries you need if you have to descend to RAG:

```yaml
sources:
  - path: raw/papers/computers-12-00091.pdf
    loc: "p.12"
    hint: "Convolutional Neural Networks"
```

---

## 3-Step Decision Protocol (Rule R-5)

1. **Search notes.** Call `search_notes(query)` in English to find candidate pages. Do not load the entire vault index.
2. **Read and evaluate.** Read `## Technical Specifications` on the best candidate.
3. **Sufficiency stop (Rule R-5.1).** If the page answers the question, stop there and cite it as `[[convolutional-neural-networks]]`. Calling RAG anyway costs a retrieval round trip and returns raw text you already have in compiled form. Descend to `snp-rag-fetch` only when you need the verbatim original — a direct quotation, or a detail the compiler did not carry over — and then pass the page's exact `sources[]` entry.
