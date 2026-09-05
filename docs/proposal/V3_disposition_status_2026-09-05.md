# V3 Component Disposition — Measured Status

> **DOMAIN REFERENCE, NOT SNP DEPLOYMENT AUTHORITY** — a measured status snapshot. The deployed contract remains the code and `AGENTS.md`.

**Supersedes the status glyphs in** `Technical_Blueprint_V3_Reworked_Architecture.md` §8.2.
**Measured:** 2026-09-04 / 2026-09-05, against the live stack and the 433-page served corpus.
Nothing here is read from a report; every number has a command behind it.

## How to read this

**Verdicts** are the blueprint's: 🟩 keep · 🟨 rework · 🟥 remove.

**Rework** carries a percentage — how much of the *target* state exists now.

**Remove** carries a class, which is the thing §8.2 never said:

| Class | Meaning |
|---|---|
| **R-clean** | Delete it. Nothing else needs editing. |
| **R-light** | Delete it, then a small edit elsewhere — one call site or one list entry. |
| **R-heavy** | Delete it, and a **kept** component has to be reworked to survive. |
| **DONE** | Already removed. |

**Substance** — what actually exists behind the name. This is the axis §8.2's
glyphs conflated, and the one that decides what a fix costs:

| Mark | Meaning | What it costs to resolve |
|---|---|---|
| **● REAL** | Implementation exists and meets its target. | nothing |
| **◐ PARTIAL** | Real code, runs, does something — but not the V3 target. | rework existing code **and** whatever depends on its current behaviour |
| **🔌 WIRED** | Real code **and** real config exist. It has never executed. | start it |
| **📄 PAPER** | Exists only in a document. No code, no config, no config entry. | build it from nothing, or delete a sentence |

**⚙️ ENGINE** marks the items the approved plan (`docs/superpowers/plans/2026-09-04-engine-completion.md`) closes. Everything unmarked stays as it is.

---

# The three kinds of zero

Ten rows on this page read "0%". They are not the same problem, and treating
them as one list is how `sync-job` sat undiscovered.

## 📄 PAPER — 0% implemented, nothing exists but the words

Verified: no file, no function, no config entry anywhere in `scout/`,
`scripts/`, `config/` or the compose files.

| Component | Where it is declared |
|---|---|
| Fetch + extract for `sources:` URLs | §8.6 — 727 declared URLs in the corpus, none fetchable |
| Index inspector | §8.6 — *and it is what §8.6 says satisfies H-3* |
| Content-addressed source cache | §8.6 |
| Blue-green embedding migration | §8.6 |
| `read_source` fallback tool | §8.6, §7.6 |
| Similarity graph | §9.2 / leaf-1.4.3 |
| scout MCP **server** description | §8.2 📄 — `FastMCP()` takes no `instructions=` |

Seven items. Between them they have **zero lines of code**. Each is a decision,
not a task: build it, or strike it from the blueprint. Nothing has to be
un-wired first, and nothing depends on them — which is precisely why they are
also the cheapest things on this page to *delete*.

## 🔌 WIRED — 0% live, but the code is written and tested

| Component | State |
|---|---|
| **`sync-job`** ⚙️ | Full service definition at `compose:207` with `restart: unless-stopped`. `scout/sync_job.py` is written, typed and unit-tested. **It has never had a container.** |
| `gitea-runner` | Declared in compose. Never registered. |
| CI `checks.yaml` · `security.yaml` | Both workflows written. **Zero runs on any commit**, because of the row above. |

This is the category that should be alarming. `sync-job` is not unbuilt — it is
finished software nobody started, and its absence is the entire reason W-2 is
open. The distance between "0% PAPER" and "0% WIRED" is the distance between a
paragraph and a `docker compose up`.

## ◐ PARTIAL — more than 0%, and therefore more expensive

Real code that runs and does the *old* job. Reworking it means changing
behaviour something may already depend on — which makes these the costliest
per item, not the cheapest.

| Component | Now | Target |
|---|---|---|
| body structure | **8%** — TL;DR 12/433 · Provenance 91/433 · Cross-References 0/433 | all three on every page |
| model stamp ⚙️ | **94%** — 2176 of 2303 chunks | universal, plus a startup guard |
| frontmatter contract | **98%** — 425/433 carry a fence | full `SCHEMA.md` conformance |
| `verify-vault` | **50%** — lints; address resolution still wired at `:153` | lint only |
| `verify-groundedness` | runs, judges page-prose vs `rag_fetch` raw context | answer vs cited page |
| `references.py` | parses bibliographies out of documents (V2 job) | populate `sources:` from fetched URLs |
| `check` | runs 4 stages including `addresses` | 3 stages |
| 3 eval scripts | measure a side engine | measure the production path |
| `fetch` → `search` + `read` | replacements built; `fetch` never withdrawn | one surface, not two |

**The cost order is the reverse of the percentage order.** A 0% PAPER item is a
sentence. A 0% WIRED item is a command. A 50% PARTIAL item is a rework plus its
blast radius. Percentage measures distance travelled, not distance remaining.

---

## Layer 1 — Knowledge Vault

| Component | Verdict | Status | Evidence |
|---|---|---|---|
| frontmatter contract → `SCHEMA.md` | 🟨 | **◐ PARTIAL · 98%** | 425 of 433 pages open with a frontmatter fence. Full per-field conformance not separately measured. |
| body structure → TL;DR / Provenance / Cross-References | 🟨 | **◐ PARTIAL · 8%** | TL;DR 12/433 · Provenance 91/433 · **Cross-References 0/433** |
| `[[wikilink]]` graph | 🟩 | **works** | 369 of 433 pages carry links; `_WIKILINK_RE` reads the body, not the heading |
| `gen_index.py` generate ⇒ verify | 🟨 | **100%** | coverage floor enforced at `scripts/gen_index.py:187` |
| structured `supersedes:` | 🟥 | **DONE · R-clean** | absent from code |

> **On Cross-References at 0%:** the relationship data already exists. 369 pages
> carry `[[wikilinks]]`; 195 carry them under a differently-named heading
> (`Related`, `Xem thêm`, `See also`). This is a generation-plus-rename job of
> hours, not the multi-day migration it looks like. The engine is unaffected —
> it reads links from the body regardless of heading.

## Layer 2 — Data Vault

| Component | Verdict | Status | Evidence |
|---|---|---|---|
| schema, `vector(1024)`, HNSW + tsvector | 🟩 | **works** | migrations 001–007 applied |
| hybrid retrieval (dense + sparse, RRF) | 🟩 | **works** | recall@1 0.80 · recall@3 1.00 · recall@5 1.00 |
| tsv config `english` → `simple` | 🟨 | **100% functional, spec deviation** | migration 007 **added** `tsv_simple` and OR-fused it rather than replacing `english`. Arguably better — keeps English stemming — but it is not what §8.2 specifies, and the deviation was never recorded. |
| Row-Level Security, 2 roles, INSERT-only audit | 🟩 | **works** | `rag_app_role`, `rag_ingest_role`, 6 policies |
| parsers: PDF · Markdown/text · CSV/TSV · source · images | 🟩 | **works** | markdown path in service |
| figure/table extraction | 🟨 | **target met (parked)** | disabled by decision pending D-5; target *is* parked-intact |
| bibliography lift (`references.py`) | 🟨 | **◐ PARTIAL · 0% toward target** | still parses bibliographies out of documents; target is populating `sources:` from fetched URLs. Not in the deployed image. |

## CLI — 27 commands (should be 23)

| Group | Command | Verdict | Status |
|---|---|---|---|
| stack | `status` `logs` `up` `down` `init` | 🟩 | **works** |
| ingest | `ingest` | 🟩 | **works** |
| ingest | `ingest-wiki` | 🆕 | **⚙️ ENGINE** — new shipped surface for `ingest_wiki()` |
| compile | `compile` `compile-plan` `compile-cancel` `plan-articles` `compile-status` | 🟩 | **works — but see `mint` below** |
| addressing | `fetch` | 🟨 | **replacement 100%, retirement 0%** — `search` and `read` both exist and work; `fetch` was never withdrawn |
| addressing | `mint` | 🟥 | **R-heavy** ⚠️ |
| addressing | `heal` | 🟥 | **R-clean** |
| addressing | `gate` | 🟥 | **R-clean** |
| verify | `verify-secrets` | 🟩 | **works** |
| verify | `verify-vault` | 🟨 | **◐ PARTIAL · 50%** — lints frontmatter; address resolution still wired at `verify.py:153` |
| verify | `verify-groundedness` | 🟨 | **◐ PARTIAL · 0% toward target** — still judges page-prose vs `rag_fetch` raw context (`:524`), not answer vs cited page |
| verify | `check` | 🟨 | **blocked** — `DEFAULT_STAGES` at `verify.py:260` still runs the `addresses` stage |
| verify | `verify-addresses` | 🟥 | **R-light** |
| read/search | `read` | 🟩 | **works** |
| read/search | `search` | 🟨 | **100%** — same pgvector engine as the agent |
| governance | `propose` | 🟩 | **works** |
| agent | `schema` `mcp-config` `install-agent` | 🟩 | **works** |
| agent | `mcp` | 🟨 | **not measured this pass** |

## MCP servers

| Component | Verdict | Status | Evidence |
|---|---|---|---|
| scout: `rag_fetch` → `wiki_search` + `wiki_read` | 🟨 | **100%** | both registered, authenticated, both auth branches |
| scout: 📄 empty server description | 🟨 | **📄 PAPER · 0%** | `FastMCP(name, auth=…, mask_error_details=…, lifespan=…)` at `mcp_server.py:120` — still no `instructions=`. The **tools** carry full R-8.5 descriptions; the **server** carries none. |
| snpmemory: `verify` `plan_articles` `compile_plan` `compile_status` | 🟩 | **works** |
| snp-wiki server, `search_notes`, `list_notes` | 🟥 | **DONE · R-clean** | gone, plus a guard (`export_mcp_config.py:30`) and a namespace test |
| `read_note` / `write_note` (D-2 transitional) | 🟨 | **100% (retired)** | `wiki_read` is proven; the transitional retention is no longer needed |

## Agent package

| Component | Verdict | Status |
|---|---|---|
| 8 skills · 6 workflows · 10 instructions · 1 rule | 🟨 | **100%** — R-5.1, R-6.3 and the Golden Rule absent from every mirror; tool names updated |
| `snp-auto-heal-vault`, `workflows/snp-heal.md` | 🟥 | **DONE · R-clean** |
| `install-agent.sh`, mcp-config generator | 🟩 | **works** |

## Infrastructure — 8 compose services

| Service | Verdict | Status | Evidence |
|---|---|---|---|
| postgres · litellm · host-sync · git · postgres-migrate | 🟩 | **running, healthy** | `docker ps` |
| **sync-job** | 🟩 | **🔌 WIRED · 0% live — declared, never had a container** ⚙️ ENGINE | `compose:207` carries `restart: unless-stopped`; `docker ps -a` returns nothing. **This is why W-2 is open.** |
| scout — tool surface changes, container does not | 🟨 | **100%** | live tool surface verified from inside the container |
| basic-memory | 🟥 | **file DONE · runtime R-clean** ⚙️ ENGINE | gone from compose; an **orphan container from 2026-08-18 is still running**, still holding the FastEmbed@384 space |
| gitea-runner | 🟨 | **🔌 WIRED · 0% live** | declared, never started |

## Verification chain

| Component | Verdict | Status |
|---|---|---|
| vault lint · secret scan | 🟩 | **works** |
| groundedness judging (50/day cap) | 🟨 | **0%** — compares the wrong pair |
| address resolution · closed-loop address gate | 🟥 | **R-light** — one entry in `DEFAULT_STAGES` |
| capability fingerprint boundary | 🟩 🟡 | **not measured this pass** |

## Release & ops

`release_backup` · `write_release_manifest` · `preflight_stack` · `release_preflight` — 🟩, untouched by this branch, not re-exercised this pass.

## CI

| Component | Verdict | Status |
|---|---|---|
| `checks.yaml` · `security.yaml` | 🟩 | **🔌 WIRED · never executed on any commit** — no runner, so `/data/gitea/actions_log` is empty |
| `auto-healer.yaml` | 🟥 | **R-clean** |

## Tests

| Component | Verdict | Status |
|---|---|---|
| offline suite | 🟩 | **green — 1387 passed, 29 deselected** |
| 3 eval scripts → repoint at production path | 🟨 | **◐ PARTIAL · 0% toward target** — `eval_hard_negatives`, `eval_niah`, `eval_ragas` unchanged. `artifacts/v3/retrieval_quality.py` is a **new fourth**, not a repoint. |
| `test_mint` (17) · `test_ci_address_gate` (28) · `test_healer` (16) | 🟥 | **go with their modules — 61 tests** |

## New components (§8.6) — 8 of 13 built

**Built:** markdown chunking · snippet-bounded payload (40 words) · canonical read envelope + 4 modes · page-level grouping and rerank · per-class rank fusion · degradation path · session dedup (`seen`) · model/dimension stamp **94%** ⚙️ ENGINE *(2176 of 2303 chunks stamped; no startup guard)*

**📄 PAPER — zero lines of code:** fetch + extract for `sources:` URLs (727 declared URLs, none fetchable) · index inspector · content-addressed source cache · blue-green embedding migration · `read_source` fallback tool

---

# The removal classes, in full

This is the part §8.2 never stated, and it is where the surprise is.

## R-clean — delete, nothing else breaks

| Target | Only importer | Test fallout |
|---|---|---|
| `scripts/ci_address_gate.py` | `cli/commands/ci.py:47` — the `gate` command, removed with it | `test_ci_address_gate.py` (28) |
| `scout/healer.py` | `cli/commands/ci.py:108` — the `heal` command, removed with it | `test_healer.py` (16) |
| `.gitea/workflows/auto-healer.yaml` | none | none |
| `docs/proposal/Technical_Blueprint_Auto_Healer_CICD.md` | none | none |
| orphan `basic-memory` container | none | none — `docker rm -f` |

Each is a closed loop: the module and its only caller are both on the removal list.

## R-light — delete, then one small edit

**`scripts/verify_addresses.py`** has three importers. Two (`healer.py:16`, `mint.py:60`) are themselves being removed. The one that survives is the ordered chain:

```python
# scout/cli/commands/verify.py:260
DEFAULT_STAGES = (
    ("vault", verify_vault),
    ("secrets", verify_secrets),
    ("addresses", verify_addresses),  # ← delete this line
    ("groundedness", verify_groundedness),
)
```

Delete that tuple entry and `check` drops from four stages to three. One line, plus `tests/test_cli_verify.py`. This is exactly what §8.3 meant by *"**Split the chain rather than deleting it**: vault lint and secret scan are unaffected"* — an instruction that was written and never carried out.

## R-heavy — delete, and a kept component must be reworked

**`scripts/mint.py`.** Four importers. Two are being removed with it (`healer.py:14`, `cli/commands/authoring.py:68`). **Two are 🟩 keep, and they are the compile pipeline:**

```
scripts/compile_plan.py:58   from scripts.mint import MintStatus, mint_address
                     :147        outcome = await mint_address(…)
                     :157        ok = outcome.status is MintStatus.MINTED and …

scripts/compile_note.py:40   from scripts.mint import MintResult, MintStatus, mint_address
                      :648       def …(result: MintResult, *, path, department, loc)
                      :651       if result.status is not MintStatus.MINTED or …
                      :798       result = await mint_address(…)
```

Minting is not decoration there — it is the **success criterion**. `compile_plan` gates each article on `MintStatus.MINTED`; `compile_note` has a function whose entire job is turning a `MintResult` into page metadata. Removing `mint.py` means rewriting what "a compiled page succeeded" means.

The blueprint anticipated this in one clause — *"`snp-compile-wiki` 🟩 … Only its minting step is removed"* — and nowhere costed it. It is the single removal with real blast radius into kept code, and it also pulls `tests/test_compile_note.py` and `tests/test_cli_authoring.py`.

**Practical consequence:** the four zombie CLI commands are *not* one half-day job. `heal`, `gate` and `verify-addresses` are cheap. **`mint` is a compile-pipeline rework** and should be planned separately.

---

# Summary

**By verdict**

| | Count |
|---|---|
| 🟩 keep, working | 21 |
| 🟩 keep, **not deployed** | 2 — `sync-job`, CI `checks`/`security` |
| 🟨 rework complete | 8 |
| 🟨 rework partial | 9 |
| 🟥 removed already | 6 |
| 🟥 **R-clean** | 5 |
| 🟥 **R-light** | 1 |
| 🟥 **R-heavy** | 1 — `mint`, into the compile pipeline |
| 🆕 built | 8 of 13 |

**By substance — what actually exists**

| Mark | Count | Cost to resolve |
|---|---|---|
| ● REAL | 29 | none |
| ◐ PARTIAL | 9 | rework + blast radius — **most expensive per item** |
| 🔌 WIRED, never run | 3 | `docker compose up` |
| 📄 PAPER, zero lines of code | 7 | build from nothing, or strike from the blueprint |

The two columns disagree, and that is the point. `sync-job` and the index
inspector both read 0%. One is finished software nobody started; the other is a
paragraph. `verify-vault` reads 50% and is harder than either.

**Two blueprint corrections.** §8.5 lists `scout/backends/pgvector.py` and `scout/ingest.py` as *retained without change*; this branch changed them by +212/−56 and +17. §8.7's "Retained unchanged: 70%" pie is therefore wrong.

**The engine plan touches five rows**, all marked ⚙️ ENGINE: `sync-job` 0% → running, the orphan container, the model stamp 94% → 100%, `ingest-wiki` as a new surface, and the behavioral gates that measure the whole path. Every other row on this page stays exactly as it reads today.
