# SNP V3 Page Schema and Authoring Contract

The target vault's `SCHEMA.md` is authoritative. A new page uses YAML
frontmatter in this shape:

```yaml
---
title: Page Title
created: 2026-09-04
updated: 2026-09-04
type: entity
tags: [security, openshift]
sources: [https://example.com/source]
confidence: high
contested: false
contradictions: []
---
```

Allowed page types are `entity`, `concept`, `comparison`, `query`, `summary`,
and `schema`. Confidence is `high`, `medium`, or `low`. `contested` and
`contradictions` are optional. Preserve additional capture metadata rather than
normalizing it away. Source entries are provenance references; authors do not
invent cached-content digests.

Use a lowercase-hyphen filename, bump `updated` on edit, and add new pages to
the authored catalogue and editorial log according to the vault's own rules.
Never regenerate those control documents from sparse metadata.

## Body frame

Two headings are required, in this order, exactly once each:

- `## TL;DR` — a summary of the page's own text. This is not decoration: the
  ingester makes it **chunk 0** of the indexed page, so it is the first thing a
  query is scored against. A page without one is found by whatever fragment
  happens to match rather than by what it is about.
- `## Cross-References` — gathers `[[wikilinks]]`. Lines that are nothing but
  wikilinks are stripped from the indexed text, so this section is navigation
  for a reader rather than content for the retriever.

Three headings are optional. Each sits immediately before `## Cross-References`
when present, in this order: `## Technical Specifications`, `## Provenance`,
`## Works Cited`.

`## Provenance` is optional unconditionally, including on pages that declare
`sources:`. It is a dated sourcing changelog, and one cannot be written
retroactively without inventing the dates — so requiring it would make
fabrication the only route to a green lint.

Interior sections are free-form under the authored frame (see below). Add at
least two outbound `[[wikilinks]]`, introduce lists with a context sentence,
and keep one primary subject per page.

The current automated checker does not yet certify this complete V3 contract.
Review these requirements explicitly and report the verifier limitation rather
than presenting a narrower check as full conformance.
