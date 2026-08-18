# SNP Frontmatter Schema & Page Authoring Contract

Every markdown file in `wiki/` must satisfy the 7-field frontmatter contract:

```yaml
---
type: technique            # Required: technique | concept | playbook | entity
title: PagedAttention Engine # Required: Human-readable display title
summary: High-density, assertive one-sentence summary for vector routing. # Required: EXACTLY ONE sentence
entities: [paged-attention, vllm, kv-cache] # Required: 2-8 lowercase entity tags
department: ai_eng         # Required: Scope hook (redteam | blueteam | ai_eng | infra | general)
sources:                   # Required: RAG address pointers (empty list [] for pure concepts)
  - path: raw/reports/vllm_serving.pdf
    loc: p.2
    hint: PagedAttention KV-Cache Virtual Block Allocation
last_compiled: 2026-08-17  # Required: YYYY-MM-DD format
---
```

## Mandatory Body Structure (In Exact Order):
1. `## TL;DR`: Dense, assertive summary (no narrative fluff).
2. `## Technical Specifications`: Domain knowledge, architecture, parameters, and specifications.
3. `## Provenance`: Direct tie-back to raw sources and notes on conflicting data.
4. `## Cross-References`: Graph relations using `[[wikilink-slug]]` syntax only.

## Automated Verification:
Run `python3 scripts/gen_index.py --check` before submitting. Exit code 0 is mandatory.
