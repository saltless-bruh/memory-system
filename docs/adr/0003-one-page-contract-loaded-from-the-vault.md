# ADR-0003 — One page contract, loaded from the vault

- **Status:** Accepted · 2026-09-15
- **Deciders:** repository owner
- **Re-cuts:** `REMEDIATION B2`, which scoped this to four tree entries

## Context

Measured: three different page schemas were in play.

| source | required fields | valid `type` |
|---|---|---|
| `scout/vault.py` (enforced) | `type, title, summary, entities, department, sources, last_compiled` | `technique, entity, playbook, concept` |
| `AGENTS.md` §4 (published) | `title, created, updated, type, tags, sources, confidence, …` | `entity, concept, comparison, query, summary, schema` |
| `wiki/SCHEMA.md` (authoritative) | same as `AGENTS.md` | same as `AGENTS.md` |

The vault follows `SCHEMA.md`. The linter enforced a third schema nothing else
used: `summary` appeared on **0** pages, `entities` **0**, `last_compiled` **0**,
`department` **1** — while `tags` (400), `created` (409) and `updated` (351) were
not in its contract at all.

## Decision

`wiki/note-schema.json` (JSON Schema 2020-12, the dialect MCP also uses) is the
page contract. `scout/vault.py` **loads** it; a tree without one keeps the
historical lists unchanged. The body frame moves to an `x-body` block, because
JSON Schema cannot express heading structure.

Three population splits fell out of the evidence and are part of the decision:

- `wiki/raw/` is **immutable evidence**, not authored pages, and is outside the
  contract. Its kinds (`raw`, `raw-provenance`) appear only there.
- `sources` accepts **either** a URL string (authored provenance, 363 pages) or a
  `path`/`loc`/`hint` mapping (the compiled-page address form, read by six live
  modules). Two populations, not one contract with two opinions.
- `TL;DR` is **optional**, per `AGENTS.md` §4's own words. It was required, which
  failed 173 conformant pages.

## Consequences

- **Better:** linting the real vault went from **5139 errors to 161**, and the
  repo's own sample tree is byte-identical in behaviour. Removing the schema
  restores exactly 5139, so adoption is opt-in.
- **Worse:** the reported error count dropped by an order of magnitude, which
  looks like weakening. `leaf-1.6:G4` exists to prove it isn't — a bad page is
  still rejected.
- **Worse:** `VALID_TYPES` and `REQUIRED_FRONTMATTER` remain in the source,
  because four compile-lane modules read them. Two contracts still coexist; they
  are now labelled by lane instead of conflated.
- **Open:** the tree rule (lowercase directories) stays deferred behind B0.
