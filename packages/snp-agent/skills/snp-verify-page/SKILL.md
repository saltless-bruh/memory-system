---
name: snp-verify-page
description: "Use this skill to check one draft or revised wiki page against the vault's page contract before opening a pull request. It runs a script that returns a named receipt — filename, frontmatter, schema, required headings, wikilink resolution, provenance — and states what it did not check. Use it for a single page; use snp-verify-vault to review a whole vault by reading."
---

# Check one page against the contract

```bash
scripts/verify_page.py path/to/page.md
```

The script resolves the vault's own `note-schema.json` by walking up from the
page, so it checks the contract *that vault* holds rather than a remembered
one. Pass `--schema` to override.

## The contract it checks

The body frame is AGENTS.md section 4, the same one `verify-vault` enforces:
an H1 title; `## Cross-References` once; `## Provenance` once before it when
the page declares `sources:`; `## TL;DR` recommended, and when present once and
first. Headings are checked for order and duplication, not mere presence. A
`[[Page#Section|alias]]` link resolves to `Page`. The lowercase-hyphen filename
rule covers new pages; an existing page such as `AFFiNE.md` keeps its name, and
outside git the rule is reported under `degraded`.

## Read the receipt, not the exit code alone

```json
{"schemaVersion": 1, "ok": true, "command": "verify-page",
 "checkCount": 10, "degraded": [], "notChecked": ["groundedness: …"]}
```

- `checks[]` names every rule that ran and how it decided.
- `degraded[]` names a rule that **could not** run — an absent schema means the
  field contract was not validated, which is not the same as it passing.
- `notChecked[]` names what this script never decides: groundedness, whether
  each `sources[]` hint still retrieves its file, index freshness, prose
  quality, and link density.

`ok: true` therefore means *these checks passed*, never *this page is correct*.
Report it that way, and name anything in `degraded` when you do.

## Exit status

`0` every check passed · `1` a check failed · `2` the page could not be read,
which is never a pass.

## After a failure

Fix the page and re-run. Changes reach the vault on a feature branch through a
human-reviewed pull request (R-6.4, R-7.3); this skill never pushes.
