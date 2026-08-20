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
   - Ensure every `sources[].hint` satisfies **both** conditions the gate enforces
     (`TOP_RANK` and `GROUNDING_MIN_COVERAGE` in `scripts/verify_addresses.py`):
     - **Rank** — the addressed file must win **rank 1** of the declaring page's
       department-scoped retrieval for that hint (exact score ties share rank 1).
     - **Grounding** — at least **50% of the hint's content tokens** must occur in
       the text that file itself returned.
   - There is **no similarity threshold**, and there cannot be one here:
     `RagChunk.score` carries Reciprocal Rank Fusion weights (`1/(60+rank)` summed
     over the dense and sparse arms, capped near `0.033`), which is an ordinal
     fusion weight rather than a similarity.
   - A `note:` line reporting a declared `loc` that is no longer among the
     retrieved locators is **advisory** — it never changes the exit code, because a
     locator that went stale after minting is a content decision for a human.

3. **Evaluate Results**:
   - Exit `0`: all addresses pass.
   - Exit `1`: semantic `DRIFT` (another file outranked the addressed one, or the
     hint is not grounded in it — re-mint, taking the vocabulary from the source)
     or `FAIL` (the addressed file returned **no chunks** for this hint: unindexed,
     empty after parsing, or outside the page's department). Run the closed-loop PR
     gate or `/snp-heal` — but neither can repair a source that has no chunks to
     match; that has to be fixed as content.
   - Exit `2`: infrastructure/configuration failure; fail without mutation.
   - The CI entry point is
     `uv run python scripts/ci_address_gate.py --mode pr`, which performs at
     most one heal pass and requires post-heal address plus lint success.
