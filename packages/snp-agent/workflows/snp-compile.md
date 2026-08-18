---
description: Synthesizes a raw document into a structured, AGENTS.md-compliant Knowledge Vault note on a PR feature branch.
---

# /snp-compile

Execute the following compilation steps:

1. **Verify Raw Document Existence**:
   - Verify that the target file exists under `raw/` or in the Data Vault warehouse.

2. **Mint a Verifiable RAG Address (Rule R-6.3)**:
   - Generate candidate phrases describing the document's core concepts.
   - Run `python scripts/mint.py --path raw/<file> --hint "<candidate>"` (or call Scout MCP minting API in team mode).
   - Use the first hint that returns `PASS`.

3. **Author the Markdown Note**:
   - Create `wiki/<category>/<slug>.md` with the mandatory 7-field frontmatter (`type`, `title`, `summary`, `entities`, `department`, `sources`, `last_compiled`).
   - Structure the body in deterministic order:
     - `## TL;DR` (1 paragraph dense summary)
     - `## Technical Specifications` (domain knowledge and architecture)
     - `## Provenance` (raw sources and data conflicts)
     - `## Cross-References` (`[[wikilink-slug]]` links only)

4. **Verify Schema & Index**:
   - Run `python3 scripts/gen_index.py --check` to confirm zero lint errors.

5. **Branch & Propose (Rule R-6.4, R-7.3)**:
   - Checkout a feature branch: `git checkout -b wiki/add-<slug>`.
   - Commit the changes and push to open a Pull Request. **NEVER** push directly to `main`.
