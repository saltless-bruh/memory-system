---
name: snp-compile-wiki
description: "Use this skill when synthesizing, compiling, or summarizing a newly indexed raw file (e.g. an RFC, a report, a spreadsheet) into the Wiki Knowledge Vault."
---

# snp-compile-wiki

## Purpose
This skill guides the synthesis of raw documents (`raw/`) into compiled Knowledge Vault notes (`wiki/`) that satisfy the 7-field frontmatter contract and PR-first governance.

---

## 1. Mint a Verifiable RAG Address (Rule R-6.3)

Never hand-write `sources[].hint`. Always mint it against PostgreSQL pgvector:

```bash
uv run python scripts/mint.py \
  --path raw/papers/computers-12-00091.pdf \
  --hint "Convolutional Neural Networks" \
  --department ai_eng \
  --loc "p.12"
```

### Expected Output:
```yaml
sources:
  - path: raw/papers/computers-12-00091.pdf
    loc: "p.12"
    hint: "Convolutional Neural Networks"
```

---

## 2. Frontmatter Contract & Section Schema (Rule R-1.3)

Every page in `wiki/` (`concepts/`, `techniques/`, `entities/`, `playbooks/`) must have these exact 7 fields and 4 sections:

```markdown
---
type: concept              # technique | entity | playbook | concept
title: Convolutional Neural Networks
summary: Deep learning algorithms that automatically extract features using shared weights and local connections.
entities: [convolutional-neural-networks, deep-learning, feature-extraction]
department: ai_eng         # Scope hook (redteam | blueteam | ai_eng | infra)
sources:                   # ADDRESS out to RAG Data Vault
  - path: raw/papers/computers-12-00091.pdf
    loc: "p.12"
    hint: "Convolutional Neural Networks"
last_compiled: 2026-08-19
---

## TL;DR
Dense, assertive summary of the compiled technical knowledge — no conversational filler.

## Technical Specifications
Detailed specifications, algorithms, mathematical formulations, and architectures.

## Provenance
Direct ties to raw/ sources and reconciliation of conflicting information.

## Cross-References
Relational links using [[wikilink-slug]] syntax only.
```

---

## 3. Automated Compilation CLI

```bash
uv run python scripts/compile_note.py \
  --path raw/papers/computers-12-00091.pdf \
  --title "Convolutional Neural Networks" \
  --category concepts \
  --dept ai_eng \
  --loc "p.12"
```

---

## 4. Lint & PR Proposal (Rules R-6.4, R-7.3)

```bash
# 1. Check frontmatter schema & index consistency
uv run python scripts/gen_index.py --check

# 2. Check live RAG address resolution
uv run python scripts/verify_addresses.py

# 3. Create branch and propose PR (NEVER push directly to main)
uv run python scripts/propose_page.py --page wiki/concepts/convolutional-neural-networks.md
```
