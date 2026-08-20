# Implementation Plan: V2 System Audit Remediation (22 findings)

Source: `artifacts/superpowers/audit-2026-08-19-v2-system.md`
Branch: `fix/architecture-security-hardening` · Base: `92f5b42`

> The previous occupant of this file (Agent Package Enhancements V5) was archived to
> `artifacts/superpowers/plan-agent-package-v5-2026-08-19.md` before this plan replaced it.

---

### Goal

Close all 22 audited findings (3 Blockers, 8 Majors, 11 Minors) in nine independently
verifiable batches, such that after execution:

1. Every model route the system depends on resolves, and "healthy" means it.
2. No code path fabricates content or reports success without verifying something.
3. The address gate distinguishes a correct hint from a wrong one.
4. Authorization metadata is real, or is honestly documented as absent.
5. Active documentation matches shipped behavior, verified by a test rather than by reading.

---

### Assumptions

1. **Batches are the unit of review.** Each batch ends green on its own gates and is committable
   alone. Batches 1–5 and 7 touch disjoint files and may run in parallel; 6, 8, 9 have stated
   dependencies.
2. **The live stack stays up.** The `snp-memory` project (8 services) and `snp-memory-it`
   integration project remain running for verification. Postgres is reachable on `127.0.0.1:5432`,
   integration Postgres on `55432`.
3. **A working Gemini model exists for this account.** Batch 1 substitutes `gemini-3.6-flash`
   (named in the live 404 body). If that model is also unavailable, Batch 1 step 1 becomes "pick a
   route that returns 200" — the batch is not complete until a route resolves.
4. **Corpus stays small for now.** Fixes must be correct at 22 chunks *and* not degenerate at
   10,000. Where a constant is corpus-relative, it is expressed relative to corpus size.
5. **No history rewrite.** M6 is solved by scanner policy, not by rewriting reachable Git objects.
   The pre-existing history decision recorded in `finish.md` stays the owner's call.
6. **PR-first holds (R-6.4).** No commits to `main`; every batch lands on this feature branch.
7. **Demo corpus stays synthetic.** `raw/` sample data is not replaced; only ungrounded *wiki
   claims* about it are corrected (M7).

---

### Standard gates

Referenced below as **[G]**. Every batch ends with all four green:

```bash
.venv/bin/python -m pytest -m 'not integration' --disable-socket -q -p no:cacheprovider
.venv/bin/ruff check .
.venv/bin/mypy scout scripts
.venv/bin/python scripts/gen_index.py --check
```

Referenced as **[L]** (live gates; export first):

```bash
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=snp_rag
export POSTGRES_QUERY_USER=rag_app_role
export POSTGRES_QUERY_PASSWORD_FILE="$PWD/.secrets/postgres_query_password"
export LITELLM_BASE_URL=http://127.0.0.1:4000/v1
export LITELLM_MASTER_KEY="$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)"
.venv/bin/python scripts/verify_addresses.py    # expect exit 0
```

---

## Plan

### BATCH 1 — Restore live model capability  *(B2)* — **run first, everything downstream needs it**

1. **Repoint the dead model routes** (2–5 min)
   - Files: `.env`, `.env.example`
   - Change: replace `gemini/gemini-2.5-flash` with a resolving model for `LITELLM_LLM_MODEL` and
     `LITELLM_VLM_MODEL`; leave `LITELLM_EMBED_MODEL=gemini/gemini-embedding-001` untouched.
   - Verify:
     ```bash
     docker compose up -d --force-recreate litellm && sleep 15
     K="$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)"
     for m in snp-llm snp-vlm snp-embed; do
       echo -n "$m -> "; curl -s -o /dev/null -w "%{http_code}\n" -X POST \
         http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $K" \
         -H 'Content-Type: application/json' -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}]}"
     done   # snp-llm and snp-vlm must be 200
     ```

2. **Make "healthy" mean the routes resolve** (5–10 min)
   - Files: `docker-compose.yml`
   - Change: replace the LiteLLM healthcheck's `/health/liveliness` probe with one that asserts the
     configured model groups actually resolve (LiteLLM `/health` or a scripted per-group call);
     keep `start_period` generous enough for cold start.
   - Verify: `docker compose up -d litellm && docker compose ps` shows `healthy`; then temporarily
     set `LITELLM_LLM_MODEL` to a bogus model, recreate, and confirm the service reports
     **unhealthy**; restore the good value.

3. **Prove Nhịp B is functional again** (2–5 min)
   - Files: none (verification only)
   - Change: none.
   - Verify:
     ```bash
     .venv/bin/python -c "
     import sys; sys.path.insert(0,'.')
     from pathlib import Path
     from scout.parsers import parse_file
     from scripts.compile_note import generate_model_data
     d=parse_file(Path('raw/architecture/agentic_memory_systems_rfc.md'), Path('.'))
     print(generate_model_data('Audit Smoke Test', d))"
     ```
     Must return structured metadata, not `CompileNoteError`.

---

### BATCH 2 — Stop silent fabrication in ingestion  *(B3, m1)*

4. **`parse_image` must fail instead of inventing a description** (5–10 min)
   - Files: `scout/parsers.py`
   - Change: remove the `except ParserError: extracted_markdown = None` swallow so the error
     propagates; delete the synthetic `"Visual Image Asset: … Size: N bytes …"` fallback, or gate it
     behind an explicit opt-in that stamps `metadata["vlm_status"]="unavailable"`.
   - Verify: `.venv/bin/python -m pytest tests/test_parsers.py -q` plus a new test asserting
     `ParserError` is raised when the vision extractor fails.

5. **Re-ingest the images and confirm real transcription lands** (5–10 min)
   - Files: none (data)
   - Change: force a `sync-job` reindex of `raw/images/`.
   - Verify:
     ```sql
     SELECT d.source_uri, left(c.chunk_text,120) FROM rag_chunks c
     JOIN rag_documents d ON d.doc_id=c.doc_id WHERE d.source_uri LIKE 'raw/images/%';
     ```
     Text must describe image content, **not** contain the string `Visual Image Asset:`.

6. **Refine `loc` when the chunker splits a parsed section** (5–10 min)
   - Files: `scout/chunker.py`, `tests/test_chunker.py`
   - Change: when one `ParsedSection` yields multiple chunks, derive a per-chunk locator
     (e.g. `Rows 4-7`, `Section X (2/3)`) instead of copying the parent `loc` verbatim.
   - Verify: `[G]`, plus a test asserting three chunks from a 10-row CSV section do **not** all
     report `Rows 1-10`.

---

### BATCH 3 — Make the address gate real  *(B1, m2, m7)*

7. **Give `verify_address` a real pass criterion** (5–10 min)
   - Files: `scripts/verify_addresses.py`
   - Change: PASS requires the addressed file at **rank 1** (or top-N with N derived from corpus
     size, not a fixed 5); add a minimum normalized-similarity floor computed from cosine distance,
     not the RRF score. Keep the total 0/1/2 exit contract unchanged.
   - Verify: `[G]`, then the discrimination harness in step 9.

8. **Propagate the criterion to minting and expose it once** (5–10 min)
   - Files: `scripts/mint.py`, `scout/healer.py`
   - Change: keep `mint_address` delegating to the single `verify_address` implementation (no second
     heuristic); surface the new threshold as one named constant both import.
   - Verify: `.venv/bin/python -m pytest tests/test_mint.py tests/test_healer.py -q`

9. **Add a discrimination regression test** (5–10 min)
   - Files: `tests/test_verify_addresses.py`
   - Change: table-driven test over a seeded fake backend asserting PASS for the correct hint and
     **DRIFT/FAIL** for wrong-file vocabulary, unrelated-domain text, and gibberish.
   - Verify: `[G]`. Then live:
     ```bash
     # must now be non-PASS
     hint="zzqq banana marmalade unicycle wobble 8842"
     ```
     against `raw/reports/vllm_high_throughput_serving.pdf`.

10. **Re-mint any address the stricter gate now rejects** (5–10 min)
    - Files: `wiki/**/*.md` (hints only)
    - Change: run `scripts/mint.py` per rejected address and paste the returned block.
    - Verify: `[L]` → `verify_addresses.py` exit **0** with the new criterion; `gen_index.py --check`
      still 13 pages / 0 errors.

11. **Prove the healer can now fire** (5–10 min)
    - Files: none (verification only)
    - Change: none.
    - Verify: inject drift into a **temp copy** of the vault (patch `healer.LOG_FILE` to a temp
      path), run `compute_heals`, and assert ≥1 heal is proposed and re-verification passes.

12. **Harden `apply_heal_edit` and the heal log** *(m7)* (5–10 min)
    - Files: `scout/healer.py`, `tests/test_healer.py`
    - Change: parse `sources[]` from the frontmatter block only (not a whole-file `- path:` regex);
      stop assuming `path:` is the first key of an entry; make `append_heal_to_log` write valid
      7-field frontmatter when creating `wiki/log.md`.
    - Verify: `[G]`, plus tests for (a) a page whose body contains a YAML code block with `- path:`
      and (b) a source entry ordered `hint:` before `path:`.

13. **Decide and enforce the meaning of `Address.loc`** *(m2)* (5–10 min)
    - Files: `scripts/mint.py` **or** `AGENTS.md` §3
    - Change: either validate at mint time that the retrieved chunk's `loc` matches the declared
      `loc`, or state explicitly in the frontmatter contract that `loc` is a human locator that
      retrieval does not honor. Pick one; do not leave it implied.
    - Verify: `[G]`; if validation was chosen, a test asserting a mismatched `loc` fails minting.

---

### BATCH 4 — Close the authorization gap  *(M1)*

14. **Make ingest departments configurable** (5–10 min)
    - Files: `scout/sync_job.py`, `scout/ingest.py`
    - Change: remove the hardcoded `allowed_depts=("all",)` default from `PgVectorDirectIndexer`;
      read a mapping (e.g. `raw/<dept-dir>` → department, or a checked-in `raw/.acl.yaml`), and
      fail closed when a file matches no rule rather than silently defaulting to `all`.
    - Verify: `.venv/bin/python -m pytest tests/test_sync_job.py tests/test_ingest_v2.py -q`

15. **Wire it through Compose and reingest** (5–10 min)
    - Files: `docker-compose.yml`, `.env.example`, `raw/.acl.yaml` (new)
    - Change: pass the mapping into `sync-job`; assign at least two distinct departments across the
      sample corpus so isolation is observable.
    - Verify:
      ```sql
      SELECT DISTINCT allowed_depts FROM rag_documents;   -- more than one row
      ```
      Then, with the `infra`-scoped static token, `rag_fetch` on a document restricted to another
      department must return `no_source`.

16. **Make `DEMO.md`'s fail-closed step actually demonstrable** (2–5 min)
    - Files: `docs/DEMO.md`
    - Change: name the specific document and department the demo uses now that isolation exists.
    - Verify: execute the DEMO steps verbatim; the "token lacking the page department" step must
      visibly withhold the source.

---

### BATCH 5 — Remove verification theatre  *(M2, M8, m8)*

17. **Fix or delete the two "TEST SUCCESS" scripts** (5–10 min)
    - Files: `scripts/test_full_system.py`, `scripts/test_mcp_endpoints.py`
    - Change: preferred — delete both and fold real coverage into `tests/integration/`. If kept:
      add assertions and a nonzero exit on failure; parse the MCP JSON-RPC **body** rather than
      trusting HTTP 200; remove the silent fake fallback; remove the hardcoded `scout-dev-token`;
      remove `FakeEmbedder` from the `--live` path; drop the nonexistent `raw/rfcs/*` fixtures.
    - Verify: run each with services **stopped** — must exit nonzero and must not print
      `TEST SUCCESS`.

18. **Repair or retire `eval_ragas.py`** (5–10 min)
    - Files: `tests/eval_ragas.py`, `pyproject.toml`
    - Change: guard the `datasets` import (or declare it in an `eval` extra); pass a real `Scope`
      to `retrieve` so RLS does not silently return nothing; feed the **system's own** answer
      instead of a hardcoded one; use a configured route (`snp-embed`/`snp-llm`), not
      `gemini/gemini-embedding-2`; read the base URL from env with `/v1`.
    - Verify: `.venv/bin/python tests/eval_ragas.py` returns real metrics, or exits nonzero with a
      named missing prerequisite — never a silent "stopped honestly" on a healthy stack.

19. **Fix the NIAH pass/fail message** *(m8)* (2–5 min)
    - Files: `tests/eval_niah.py`
    - Change: stop printing "Needle retrieved successfully" when the marker is ❌.
    - Verify: force a failing depth and confirm the line reads as a failure.

---

### BATCH 6 — Add the missing groundedness gate  *(M7)* — *depends on Batch 1 and Batch 3*

20. **Correct the ungrounded claims already in the vault** (5–10 min)
    - Files: `wiki/entities/vllm-inference-cluster.md`
    - Change: remove "NVIDIA A100/H100" (unsupported by any file in `raw/`); either cite
      `raw/data/llm_inference_slo_benchmarks.csv` for the p99 claim and scope it to the models the
      CSV actually measures, or drop the claim.
    - Verify: `grep -rn "A100\|H100" raw/` stays empty **and** the page no longer asserts it;
      `gen_index.py --check` green.

21. **Add a faithfulness check to the merge gate** (5–10 min per sub-step; budget 2 steps)
    - Files: `scripts/verify_groundedness.py` (new), `scripts/ci_address_gate.py`,
      `tests/test_verify_groundedness.py`
    - Change: for each page, fetch its `sources[]` context and have `snp-llm` judge whether the
      body's factual claims are supported; emit the same total 0/1/2 exit semantics; wire it into
      the CI gate **after** address verification. Unsupported claims fail with the sentence quoted.
    - Verify: `[G]`; run against the vault — must flag a deliberately inserted false claim and pass
      the corrected page from step 20.

---

### BATCH 7 — Unblock the CI security gate  *(M6)*

22. **Let the scanner recognize the project's own placeholders** (5–10 min)
    - Files: `scripts/scan_secrets.py`, `tests/test_secrets_hygiene.py`
    - Change: allow the existing `^sk-local-dev-[a-z0-9-]+$` / `^sk-placeholder-[a-z0-9-]+$`
      patterns outside `PLACEHOLDER_PATHS` **only** when the value matches a placeholder pattern
      exactly — keeping zero tolerance for real token shapes; alternatively purge placeholders from
      committed content and keep paths strict. Document which policy was chosen in the module
      docstring.
    - Verify:
      ```bash
      .venv/bin/python scripts/scan_secrets.py --all-current --history   # exit 0
      .venv/bin/python -m pytest tests/test_secrets_hygiene.py -q
      ```
      Plus a negative test: a genuine-shaped `sk-` token in any tracked file still fails.

---

### BATCH 8 — Align documentation with shipped behavior  *(M3, M4, M5, m3, m4, m9)* — *depends on Batches 1–5, 7*

23. **Correct the active agent instructions** *(M3, M4)* (5–10 min)
    - Files: `.agent/workflows/snp-verify.md`, `packages/snp-agent/workflows/snp-verify.md`,
      `AGENTS.md` §5
    - Change: replace "score ≥ 0.70" with the criterion Batch 3 actually implements; rewrite the
      "returns empty, silently … retrieval dead-ends" passage to describe real behavior — a
      path-filtered fetch returns the whole file, so the *hint* controls ranking, not existence.
    - Verify: `grep -rn "0\.70" .agent/ packages/ AGENTS.md` returns nothing; a new
      `tests/test_docs_contract.py` case asserts no active doc claims a threshold absent from code.

24. **Re-banner the reversed Gate 4 decision** *(M5)* (5–10 min)
    - Files: `spikes/GATE_RESULTS.md`, `docs/ARCHITECTURE_STATUS.md`, `docs/basic-memory-setup.md`
    - Change: record that the bge-m3 decision was **not** implemented and why; add `GATE_RESULTS.md`
      to the historical inventory (it is currently in neither list); note the measured Vietnamese
      recall consequence. Do not silently rewrite the spike — change its banner.
    - Verify: `.venv/bin/python -m pytest tests/test_docs_contract.py -q`; manual read confirms no
      active document still asserts bge-m3 is deployed.

25. **Refresh unreproducible numbers and stale comments** *(m3, m4, m9)* (5–10 min)
    - Files: `artifacts/superpowers/finish.md`, `docker-compose.yml`
    - Change: replace "94.31% / 17.58×" with the current measured figure and name the command that
      produces it; qualify the "Needle in a Haystack" scenario with its real corpus size; fix the
      `postgres … internal-only` header comment now that the port is published to loopback.
    - Verify: `.venv/bin/python scripts/measure_tokens.py` output matches the number in the doc;
      `grep -n "internal-only" docker-compose.yml` reflects reality.

26. **Decide the wiki-search embedding question** *(follow-on to M5)* (5–10 min)
    - Files: `basic-memory/config.json`, `docs/basic-memory-setup.md`
    - Change: either adopt a multilingual model per Gate 4, or record an explicit, dated decision to
      stay on `bge-small-en-v1.5` with the recall cost accepted.
    - Verify: re-run the three probe queries; `"dual layer memory architecture"` should rank its own
      page **top-3** if a model change was made, or the accepted-cost decision is written down.

---

### BATCH 9 — Agent contract and repo hygiene  *(m5, m6, m10, m11)*

27. **Resolve the `.claude/` ↔ `.agent/` drift** *(m5)* (5–10 min)
    - Files: `.claude/**`, `.gitignore`
    - Change: make `.claude/` a tracked mirror of `.agent/` (or a symlink), so Claude Code and other
      agents read the same contract; currently `.claude/` is untracked and holds the older text.
    - Verify: `diff -rq .claude .agent` clean for shared files; `git status` no longer shows
      `.claude/` as untracked-and-divergent.

28. **Test the mirror equivalence that the docs promise** *(m6)* (5–10 min)
    - Files: `tests/test_agent_package.py`
    - Change: assert byte equality for every file present in both `.agent/` and
      `packages/snp-agent/`, including `package.json` — README and `ARCHITECTURE_STATUS.md` already
      claim this and only the 8 skills are checked today.
    - Verify: `.venv/bin/python -m pytest tests/test_agent_package.py -q`

29. **Document static-token lifecycle and clean stray paths** *(m10, m11)* (2–5 min)
    - Files: `docs/runbook.md`, repo root
    - Change: state that static bearer tokens do not expire and that rotation is file-edit +
      restart; remove the empty `~/`, `.agents/`, `.codex/` directories.
    - Verify: `ls -d '~' .agents .codex 2>&1` reports missing; `[G]`.

---

## Coverage matrix

| Batch | Findings closed |
|---|---|
| 1 Restore live model capability | **B2** |
| 2 Stop silent fabrication | **B3**, m1 |
| 3 Make the address gate real | **B1**, m2, m7 |
| 4 Close the authorization gap | M1 |
| 5 Remove verification theatre | M2, M8, m8 |
| 6 Groundedness gate | M7 |
| 7 Unblock CI security gate | M6 |
| 8 Documentation alignment | M3, M4, M5, m3, m4, m9 |
| 9 Agent contract + hygiene | m5, m6, m10, m11 |

All 3 Blockers, 8 Majors, 11 Minors accounted for (22/22).

**Execution order:** Batch 1 first. Then 2, 3, 4, 5, 7 may run in parallel (disjoint files).
Batch 6 after 1 and 3. Batch 8 after 1–5 and 7. Batch 9 anytime.

---

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Batch 3 turns 19/19 PASS into a wall of failures.** A real criterion may reject many current hints. | Expected, not a regression — it is the finding. Step 10 budgets re-minting. If >half fail, stop and reconsider the threshold before editing pages. |
| **No Gemini model resolves for this account** (Batch 1). | Assumption 3 makes this explicit. Fall back to another configured provider (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are already plumbed through LiteLLM). Batch 1 is not done until a route returns 200. |
| **Batch 2 makes ingestion fail where it used to "succeed."** | That is the point, but it can block `sync-job` startup. Ship step 4 with the explicit `vlm_status` variant if hard failure proves too brittle for the watch loop. |
| **Batch 4 breaks existing retrieval** by restricting documents the demo token can no longer see. | Assign the sample corpus so the `infra` token retains access to the pages used in DEMO/tests; run the live probe in step 15 before committing. |
| **Batch 6 adds an LLM call to the merge gate**, making CI slower and cost-bearing. | Judge only changed pages in PR mode; keep full-vault sweeps on the scheduled job. |
| **Doc edits (Batch 8) drift again.** | Step 23 lands the claim as a *test* in `test_docs_contract.py`, not just prose. |
| **Working tree already carries 171 uncommitted changes.** | Commit each batch separately on this feature branch; never mix a batch with the pre-existing dirty state. Run `git status` before and after each batch. |
| **Overwriting the archived plan.** | Already mitigated: previous plan copied to `plan-agent-package-v5-2026-08-19.md`. |

---

### Rollback plan

- **Per step:** every step is a small, single-purpose edit. `git checkout -- <file>` reverts it;
  no step leaves the repo in a half-migrated state.
- **Per batch:** each batch is one commit on `fix/architecture-security-hardening`.
  `git revert <sha>` undoes exactly one batch without touching the others.
- **Batch 1 (config):** revert `.env` to `gemini/gemini-2.5-flash` and
  `docker compose up -d --force-recreate litellm`. Nothing persistent changes.
- **Batch 2/4 (data-affecting):** re-ingestion is idempotent — `rag_documents.source_uri` is
  `UNIQUE` and upserts. To fully reset: `docker compose down` (keep volumes), revert code,
  `docker compose up -d`, then force a `sync-job` reindex.
- **Batch 3 (wiki hints):** re-minted hints are ordinary text edits under Git;
  `git checkout -- wiki/` restores every previous address.
- **Stop condition:** if any batch cannot reach green on `[G]`, stop, do not proceed to the next
  batch, and switch to `/superpowers-debug` for that batch only.
