---
name: snp-verify-vault
description: >-
  Use this skill when validating that the Wiki knowledge vault is mechanically sound, frontmatter contracts are intact, and all RAG addresses resolve against PostgreSQL.
---

# snp-verify-vault

## Purpose
This skill validates the mechanical integrity of the SNP Memory System V2. It executes both the frontmatter schema linter and the live PostgreSQL pgvector address verification gates before any changes are committed or merged.

## How to use

1. **Verify Frontmatter & Master Index**
   Run the index generator in check mode to ensure all wiki pages have the 7 required frontmatter fields (`type`, `title`, `summary`, `entities`, `department`, `sources`, `last_compiled`), valid `[[wikilinks]]`, and that `wiki/index.md` is current:
   ```bash
   python3 scripts/gen_index.py --check
   ```
   If this reports missing fields or an out-of-date index, fix the frontmatter or run `python3 scripts/gen_index.py` to regenerate the index.

2. **Verify RAG Address Resolution**
   Test every `sources[]` block against PostgreSQL 16 `pgvector` to ensure no citations have drifted or failed:
   ```bash
   uv run python scripts/verify_addresses.py
   ```
   If any address reports `DRIFT` or `FAIL`, use the `snp-auto-heal-vault` skill to auto-heal the addresses.

3. **Pre-PR Merge Gate**
   Both checks MUST return exit code 0 before a branch can be merged to `main`.
