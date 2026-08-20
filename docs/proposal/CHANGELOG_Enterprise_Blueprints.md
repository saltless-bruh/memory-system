# CHANGELOG — Enterprise Blueprints Revision (v1.0 → v1.1)

> **HISTORICAL CHANGELOG.** Preserved for design provenance; it is not current
> operational guidance. See `docs/ARCHITECTURE_STATUS.md`.

**Scope:** `Technical_Blueprint_Enterprise_Knowledge_Vault.md` and `Technical_Blueprint_Enterprise_Data_Vault_and_RAG.md`.
**Purpose:** every change below lists **(a)** the problem the old version introduced, **(b)** what changed, **(c)** how it differs from the old version, and **(d)** the benefit.
**Guiding principle of this revision:** make the two enterprise blueprints agree with the already-reviewed component specs (`V2_RAG`, `Basic_Memory_Gitea`, `Auto_Healer_CICD`) and with the project's own empirical spike results, so an implementer following either layer builds the same, correct system.

---

## Summary Table

| # | Blueprint | Change | Severity |
|---|-----------|--------|----------|
| 1 | Both | Embedding `bge-small-en-v1.5` (384) → **`bge-m3` (1024)** | **Critical** (recall) |
| 2 | Data Vault | `SET LOCAL … = $1` → **`set_config(…, $1, true)`** | **Critical** (runtime error) |
| 3 | Data Vault | Single `department TEXT` → **`allowed_depts TEXT[]` + `&&` overlap** | **High** (can't model shared docs) |
| 4 | Data Vault | `coalesce(…, 'general')` → **true fail-closed (NULL → no rows)** | **High** (fail-open) |
| 5 | Data Vault | Claimed-but-absent RRF → **RRF hybrid actually implemented** | **Medium** (spec vs code) |
| 6 | Data Vault | FTS config `'english'` → **`'simple'`** | Medium (VN + exact terms) |
| 7 | Data Vault | Added **RLS hardening** (no `BYPASSRLS`, PgBouncer transaction-local) | **High** (silent RLS leak) |
| 8 | Data Vault | Added **honest egress + ZDR/NDA** section | **High** (compliance) |
| 9 | Both | Added **component-spec authority** header + coherence lint rec | Medium (doc drift) |
| 10 | Both | Naming aligned to `rag-brdg` / bge-m3; healer flow matches shipped `--ci` | Low (consistency) |

Out of scope for these two files (tracked elsewhere): the committed **leaked key** (see `SECRET_SCRUB_RUNBOOK.md`) and the **`curl … | bash` installer** in the Agent Package blueprint.

---

## Change 1 — Embedding model: `bge-small-en-v1.5` (384) → `bge-m3` (1024)
*(Both blueprints: Knowledge Vault §7; Data Vault §3.1, §4, §2)*

- **Problem the old version introduced:** both blueprints specified `BAAI/bge-small-en-v1.5` at 384 dimensions. That is an **English-only** model, and your **own Gate 4 spike** measured it failing on Vietnamese — recall@3 **0.812**, ranking the ESC8 paraphrase **7th** (behind `archive` and `log`) — versus **1.0** for `bge-m3`. Your corpus is Vietnamese security reports, so this would have silently degraded retrieval on the exact queries that matter. It also **contradicted `Technical_Blueprint_V2_RAG.md`**, which already uses `bge-m3` / `vector(1024)` — the two docs disagreed on vector dimension, which is baked into the schema and HNSW index.
- **What changed:** standardized on `BAAI/bge-m3` (1024-dim) everywhere — the Knowledge Vault's in-process FastEmbed model and the Data Vault's schema (`vector(1024)`), ingest embedder, and topology.
- **How it differs:** the dimension changed 384 → 1024, and the model changed from an English monolingual to a multilingual one; both layers now match each other and the component spec.
- **Benefit:** correct Vietnamese recall (the property you actually benchmarked), a consistent vector dimension across all V2 docs, and — because the wiki and RAG now share the bge-m3 family — a hint minted in the RAG is embedded the same way the wiki was searched.
- **Carried caveat (added to the doc):** confirm `basic-memory`/FastEmbed can be pointed at `bge-m3` (if it pins the model, use the DIY fallback engine or configure FastEmbed); after switching, **re-index** and re-run the Gate-4 check against FastEmbed-hosted bge-m3.

## Change 2 — RLS session variable: `SET LOCAL … = $1` → `set_config(…, $1, true)`
*(Data Vault §3.2)*

- **Problem:** the old code was `await conn.execute("SET LOCAL scout.current_depts = $1", …)`. PostgreSQL `SET` is a **utility statement that does not accept bind parameters**; via the extended protocol this throws a syntax error. **Every RLS-gated retrieval would have failed at runtime.**
- **What changed:** replaced with `SELECT set_config('scout.current_depts', $1, true)` — the parameter-safe, transaction-local equivalent (identical to the `V2_RAG` adapter).
- **How it differs:** the value is now passed as a real bound parameter to a function that accepts one, and it is explicitly transaction-scoped (`true`).
- **Benefit:** the query actually runs; and because it's a bound parameter, there is no string-interpolation injection surface on the security-context line.

## Change 3 — Access model: single `department TEXT` → `allowed_depts TEXT[]` + array overlap
*(Data Vault §3.1, §4)*

- **Problem:** the chunk carried **one** `department`, and the policy tested `department = ANY(caller_depts)`. A document is frequently relevant to **several** departments (e.g., a report both `redteam` and `blueteam` need). The single-column model cannot represent that — you'd have to **ingest the file twice** — and it contradicted the `allowed_depts TEXT[]` model in `V2_RAG`.
- **What changed:** `allowed_depts TEXT[] NOT NULL CHECK (cardinality > 0)`, a GIN index on it, and an RLS policy using the **array-overlap operator** `allowed_depts && caller_depts`. Ingestion sets the set from the uploader's grant.
- **How it differs:** a chunk now belongs to a *set* of departments and a caller carries a *set*; visibility is set-intersection, not single-value membership.
- **Benefit:** shared-across-department documents are one row-set (no duplication, no divergence), peer-department need-to-know still holds, and the two layers now use the same access primitive.

## Change 4 — Fail-closed default: `coalesce(…, 'general')` → NULL → no rows
*(Data Vault §3.1)*

- **Problem:** the old policy wrapped the setting in `coalesce(nullif(current_setting(…), ''), 'general')`. Despite being labeled "Fail-Closed," an **unset** session variable defaulted to `'general'`, granting the caller **every `general` document**. If the adapter were ever bypassed or the variable not set, that's a silent fail-**open** to the general tier.
- **What changed:** removed the `coalesce('general')` fallback. `current_setting('scout.current_depts', true)` returns NULL when unset; `allowed_depts && string_to_array(NULL, ',')` evaluates to NULL → the row is not visible.
- **How it differs:** unset context now yields **zero rows** instead of the general tier.
- **Benefit:** genuinely fail-closed — a missing security context denies everything, so a bypass or bug can't leak documents.

## Change 5 — RRF hybrid: claimed but absent → actually implemented
*(Data Vault §1, §3.2, §5)*

- **Problem:** §1 and §5 promised "Reciprocal Rank Fusion combining vector cosine and BM25," and §3.1 created a `tsvector` column + GIN index — but the actual query was **pure vector** (`ORDER BY embedding <=> $1`). The full-text index was dead weight and the RRF claim was unmet; anyone building to the spec would ship something different from the doc.
- **What changed:** a real hybrid query — a vector-arm CTE (bge-m3 cosine, `ROW_NUMBER` rank) and a full-text-arm CTE (`ts_rank` over `plainto_tsquery`), `FULL OUTER JOIN` on chunk `id`, fused with `1/(k + rank)` RRF and ordered by the fused score. RLS applies to both arms.
- **How it differs:** the tsvector index is now used; ranking is a fusion of semantic + exact-term signals rather than vector-only.
- **Benefit:** exact technical tokens (`ESC8`, CVE IDs, tool names) that pure vector search can miss are recovered by the full-text arm, while bge-m3 keeps semantic/multilingual recall — and the code finally matches the claim.

## Change 6 — Full-text config: `'english'` → `'simple'`
*(Data Vault §3.1, §3.2)*

- **Problem:** `to_tsvector('english', content)` applies English stemming and stopword removal. On Vietnamese text that's meaningless-to-harmful, and even for English it collapses exact security tokens you want to match verbatim.
- **What changed:** the generated column and the query's `plainto_tsquery` both use `'simple'` (tokenize + lowercase, no stemming/stopwords).
- **How it differs:** no language-specific stemming is applied; tokens match as written.
- **Benefit:** `ESC8` matches `ESC8`, `CVE-2026-1234` matches exactly, and Vietnamese isn't mangled by an English stemmer — appropriate since the vector arm already carries semantics.

## Change 7 — Added RLS operational hardening
*(Data Vault §3.1 callout)*

- **Problem:** the old blueprint enabled RLS but omitted the two ways it silently leaks in exactly this stack: (a) a superuser/`BYPASSRLS` connection ignores all policies; (b) with **PgBouncer transaction pooling** (which the blueprint uses), a plain `SET` persists on a pooled connection and bleeds one client's departments onto the next.
- **What changed:** added an explicit hardening callout — connect as a **non-superuser role without `BYPASSRLS`**, and set the variable **transaction-locally** (`set_config(..., true)`) inside the same transaction as the query.
- **How it differs:** the old doc implied "RLS on = safe"; the new doc names the preconditions that make RLS actually hold under pooling.
- **Benefit:** closes two realistic, hard-to-spot cross-department leak paths before they ship.

## Change 8 — Added honest egress + ZDR/NDA section
*(Data Vault §7; §1 egress note; topology label)*

- **Problem:** the old Data Vault blueprint didn't state that ingestion embeddings/VLM traverse the company LiteLLM gateway to **cloud** providers — i.e., that **raw report content leaves your infrastructure**. Combined with "enterprise/secure" framing, that's the same air-gap-vs-cloud dishonesty corrected in the other blueprints, and it hides a real client-confidentiality exposure.
- **What changed:** a dedicated §7 stating the Data Vault is **not air-gapped**, plus the prerequisite that replaces "no egress": zero-data-retention/no-training provider terms, NDA-permitted subprocessors, and proxy body-logging off. Added an egress-minimization recommendation (local text embedder; cloud only for genuine VLM parsing).
- **How it differs:** egress is now explicit and paired with the compliance gate, rather than implied-absent.
- **Benefit:** the security posture is accurate, the one hard prerequisite is written down where implementers will see it, and there's a concrete path to shrink cloud exposure.

## Change 9 — Component-spec authority + coherence lint
*(Both: headers; Knowledge Vault §3 callout)*

- **Problem:** six overlapping blueprints had begun to **contradict** each other on load-bearing details (dimension, RLS model, `SET` syntax). Nothing declared which document was authoritative, so drift was inevitable.
- **What changed:** each enterprise blueprint's header now names its **component spec as authoritative** ("this is the scaled-deployment view; the component doc governs the contract"). Added a recommendation to lint that a department's wiki page only references Data Vault docs whose `allowed_depts` include that department (the wiki↔RAG coherence invariant).
- **How it differs:** the docs now have an explicit hierarchy and a mechanical coherence check instead of parallel, drifting truth.
- **Benefit:** future edits have a single source of truth to reconcile against, and the wiki/RAG access coherence is checkable rather than assumed.

## Change 10 — Naming + healer-flow consistency
*(Both, throughout)*

- **Problem:** the enterprise blueprints used the retired name "Scout," and the Knowledge Vault's healer sequence still showed the healer committing/pushing directly (the double-commit shape), inconsistent with the shipped PR-first `--ci` healer.
- **What changed:** "Scout" → `rag-brdg` where it denotes the bridge; the Knowledge Vault §6 healer sequence now shows *apply-to-PR-branch → lint gate → single workflow commit → human review*, matching the shipped `scout/healer.py --ci` and reconciled workflow.
- **How it differs:** terminology and the healer flow now match the current code and the other blueprints.
- **Benefit:** no "which Scout / which healer flow" confusion; the docs describe what actually runs.

---

## Not changed (and why)
- **The genuinely strong parts were kept as-is:** the zero-credential `basic-memory` design, `host-sync` with `_sync_lock` + bare mirror, the HMAC webhook, Patroni/PgBouncer/pgBackRest HA-DR, the S3/MinIO raw decoupling, the `/v2/ingest` async pipeline, and the injection-firewall schema. These were correct and are untouched except where a fix above intersected them.
- **Leaked key** and the **`curl | bash` installer** are real but belong to other files (`SECRET_SCRUB_RUNBOOK.md`; the Agent Package blueprint) — not edited here to keep this revision scoped to the two enterprise blueprints.
