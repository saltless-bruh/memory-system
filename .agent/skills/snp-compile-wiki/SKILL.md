---
name: snp-compile-wiki
description: "Use this skill when a user asks you to author or revise a compiled wiki page from available evidence while preserving the V3 schema and pull-request governance."
---

# Compile a wiki page

## Preconditions

- The user requested a page change.
- The evidence is already available and may be cited honestly.
- You are on a feature branch and will preserve unrelated work.

Source fetching and cache population are deferred. If the necessary evidence
is not available, stop the authoring attempt and state what is missing.

## Authoring sequence

1. Read the target vault's `SCHEMA.md`; it overrides templates elsewhere.
2. Use a lowercase-hyphen filename and the V3 fields `title`, `created`,
   `updated`, `type`, `tags`, `sources`, and `confidence`. Preserve optional
   `contested`, `contradictions`, and capture metadata.
3. Write an H1 and free-form grounded sections. Add a concise `## TL;DR` when
   useful, `## Provenance` when sources exist, and required
   `## Cross-References` with at least two `[[wikilinks]]`.
4. Begin sections and lists with enough context for body-chunk retrieval. Keep
   one primary subject per page.
5. Update the authored catalogue and editorial log only as the vault's schema
   requires. Never replace them with generated empty descriptions or machine
   operational messages.
6. Review the schema and headings explicitly. The current automated checker is
   transitional and does not certify the complete V3 contract.
7. Submit the page on a pull request for human review. Never merge directly to
   a protected branch.

Source references record provenance; they are not manually authored semantic
pointers into the retrieval index.
