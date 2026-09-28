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

`AGENTS.md` section 4 is the authoritative body frame; this restates it. An H1
title opens the page, and these rules govern its `##` sections:

- `## Cross-References` — **required**, exactly once, last of the mandated
  sections. It gathers `[[wikilinks]]`. Lines that are nothing but wikilinks
  are stripped from the indexed text, so this section is navigation for a
  reader rather than content for the retriever.
- `## Provenance` — source attribution and conflicts.
  **Required when sources are declared**: a page whose frontmatter lists
  `sources:` carries it exactly once, before `## Cross-References`. It attributes the sources the page
  already names; it is not a dated changelog, so writing it never means
  inventing dates.
- `## TL;DR` — two to four assertive, self-contained sentences.
  **Recommended, not mandatory.** When present it appears once, ahead of the
  mandated sections. It is worth writing: the ingester makes it **chunk 0** of
  the indexed page, so it is the first thing a query is scored against, and a
  page without one is found by whatever fragment happens to match.

`## Technical Specifications` and `## Works Cited` are optional. Under the
authored frame they are free-form sections like any other.

Interior sections are free-form under the authored frame (see below). Add at
least two outbound `[[wikilinks]]`, introduce lists with a context sentence,
and keep one primary subject per page.

### Which frame applies

`lint_page` judges a page as one of two document classes, and they are not
interchangeable.

**authored** — a page a person wrote. `## Cross-References` is present once,
`## Provenance` too when sources are declared, in that order; a `## TL;DR`, if
written, comes first and appears once. Every other section is the author's
business. This is the frame for anything in the knowledge vault, and it is what
`verify-vault` uses.

**compiled** — a page `scripts/compile_note.py` generated. Its headings must be
*exactly* the generator's frame — `## TL;DR`, then any of
`## Technical Specifications`, `## Provenance`, `## Works Cited` in that order,
then `## Cross-References` — with nothing else, because that is how drift in
the generator is caught. Do not hand-edit a compiled page toward the authored
frame; regenerate it.

If you are writing or revising a page yourself, you are working in the authored
frame. Reach for the compiled frame only when checking a generator's output.

The current automated checker does not yet certify this complete V3 contract.
It does not check the H1, the two-wikilink minimum or the prose rules. Review
these requirements explicitly and report the verifier limitation rather than
presenting a narrower check as full conformance.
