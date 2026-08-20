---
description: Synthesizes a raw document into a structured, AGENTS.md-compliant Knowledge Vault note on a PR feature branch.
---

# /snp-compile

Execute the following compilation steps:

1. **Verify Raw Document Existence**:
   - Verify that the target file exists under `raw/` or in the Data Vault warehouse.

2. **Mint a Verifiable RAG Address (Rule R-6.3)**:
   - Generate candidate phrases describing the document's core concepts.
   - Run `python scripts/mint.py --path raw/<file> --hint "<candidate>" --department <department> --loc "<locator>"`.
   - Use the first hint that returns `PASS`.

3. **Author the Markdown Note**:
   - On a feature branch, run `python scripts/compile_note.py --path raw/<file> --title "<title>" --category <category> --dept <department> --loc "<locator>"`.
   - The compiler requires strict model JSON, scoped minting, candidate lint,
     overwrite protection, per-file atomic replacement, and exact rollback on
     ordinary failures. It does not claim a cross-file crash transaction.
   - Structure the body in deterministic order:
     - `## TL;DR` (1 paragraph dense summary)
     - `## Technical Specifications` (domain knowledge and architecture)
     - `## Provenance` (raw sources and data conflicts)
     - `## Cross-References` (`[[wikilink-slug]]` links only)

4. **Verify Schema & Index**:
   - Run `python3 scripts/gen_index.py --check` to confirm zero lint errors.

5. **Branch & Propose (Rule R-6.4, R-7.3)**:
   - Run `python scripts/propose_page.py --page wiki/<category>/<slug>.md`.
   - It rejects pre-staged work and commits only the page plus changed generated
     companions (`wiki/index.md`, `wiki/log.md`). Local branch/add/commit
     failures restore the original branch; an ambiguous push failure preserves
     the verified local commit. Open a Pull Request; **NEVER** push directly to
     `main`.
