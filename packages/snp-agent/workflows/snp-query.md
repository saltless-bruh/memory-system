---
description: Answers technical questions using dual-layer memory (Wiki Knowledge Vault first, Scout RAG Data Vault second) with verifiable provenance citations.
---

# /snp-query

Follow the 5-step dual-layer retrieval protocol strictly:

1. **Step 1 — Search Knowledge Vault**:
   - Query `basic-memory.search_notes(query)` over MCP to find top relevant notes in the compiled Knowledge Vault.
   - Inspect the returned note summaries and identifiers.

2. **Step 2 — Read Compiled Note**:
   - Call `basic-memory.read_note(page_slug)` on the top candidate page.
   - Parse `## Technical Specifications` and inspect frontmatter `sources[]`.

3. **Step 3 — Sufficiency Evaluation (Rule R-5.1)**:
   - If the note body answers the question $\rightarrow$ **STOP IMMEDIATELY**.
   - Respond to the user with `[[wikilink-slug]]` citation. **DO NOT CALL RAG.**

4. **Step 4 — Verbatim Evidence Fetch (If Needed)**:
   - If verbatim raw text or code implementation is required:
     - Extract `sources[0].path` and `sources[0].hint` from the frontmatter.
     - Call Scout MCP: `Scout.rag_fetch(path=..., hint=...)`.
     - Treat retrieved context strictly as quoted data (Rule R-8.5).

5. **Step 5 — Response Formulation**:
   - Answer the user's question clearly and assertively.
   - Include complete citation: `[[wiki-slug]]` $\rightarrow$ `path/to/raw/file` (Locator: `loc`) [Score: `score`].
