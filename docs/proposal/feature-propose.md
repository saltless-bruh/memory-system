# Feature Proposal — Page Form

> **DOMAIN REFERENCE, NOT SNP DEPLOYMENT AUTHORITY** — active proposal. The deployed contract remains the code, `AGENTS.md`, and the target vault's `SCHEMA.md` until this is implemented and verified.

| | |
|---|---|
| **Status** | Proposal, v1.0 |
| **Date** | 2026-09-04 |
| **Requested by** | Repository owner, 2026-09-04 |
| **Originating name** | `Formfillarticle` — renamed, see §2.1 |
| **Extends** | `Technical_Blueprint_V3_Reworked_Architecture.md` §4 (page contract) |
| **Reference corpus** | 433-file Obsidian vault, measured 2026-09-04 |
| **Audience** | Platform engineers, agent authors, the vault owner |

---

## 1. Problem

### 1.1 The contract exists; nothing enforces it

`documentation_standards.instructions.md` states the page contract in six
numbered rules, and closes with rule 6:

> *The current automated checker is narrower than this V3 contract. Record the
> manual schema/heading review rather than overstating automation.*

That sentence is the whole problem written down. `scout/vault.py:lint_page`
enforces a **different, older** contract — seven required frontmatter fields
and `type ∈ {technique, entity, playbook, concept}` — which no page in the
reference vault was ever authored against. So the V3 contract is enforced by
nothing but attention, and attention does not scale to 1–2 GB.

### 1.2 Measured drift

Census run 2026-09-04 through `scout.vault.load_pages` — the production
parser, so these are the figures the ingest path itself sees.

```
432 files parsed  →  329 wiki pages  ·  101 under raw/  ·  3 control documents
```

**Frontmatter is close to healthy.** The target `SCHEMA.md` and the V3
frontmatter block in `AGENTS.md` declare the same fields, so there is no
contract to reconcile — only gaps to fill.

| Field | Present | Missing |
|---|---|---|
| `title` | 95% | 16 |
| `created` | 98% | 5 |
| `updated` | 94% | 20 |
| `type` | 96% | 14 |
| `tags` | 98% | 7 |

Malformed dates: **0**. Values outside the type taxonomy: **2** (both
`source`). Pages with no parseable frontmatter at all: **3**.

**The body is where the drift lives — 0 of 329 pages conform.**

| Slot | State |
|---|---|
| `## Cross-References` | absent on **100%** — but **234 pages (71%)** carry it under another name |
| `## TL;DR` | absent on 97%; 9 pages have it |
| `## Provenance` | 294 pages declare `sources`; 52 carry the heading |
| `[[wikilinks]]` | only 16 pages (5%) have fewer than two — **the graph already exists** |
| interior H2s | 805 distinct headings, **586 appearing exactly once** |
| language | 131 pages (40%) carry Vietnamese headings |

### 1.3 What that last row means

805 distinct headings reads like catastrophic drift. It is not. The V3 frame is

```
# H1
## TL;DR                 recommended
## <free-form sections>  ← the subject's own vocabulary, by design
## Provenance            required when sources are declared
## Cross-References      required
```

The middle is *supposed* to vary. 586 one-off headings — `Cluster position`,
`Trade-offs`, `Architecture` — are the contract working, not failing.

**The real gap is three named slots.** Everything else in the body is either
already correct or deliberately free.

### 1.4 Why a linter alone does not solve it

A checker tells an author their page is wrong after they have written it, in
whatever shape they chose. It converts drift into a backlog. What is missing is
the other half: **a page that cannot be authored in the wrong shape in the
first place.**

---

## 2. Proposal

### 2.1 Name

Proposed as `Formfillarticle`. Renamed to **Page Form** because the design
turns on the form being *one object* rather than one script (§3.1), and the
name has to survive three surfaces:

| Surface | Name |
|---|---|
| Module | `scout/pageform.py` |
| CLI | `snpmemory page new` · `page fill` · `page check` |
| MCP tool | `page_form` |

### 2.2 Statement

> An agent or a human authoring a vault page fills a **form** whose definition
> is the same object that validates the page and gates it in CI. Filling the
> form correctly is the only way to produce a page. A page that does not
> conform cannot be written, and cannot be merged.

---

## 3. Design

### 3.1 One object, three surfaces — the load-bearing decision

```
                       scout/pageform.py
                 ┌────────────────────────────────┐
                 │  class PageForm(BaseModel)     │   ← the single definition
                 │  BODY_FRAME: ordered slot spec │
                 │  parse() · render() · check()  │
                 └───────────────┬────────────────┘
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
   snpmemory page …        page_form MCP tool      page check --all
   (human + operator)      (local stdio server)    (CI, blocks the PR)
         │                       │                       │
         └──── writes the working tree ──────┘     (no push verb anywhere)
```

**The form and the validator must be the same artifact.** A template file plus
separate lint code is two artifacts, and two artifacts diverge — which would
reproduce the drift problem one level up, in the tooling. This is the single
design constraint the proposal will not trade away.

### 3.2 Schema

Pydantic v2, from which the JSON Schema — and therefore the MCP tool's
`inputSchema` — is derived rather than hand-written.

```python
class PageForm(BaseModel):
    model_config = ConfigDict(extra="allow")  # see §3.3

    title: str
    created: datetime.date
    updated: datetime.date
    type: Literal["entity", "concept", "comparison", "query", "summary", "schema"]
    tags: Annotated[list[str], Field(min_length=1)]

    sources: list[SourceRef] = []
    confidence: Literal["high", "medium", "low"] | None = None
    contested: bool = False
    contradictions: list[str] = []
```

Field set and taxonomy are taken from the target vault's `SCHEMA.md`, which
`documentation_standards.instructions.md` rule 1 makes authoritative. The model
does not invent a field the vault owner did not declare.

The body frame is a sibling ordered spec — `H1`, optional `TL;DR`, free-form
interior, `Provenance` (required iff `sources`), `Cross-References` (required,
≥2 outbound `[[wikilinks]]`).

### 3.3 Four rules that make it hold

**`extra="allow"`.** 73 pages carry `aliases`; others carry `stars`, `license`,
`language`, `fork`, `url`. This is GitHub capture metadata and `AGENTS.md`
already requires preserving it. A form that dropped unknown fields would
destroy real data on its first run.

**Round-trip stability.** `render(parse(p)) == p`, byte for byte, for every
conformant page — enforced as a property test over the whole corpus. This is
what makes an unattended bulk migration safe: the tool provably does not touch
what it is not changing.

**Fail closed.** `write` refuses on any validation error and returns errors
keyed to the slot that failed. No partial page ever reaches disk.

**No push verb.** R-6.4 and R-7.3 forbid an agent pushing to a protected
branch. The reliable enforcement is not an instruction telling the agent not
to — it is not giving it the capability. Governance by absent affordance.

### 3.4 Placement — local server, never scout

Contract **C7** / gate **G9** pin the served scout surface to exactly
`wiki_search` and `wiki_read`. Adding an authoring tool there would break a
green gate *and* place a write verb behind a remote bearer token.

`scout/cli/mcp_policy.py` already carries the right pattern: `plan-articles`,
`compile-plan` and `compile-status` are `Exposure.TOOL`; `install-agent` is
`Exposure.HIDDEN` with a written reason. Page Form joins as:

```python
ToolPolicy("page-new", Exposure.TOOL, tool="page_form")
ToolPolicy("page-check", Exposure.TOOL, tool="page_check")
```

Scout stays a read-only authenticated retrieval boundary. Authoring is local,
operates on a working tree, and is reviewed by a human before it reaches
anything.

---

## 4. Interfaces

### 4.1 CLI

| Command | Behaviour |
|---|---|
| `snpmemory page new --type entity --title "…" [--out PATH]` | Writes a skeleton with every slot present and `<!-- fill: … -->` markers. Refuses to overwrite. |
| `snpmemory page fill PATH --set key=value …` | Sets frontmatter fields through the model. Unknown fields preserved. |
| `snpmemory page check PATH` | Validates one page. Exit 0 / 1, errors keyed to slot. |
| `snpmemory page check --all [--vault DIR]` | Corpus census: conformant count, per-slot failures, machine-readable with `--json`. |
| `snpmemory page migrate --dry-run` | Applies the §5 deterministic renames; prints a diff. `--apply` writes. Never touches interior sections. |

### 4.2 MCP — local stdio server

`page_form` with three verbs, so an agent needs no out-of-band knowledge of
the format:

- `describe(type)` → the JSON Schema plus the ordered body frame for that page
  type. **This is how an agent learns the format** — not from a prompt that can
  go stale.
- `validate(content)` → `{ok, errors[]}`, each error naming its slot.
- `write(path, form, body)` → writes only if valid; returns the errors
  otherwise.

### 4.3 Retrieval interaction

Page Form writes the vault. It does not touch the index. The existing chain
still applies: vault write → sync → reindex → search. Page Form's contribution
to retrieval is indirect but large: a populated `## TL;DR` is the first link in
the §5.2 tldr-resolution chain, and the 2026-08-31 ingest recorded
`tldr_source` as **lead-paragraph on 302 pages and explicit on 0** — every
routing snippet in the system today is inferred rather than authored.

---

## 5. Migrating the existing corpus

Two categories, and they must not be mixed in one commit.

### 5.1 Deterministic — 244 heading renames

Same meaning, different word. Reviewable as a diff; no judgment involved.

| Slot | Renames | Observed aliases |
|---|---|---|
| `## Cross-References` | **234** | `Related` ×100, `Liên quan` ×45, `Related pages` ×32, `Links` ×26, `See also` ×21 |
| `## TL;DR` | **7** | `TLDR`, `Summary`, `Tóm tắt`, `Tổng quan` |
| `## Provenance` | **3** | `Sources`, `Nguồn`, `References` |

### 5.2 Judgment — 663 sections to author across 329 pages

| Slot | Pages | Note |
|---|---|---|
| `## TL;DR` | **313** | see §5.3 |
| `## Provenance` | **239** | pages declaring `sources` with no provenance section |
| `## Cross-References` | **95** | no relationship section under any name |
| `[[wikilinks]]` | **16** | fewer than two outbound links |
| frontmatter | ~30 | incl. 3 pages with none and 2 out-of-taxonomy `type` values |

### 5.3 A synonym map that would be wrong

78 of the 313 pages missing a TL;DR carry a near-top section named
`Why it matters`, `What it is`, `Overview`, or `Điểm đáng chú ý`. It is
tempting to rename these. **They must not be renamed.** They are genuine
free-form content that happens to sit near the top; a TL;DR is two to four
assertive self-contained sentences that answer the page. Renaming one into the
other would put a false label on 78 pages and degrade exactly the field the
retrieval snippet depends on.

TL;DR is author-only except for the four exact aliases in §5.1.

### 5.4 Order

1. `page check --all --json` → baseline, committed as evidence.
2. `page migrate --apply` → the 244 renames, one commit, one PR, reviewed as a diff.
3. Frontmatter patches (~30 pages), one commit.
4. Authored sections in batches by `type`, through `page_form`, each batch a PR.
5. `page check --all` → 329/329, recorded against the §1.2 baseline.

Step 2 is hours. Step 4 is the real cost and is bounded by review, not by
compute.

---

## 6. Governance

Two actors, two rules. Conflating them is what makes format enforcement feel
hostile to the person who owns the vault.

| Actor | Path | Rationale |
|---|---|---|
| **Human in Obsidian** | direct edit, direct push | This is workflow W-2. Putting review between the vault owner and his own notes would break the acceptance cycle. |
| **Agent** | `page_form` → working tree → feature branch → PR → human merge | R-6.4 / R-7.3. Enforced by the tool having no push verb, not by instruction. |

The CI gate applies to both, and this is the part that makes "drift will never
be a thing" true rather than aspirational: `page check --all` in
`.gitea/workflows/` **fails any PR that adds or edits a non-conforming page.**
Without it, Page Form is optional, and optional tooling is skipped under
deadline.

Grandfathering: the gate runs in report-only mode until §5 completes, then
flips to blocking. A gate that fails on 329 pre-existing pages on day one gets
disabled on day two.

---

## 7. Acceptance

Each check states an outcome that can fail, and each absence-claim carries a
positive control.

| ID | Outcome | Check |
|---|---|---|
| **F-1** | One definition backs all three surfaces | Mutating a field in `PageForm` changes the CLI error, the MCP `describe` output, and the CI result — asserted in one test. |
| **F-2** | Unknown capture metadata survives | A page with `aliases`, `stars`, `license` round-trips byte-identical. |
| **F-3** | Round-trip is byte-stable | `render(parse(p)) == p` over every conformant page in the corpus. |
| **F-4** | Invalid pages cannot be written | `write` with a missing required slot leaves the target untouched and returns a slot-keyed error. Positive control: the valid twin writes. |
| **F-5** | The agent surface cannot push | No push verb exists in `page_form`; asserted against the served tool list. |
| **F-6** | Scout's surface is unchanged | Gate G9 (`tool-surface`) still passes — exactly `wiki_search` + `wiki_read`. |
| **F-7** | Renames are meaning-preserving | `page migrate --dry-run` touches only the three named slots; no interior heading changes. Positive control: a planted `Why it matters` is **not** renamed. |
| **F-8** | The corpus conforms | `page check --all` reports 329/329 against the §1.2 baseline of 0/329. |
| **F-9** | Drift cannot re-enter | A PR planting a non-conforming page fails CI. Positive control: the conforming twin passes. |

F-9 is the one that matters. The rest is machinery.

---

## 8. Non-goals

- **Not a content generator.** Page Form supplies structure. What goes in a
  TL;DR is authored by a person or an agent that has read the page.
- **Not a frontmatter authority.** The target vault's `SCHEMA.md` is
  authoritative; the model mirrors it and must be updated when it changes.
- **Not an index tool.** It writes the vault. Sync and reindex are unchanged.
- **Does not normalize interior sections.** 586 one-off headings stay.
- **Does not touch `raw/`** (101 files, immutable evidence), `index.md`, or
  `log.md` (authored control documents).

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| A bulk rename corrupts pages | F-3 round-trip test over the whole corpus before `--apply`; step 2 is one reviewable diff. |
| The model drifts from `SCHEMA.md` | A test parses the vault's `SCHEMA.md` frontmatter block and asserts the field set matches `PageForm`. The vault stays authoritative. |
| 313 authored TL;DRs is a large manual cost | Batch by `type` through `page_form`; the gate stays report-only until done, so partial progress is never blocking. |
| The CI gate lands before the corpus conforms and gets disabled | Report-only until §5 completes. Flipping to blocking is an explicit, separate change. |
| Vietnamese pages get anglicised | Renames apply to the three structural slot names only. Interior headings, including all 131 pages of Vietnamese sections, are untouched. |
| Adding a tool erodes the small agent surface | Local server only; scout unchanged; F-6 asserts it. |

---

## 10. Phasing and cost

| Phase | Content | Estimate |
|---|---|---|
| **P0** | `scout/pageform.py`: model, body frame, parse/render/check, round-trip test | 1 day |
| **P1** | CLI: `page new` / `fill` / `check` / `check --all` | 0.5 day |
| **P2** | `page migrate`, dry-run, apply the 244 renames | 0.5 day |
| **P3** | `page_form` MCP tool + `mcp_policy` entries + guardrails | 0.5 day |
| **P4** | CI gate, report-only | 0.5 day |
| **P5** | Authored sections, §5.2 — batched, review-bound | multi-day |
| **P6** | Gate flips to blocking | trivial, gated on P5 |

**This does not fit before the 2026-09-08 demo and should not be forced into
it.** P0–P4 is the durable answer to a 1–2 GB corpus; the demo needs the
retrieval path rebuilt and rehearsed. Recommended start: the first working day
after the demo.

---

## 11. Open decisions

| ID | Decision | Owner |
|---|---|---|
| **DF-1** | Does the vault owner accept the three structural slot names in English on Vietnamese pages, or should the frame accept a declared per-vault alias for each slot? | Vault owner |
| **DF-2** | Is `## TL;DR` promoted from recommended to required once §5 completes? It is the highest-value field for retrieval and currently authored on 9 of 329 pages. | Vault owner |
| **DF-3** | Do the 101 `raw/` files get a reduced form (frontmatter only, no body frame), or stay entirely exempt as immutable evidence? | Platform |
| **DF-4** | Does `page check --all` become a root gate in the V3 ledger, or stay a CI-only check? | Platform |

---

## 12. Relationship to existing documents

Extends `Technical_Blueprint_V3_Reworked_Architecture.md` §4 (page contract) by
supplying its enforcement. Supersedes nothing. Closes rule 6 of
`documentation_standards.instructions.md`, which records that no automation
currently certifies the V3 contract — on completion, that sentence is replaced
by a reference to `page check`.
