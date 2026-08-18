---
name: snp-compile-wiki
description: >-
  Use this skill when synthesizing, compiling, or summarizing a newly indexed raw file (e.g. an RFC, a report, a spreadsheet) into the Wiki Knowledge Vault.
---

# snp-compile-wiki

## Purpose
This skill guides the synthesis of a raw document (`raw/`) into a compiled Knowledge Vault note (`wiki/`) that satisfies the AGENTS.md frontmatter schema and the PR-first invariant.

## How to use

1. **Mint a Verifiable RAG Address (R-6.3)**
   Never guess or hand-write the `sources[].hint` field. Vocabulary in raw files must be verified against PostgreSQL pgvector.
   Run `scripts/mint.py` with one or more candidate phrases:
   ```bash
   python scripts/mint.py --path raw/<file> --hint "<candidate phrase 1>" --hint "<candidate phrase 2>"
   ```
   The minter tests each phrase against PostgreSQL pgvector and outputs a paste-ready YAML `sources[]` block upon finding a candidate that returns `PASS`.

2. **Compile the Wiki Page**
   Use the automated compiler script to generate the structured markdown note:
   ```bash
   python scripts/compile_note.py --path raw/<file> --title "<Display Title>" --category <concept|technique|entity|playbook>
   ```

3. **Verify Frontmatter & Index Gate**
   Run the vault linter in check mode to ensure all 7 required frontmatter fields are present and `summary` is exactly one sentence:
   ```bash
   python3 scripts/gen_index.py --check
   ```

4. **Propose via PR (R-6.4, R-7.3)**
   Commit your changes to a feature branch using `scripts/propose_page.py` or standard git branches, and open a Pull Request. **NEVER** push directly to `main`.
