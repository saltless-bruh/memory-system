---
description: Validates Knowledge Vault frontmatter schemas and live PostgreSQL pgvector RAG address resolution gates.
---

# /snp-verify

Execute the pre-flight verification gates:

1. **Gate 1 — Frontmatter & Master Index Lint**:
   - Run `python3 scripts/gen_index.py --check`.
   - Verify that all pages have the 7 required frontmatter fields, valid `[[wikilinks]]`, and that `wiki/index.md` is current.

2. **Gate 2 — RAG Address Resolution**:
   - Run `uv run python scripts/verify_addresses.py`.
   - Ensure every `sources[].hint` resolves against PostgreSQL `pgvector` with score $\ge 0.70$.

3. **Evaluate Results**:
   - **100% PASS**: Output confirmation that the vault is clean and safe to merge.
   - **DRIFT or FAIL**: Output the list of drifted files and recommend running `/snp-heal`.
