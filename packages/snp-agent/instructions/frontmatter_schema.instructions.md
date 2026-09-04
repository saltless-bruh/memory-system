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

Every page has an H1 and `## Cross-References`. `## TL;DR` is recommended.
`## Provenance` is required when sources are declared. Interior sections are
free-form. Add at least two outbound `[[wikilinks]]`, introduce lists with a
context sentence, and keep one primary subject per page.

The current automated checker does not yet certify this complete V3 contract.
Review these requirements explicitly and report the verifier limitation rather
than presenting a narrower check as full conformance.
