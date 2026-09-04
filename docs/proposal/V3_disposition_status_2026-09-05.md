# V3 Component Disposition — Measured Status

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

**⚙️ ENGINE** marks the items the approved plan (`docs/superpowers/plans/2026-09-04-engine-completion.md`) closes. Everything unmarked stays as it is.

---

## Layer 1 — Knowledge Vault

| Component | Verdict | Status | Evidence |
|---|---|---|---|
| frontmatter contract → `SCHEMA.md` | 🟨 | **98%** | 425 of 433 pages open with a frontmatter fence. Full per-field conformance not separately measured. |
| body structure → TL;DR / Provenance / Cross-References | 🟨 | **8%** | TL;DR 12/433 · Provenance 91/433 · **Cross-References 0/433** |
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
| bibliography lift (`references.py`) | 🟨 | **0%** | still parses bibliographies out of documents; target is populating `sources:` from fetched URLs. Not in the deployed image. |

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
| verify | `verify-vault` | 🟨 | **50%** — lints frontmatter; address resolution still wired at `verify.py:153` |
| verify | `verify-groundedness` | 🟨 | **0%** — still judges page-prose vs `rag_fetch` raw context (`:524`), not answer vs cited page |
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
| scout: 📄 empty server description | 🟨 | **0%** | `FastMCP(name, auth=…, mask_error_details=…, lifespan=…)` at `mcp_server.py:120` — still no `instructions=`. The **tools** carry full R-8.5 descriptions; the **server** carries none. |
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
| **sync-job** | 🟩 | **0% — declared, never had a container** ⚙️ ENGINE | `compose:207` carries `restart: unless-stopped`; `docker ps -a` returns nothing. **This is why W-2 is open.** |
| scout — tool surface changes, container does not | 🟨 | **100%** | live tool surface verified from inside the container |
| basic-memory | 🟥 | **file DONE · runtime R-clean** ⚙️ ENGINE | gone from compose; an **orphan container from 2026-08-18 is still running**, still holding the FastEmbed@384 space |
| gitea-runner | 🟨 | **0%** | declared, never started |

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
| `checks.yaml` · `security.yaml` | 🟩 | **present · never executed on any commit** — no runner, so `/data/gitea/actions_log` is empty |
| `auto-healer.yaml` | 🟥 | **R-clean** |

## Tests

| Component | Verdict | Status |
|---|---|---|
| offline suite | 🟩 | **green — 1387 passed, 29 deselected** |
| 3 eval scripts → repoint at production path | 🟨 | **0%** — `eval_hard_negatives`, `eval_niah`, `eval_ragas` unchanged. `artifacts/v3/retrieval_quality.py` is a **new fourth**, not a repoint. |
| `test_mint` (17) · `test_ci_address_gate` (28) · `test_healer` (16) | 🟥 | **go with their modules — 61 tests** |

## New components (§8.6) — 8 of 13 built

**Built:** markdown chunking · snippet-bounded payload (40 words) · canonical read envelope + 4 modes · page-level grouping and rerank · per-class rank fusion · degradation path · session dedup (`seen`) · model/dimension stamp **94%** ⚙️ ENGINE *(2176 of 2303 chunks stamped; no startup guard)*

**Absent:** fetch + extract for `sources:` URLs (727 declared URLs, none fetchable) · index inspector · content-addressed source cache · blue-green embedding migration · `read_source` fallback tool

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
    ("addresses", verify_addresses),   # ← delete this line
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

| | Count |
|---|---|
| 🟩 keep, working | 21 |
| 🟩 keep, **not deployed** | 2 — `sync-job`, CI `checks`/`security` |
| 🟨 rework at 100% | 8 |
| 🟨 rework in progress | 3 — frontmatter 98% · model stamp 94% · `verify-vault` 50% |
| 🟨 rework at 0% | 6 — body structure 8% · `references.py` · `verify-groundedness` · `check` · `gitea-runner` · eval scripts · server description |
| 🟥 removed already | 6 |
| 🟥 **R-clean** | 5 |
| 🟥 **R-light** | 1 |
| 🟥 **R-heavy** | 1 — `mint`, into the compile pipeline |
| 🆕 built | 8 of 13 |

**Two blueprint corrections.** §8.5 lists `scout/backends/pgvector.py` and `scout/ingest.py` as *retained without change*; this branch changed them by +212/−56 and +17. §8.7's "Retained unchanged: 70%" pie is therefore wrong.

**The engine plan touches five rows**, all marked ⚙️ ENGINE: `sync-job` 0% → running, the orphan container, the model stamp 94% → 100%, `ingest-wiki` as a new surface, and the behavioral gates that measure the whole path. Every other row on this page stays exactly as it reads today.
