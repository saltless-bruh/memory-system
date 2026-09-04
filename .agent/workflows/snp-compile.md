---
description: Authors or revises a V3 wiki page from available evidence on a feature branch for human review.
---

# /snp-compile

1. Confirm the user requested a wiki change and that the supporting evidence is
   already available. Source fetching is not yet implemented.
2. Read the target vault's `SCHEMA.md` and inspect neighboring pages for local
   conventions.
3. Author the V3 frontmatter, H1, grounded free-form sections, optional
   `## TL;DR`, required source-dependent `## Provenance`, and mandatory
   `## Cross-References` with at least two `[[wikilinks]]`.
4. Preserve extra capture metadata, immutable evidence, and authored control
   documents. Do not generate blank catalogue descriptions.
5. Review every schema and heading requirement explicitly. Current automation
   does not certify the entire V3 page contract.
6. Put the change on a feature branch and open a pull request for human review.
   Preserve unrelated work and never merge directly to a protected branch.

Source metadata records provenance; it does not contain manually composed
semantic index pointers.
