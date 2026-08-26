# Plan — the Codex audit items, and the capability boundary I conceded

**Date:** 2026-08-26 · **Branch:** `fix/architecture-security-hardening`
**Scope:** the seven items handed off in `docs/conversation.md`, plus the
host/container ingestion divergence Codex pushed back on and I accepted, plus
one operational gap found while causing it. Nothing from the Tier 4–6 backlog.

---

## What the 2026 research changed

### F-1 — "Advisory" applies to a verdict. It must never apply to infrastructure

The fail-closed literature is unambiguous: *"any unhandled error exits with a
non-zero code, preventing false passes when the evaluation service is
unreachable"*, and *"a pipeline should treat every nonzero code as blocking."*
Modern gates do keep an advisory tier — an `UNCLEAR` verdict that routes to
manual approval rather than blocking — but that tier applies to the **verdict**,
and gates are *"triggered on verdict/exit_code, not on the presence or absence of
specific issues."*

`scripts/ci_address_gate.py:79` collapses both:

```python
exit_code = runner(command, "groundedness check")
if exit_code == 0:
    return 0
if not enforce:
    print(...)
    return 0  # <- swallows 1 AND 2
return exit_code if exit_code in {1, 2} else 2
```

Exit 1 (pages unsupported) is a verdict and may legitimately be advisory. Exit 2
(the judge could not run) is an infrastructure failure and is exactly the
"evaluation service is unreachable" case the pattern exists to catch. This
repository already promises elsewhere — `README.md`, `scout/cli/result.py`,
`docs/CLI_SPEC.md` — that exit 2 never means pass. The gate breaks its own rule.

### F-2 — The divergence has a name, and the fix is to capture the environment, not the output

From the reproducible-pipeline work: *"capturing preprocessing parameters,
environment configs, and dependencies revealed that 'identical' datasets had
actually gone through different pipelines."* That is precisely the host/container
case — same parser, same source file, different available extractors, different
corpus. The prescribed shape is a lineage record that includes the environment,
with drift **surfaced between environments** and breaking changes treated as a
versioned migration rather than absorbed.

Applied here, and shaped by a bug this repository has already had: the
fingerprint must record what was **available**, not what was **configured**.
`figures_status: "ok"` for a parser that could not look (T5.1) is the same lie
one layer down, and a fingerprint asserting `tables: enabled` while `pdfplumber`
is absent would reproduce it one layer up.

### F-3 — The webhook default is the named anti-pattern

2026 guidance: webhook secrets *"must never be hardcoded in application code or
committed to version control"*, with secrets managers the recommended store. The
field has moved further — providers now mint ephemeral HMAC keys published via a
JWKS-style endpoint — but that is Gitea's side, not ours. What is ours is
`scripts/setup_gitea_webhook.py:165`:

```python
"--secret", default=os.environ.get("WEBHOOK_SECRET", "dev-secret")
```

A literal shared secret as a fallback default. Compose already requires a real
one, so the default is not even convenient — it is only a way to configure a
webhook that silently accepts forged payloads.

### F-4 — OD-1 already exists, so item 3 is a documentation fix, not a model decision

`docs/ARCHITECTURE_STATUS.md` §OD-1 records the wiki-search embedding model as an
**open owner decision**, with the measurement (`recall@1 0.625` on Vietnamese
against 0.812 for a multilingual alternative), the reason it is not a config swap
(basic-memory embeds in-process and never receives the LiteLLM credential), and
three options.

So `CLAUDE.md:10` — "multilingual; Vietnamese ok" — is not a model problem. It is
**live agent guidance contradicting a documented open decision**. Fixing the
sentence is independent of whichever option OD-1 eventually takes, and the
research confirms the option space is real if it is ever taken: VN-MTEB now
exists as a Vietnamese benchmark, and `multilingual-e5` / `bge-m3` are the
standard candidates.

---

### Goal

Nothing in this repository tells a reader, an agent, or CI something that the
repository itself can show is false. Concretely:

1. A gate never passes because its checker could not run.
2. No agent instruction contradicts a measurement in this repo.
3. The green offline suite is protected by CI before it rots.
4. No insecure value is a default.
5. A corpus cannot silently differ from the one the runtime would produce.

Non-goals: taking OD-1; adding figure/table extraction (T5.1 decided against);
compiling articles (T4.1 is the owner's go/no-go); anything else from Tiers 4–6.

### Assumptions

- **A1.** Enabling groundedness enforcement is safe *now*. It was disabled
  because "a full run judges 10 of 13 pages UNSUPPORTED"; today's measurement is
  **5 GROUNDED, 0 UNSUPPORTED** on a 5-page vault. Step 4 re-measures before
  flipping it, so the assumption is checked rather than trusted.
- **A2.** The Gitea runner is self-hosted and can run `uv` (the existing
  `auto-healer.yaml` already assumes a self-hosted runner reaching the LiteLLM
  gateway and PostgreSQL). The new workflow needs **neither** — it is offline
  only — so it is strictly less demanding than what already runs.
- **A3.** The capability fingerprint is metadata on `rag_documents`, not a schema
  change to `rag_chunks`. Verified by inspection in step 12; if it needs a
  migration, step 12 stops and says so.
- **A4.** The host-ingested corpus currently in place is the divergent one. The
  resolution is to re-ingest through the container image, not to bless it.
- **A5.** No item here needs a model call except the re-measurement in step 4
  (5 judge calls), the judge preflight in step 2 (**one** request per healing gate
  run), and the re-ingest in step 14 (embeddings only, Gemini).

---

## Plan

### Phase A — the gate that can pass on an outage, **and the path that mutates** (item 1)

> **Revised after Codex's plan review, and the correction was blocking.** My
> original Phase A fixed the false pass on the branch that does **not** mutate.
> Verified at `scripts/ci_address_gate.py:155–240`: `_groundedness_exit` is
> called only inside `if initial == 0`. When the initial address check fails the
> gate heals — mutating `sources[].hint` — then calls `_post_heal_exit`, which
> runs address verification and lint and **no groundedness at all**, and then
> returns 0 for a PR or `git add`/`commit`/`push`es the scheduled branch. The one
> path that changes published frontmatter was the one path with no groundedness
> judgement. Enabling the default alone would not have touched it.

**1. Advisory applies to the verdict, never to the infrastructure**
   - Files: `scripts/ci_address_gate.py`, `tests/test_ci_address_gate.py`
   - Change:
     - In the `not enforce` branch, return 0 **only** for exit 1. Any other
       non-zero — 2 in particular — propagates as 2.
     - Say why: an advisory verdict is a finding the run chose not to block on; a
       checker that could not run has produced no verdict at all.
   - Verify: advisory mode returns 0 for exit 1 and **2** for exit 2; existing
     enforcement tests unchanged. Write the exit-2 test first and confirm it fails.

**2. Preflight the judge before any mutation begins**
   - Files: `scripts/verify_groundedness.py`, `scripts/ci_address_gate.py`,
     `tests/test_ci_address_gate.py`
   - Change:
     - A `--probe` mode that judges nothing and answers one question: does the
       judge route respond? Exit 0 operational, 2 not. It cannot use
       `/health?model=` — that endpoint always reports 503 for this route (the
       open item 4), which is the whole reason a probe is needed.
     - The gate runs it **before** `_snapshot_wiki`, on both modes. Exit 2 →
       return 2 having changed nothing.
   - Verify: with the judge unreachable the gate returns 2 and `git switch -c`,
     `HEAL_COMMAND`, `git add`, `commit` and `push` are **all** unreached — assert
     on the recorded runner calls, not on the exit code alone. Cost: **one** judge
     request per healing gate run, stated in the docstring.

**3. Judge groundedness after the heal, and roll back on any failure**
   - Files: `scripts/ci_address_gate.py`, `tests/test_ci_address_gate.py`
   - Change:
     - After `_post_heal_exit` succeeds, run groundedness under the same
       enforcement/advisory policy as the non-healing path.
     - On a post-heal **1 or 2**: `_restore_wiki`, `_cleanup_scheduled_branch`,
       and return — before `git add`. Reuse the existing restore/cleanup path so
       there is one rollback implementation, not two.
   - Verify: **both modes**, and the assertions are about what did not happen —
     on post-heal 1 and on post-heal 2, `git add`, `git commit` and `git push`
     are never invoked and the wiki snapshot is restored. This is the step that
     makes "infrastructure failure never authorises mutation" true of the gate
     rather than merely written down elsewhere.

**4. Re-measure, then enable enforcement by default**
   - Files: `scripts/ci_address_gate.py`, `.gitea/workflows/auto-healer.yaml`,
     `tests/test_ci_address_gate.py`
   - Change:
     - Run `snpmemory verify-groundedness` and record the result. If not clean,
       **stop and report** — the advisory default was protecting a real failure.
     - If clean: invert the flag so enforcement is the default, keep an explicit
       `--advisory-groundedness` escape hatch, and replace the stale "10 of 13
       UNSUPPORTED" rationale with the current measurement and its date.
   - Verify: the gate blocks on exit 1 by default; the escape hatch still yields
     0 for exit 1 and **2** for exit 2 (step 1's invariant survives the inversion,
     on both the healing and non-healing paths).

### Phase B — instructions that contradict measurements (item 3)

**5. Correct the multilingual claim and point at OD-1**
   - Files: `CLAUDE.md`, `tests/test_docs_contract.py`
   - Change:
     - Replace "multilingual; Vietnamese ok" with what ships: English-only
       `bge-small-en-v1.5` @384, measured `recall@1 0.625` on Vietnamese
       paraphrases, and a pointer to OD-1 for the open decision.
     - Do **not** touch the model. That is OD-1 and it is the owner's.
   - Verify: a test asserts no agent-facing instruction file claims multilingual
     wiki search while `ARCHITECTURE_STATUS.md` records OD-1 as open — the same
     shape as the Tier 3 tool-namespace guard, which exists because instructions
     drifting from reality is a recurring failure here, not a one-off.

### Phase C — protect the suite that is finally green (item 4)

**6. An offline PR workflow**
   - Files: `.gitea/workflows/checks.yaml` (new)
   - Change:
     - On `pull_request` and `push`, **literally**:

       ```
       uv run ruff check .
       uv run ruff format --check .
       uv run mypy scout scripts
       uv run pytest -m 'not integration' --disable-socket -q
       ```

     - `-m 'not integration'` is not redundant with T6.3's skip, and Codex is
       right to insist on it. The fixture skips based on `SNP_INTEGRATION_PROJECT`
       being unset; the marker **deselects regardless of the environment**, so a
       runner that happens to export that variable cannot turn live tests on.
       Measured: `1342/1363 collected (21 deselected)`. `--disable-socket` is
       already in `addopts`; stating it means the workflow does not depend on
       that staying true.
     - `uv` is **pinned/installed in the workflow itself**. `auto-healer.yaml`
       assuming a capable self-hosted runner is an assumption, not a
       reproducibility guarantee, and a new workflow should not inherit it.
     - **Offline by construction** — no LiteLLM, no PostgreSQL, no judge, no
       secret. No `persist-credentials`, least-privilege `contents: read`,
       matching `security.yaml`.
   - Verify: the four commands run clean locally **in that exact form**; the YAML
     parses; the job declares no secret; `-m 'not integration'` deselects 21.

**7. Say what CI covers and what it does not**
   - Files: `README.md` or `docs/runbook.md`
   - Change: three workflows now — secret scan, vault heal, offline checks — and
     state plainly that **no** workflow runs the live verifies, because they need
     the stack and spend judge budget.
   - Verify: the docs contract test passes; the three workflow files named in the
     doc all exist.

### Phase D — the remaining correctness items (6, 5, 2, 7)

**8. The webhook secret refuses insecure values (item 6)**
   - Files: `scripts/setup_gitea_webhook.py`, `tests/test_setup_gitea_webhook.py`
     (new or existing)
   - Change:
     - Remove the `"dev-secret"` fallback. Unset → refuse, naming the variable.
     - Refuse known-development values (`dev-secret`, `changeme`, `secret`,
       empty, whitespace) unless `--development` is passed explicitly, and print
       a warning when it is.
     - Add a minimum length so a one-character secret cannot pass either.
   - Verify: unset exits non-zero naming `WEBHOOK_SECRET`; `dev-secret` without
     the flag is refused; with the flag it proceeds and warns; a real secret is
     unaffected. **No test may print a secret value.**

**9. State the non-parity of `snpmemory search` (item 5)**
   - Files: `scout/cli/commands/wiki.py`, `docs/CLI_SPEC.md`
   - Change: the module already says it "does not compete with" `snp-wiki`. Add
     the part that actually bites — the two use **different embedding models**
     (LiteLLM/Gemini here, in-process FastEmbed 384 there) and **will return
     different orderings for the same query**, so this command does not preview
     what an agent sees.
   - Verify: the sentence exists in both places; no behaviour change; suite green.

**10. Make the live wiki test's name match what it tests (item 2)**
   - Files: `tests/integration/test_live_end_to_end.py`
   - Change: it builds a temp corpus and requires LiteLLM + PostgreSQL, with no
     basic-memory anywhere — so it cannot prove the agent's first hop. Rename to
     say what it does (the `sources[]` → pgvector → verbatim path) and record in
     the docstring that basic-memory coverage does not exist.
   - Verify: the test still passes when selected; its name and docstring no
     longer claim `snp-wiki` coverage.
   - **Codex is right that this does not close the item.** A docstring note is
     too easy to forget. So the rename lands *with* a backlog entry carrying an
     acceptance criterion — a separately runnable basic-memory MCP test doing
     real snapshot → `search_notes` → `read_note` — recorded as an **accepted
     risk with an owner and a target**, not as a comment. Building that test is
     still out of scope here; pretending the rename closed it is what I am
     avoiding.

**11. Make the vision test assert the contract that is actually true (item 7)**
   - Files: `tests/integration/test_multimodal_vision_live.py`,
     `docs/REMAINING_TASKS.md`
   - Change:
     - **Revised on Codex's push.** My original was "record the coupled
       decision", which leaves a test that requires an absent asset *and* an
       intentionally absent capability — a test that cannot pass by design.
       Replace the positive claim with a **deployment-capability test** asserting
       the deployed contract: figures report `unavailable`, and nothing is
       described. That test is true today and would fail the day the capability
       silently returns.
     - The positive test and a committed asset move to an explicit
       vision-enabled profile, to be built only if that feature is approved.
   - Verify: the capability test passes against the shipped image's behaviour
     (Pillow absent → `unavailable`); no test requires
     `raw/images/agent_memory_architecture.svg`; the backlog entry names both
     halves and links T5.1.

### Phase E — the capability boundary (the item I conceded)

**12. Record what the parser could actually do**
   - Files: `scout/parsers.py`, `scout/ingest.py`, `tests/test_parsers.py`
   - Change:
     - A `capability_fingerprint` with a **canonical, versioned schema**, per
       Codex's tightening:
       * `schema_version` — so the record itself can migrate;
       * `parser_revision` — an explicit constant **bumped on semantic parser
         changes**, not the package version. `0.1.0` is insufficient: this
         repository has changed parser behaviour twice this week without a
         release bump (the reference lift, the figure-status fix), and either
         would have left the fingerprint claiming equivalence.
       * `extractors` — per optional extractor, **probed availability** plus the
         installed version when present (F-2: probe, never read a flag).
   - Verify: on the host the fingerprint reports tables available; inside the
     `scout` image it reports them absent. **Both must be observed** — the point
     is that the two differ, and a test that only ever sees one of them proves
     nothing.

**13. Persist it, and refuse a mismatch**
   - Files: `scout/ingest.py`, `scout/sync_job.py`,
     `scout/cli/commands/ingest.py`, `tests/test_ingest_v2.py`
   - Change:
     - Store the fingerprint with each indexed document (A3: metadata, not a
       schema change).
     - `snpmemory ingest` **refuses** when its fingerprint differs from the one
       already recorded for that corpus, naming both. Strict refusal, no
       compatibility matrix — Codex's framing, and correct: silent churn is worse
       than a blocked command.
     - `sync-job` treats a mismatch as a **permanent failed outcome with no
       partial write and no delete** — not a log line. It must not reach
       `reconcile_deletions`, which would purge rows for a corpus it has just
       declared it cannot safely rebuild. Codex's correction; mine said
       "reports", which would have left the destructive half running.
   - Verify: ingesting from the host against a container-built corpus exits
     non-zero naming the difference; ingesting with a matching fingerprint is
     unaffected; a corpus with **no** recorded fingerprint (everything indexed
     before this step) warns rather than refusing, or every existing deployment
     is bricked by an upgrade.

**14. Resolve the divergence I created**
   - Files: none — an operation
   - Change: re-ingest through the container image so the corpus matches what
     `sync-job` produces. The 3 host-only table sections go away, which is the
     correct outcome: they are evidence the runtime cannot reproduce.
   - Verify: `verify-addresses` **5/5 PASS** afterwards (the same hard gate the
     bibliography change had to clear), `verify-vault` 0 errors, and the
     fingerprint recorded for the corpus is the container's.

**15. `snpmemory ingest` should not need a hand-exported `.env`**
   - Files: `scout/ingest.py` or `scout/backends/pgvector.py`,
     `scout/cli/commands/ingest.py`, `tests/test_cli_ingest.py`
   - Change: thread the resolved `Config` into the connection path instead of
     letting `postgres_settings()` read ambient `os.environ` — the same fix
     applied to `fetch` in Tier 1, in the place it was missed.
   - Verify: `snpmemory ingest --dir raw --dry-run` succeeds in a shell that has
     **not** sourced `.env`; a regression test covers it.

### Phase F — close out

**16. Reconcile the conversation and the backlog**
   - Files: `docs/conversation.md`, `docs/REMAINING_TASKS.md`,
     `artifacts/superpowers/finish-codex-audit-2026-08-26.md`
   - Change: mark each of the seven fixed / accepted / open with its evidence,
     as Codex asked; record the capability boundary as built.
   - Verify: `pytest -q` green; `ruff check`, `ruff format --check`, `mypy`,
     `verify-secrets` clean; `snpmemory schema` valid against clispec v0.3;
     `plugin.json`/`mcp.json` valid; live `verify-vault` and `verify-addresses`.

---

### Risks & mitigations

- **R1 — Enabling enforcement could block the vault on a judge outage.** That is
  the *point* of step 1 (exit 2 propagates) and the *risk* of step 2 (exit 1
  blocks). Mitigation: step 2 re-measures first and stops if the vault is not
  clean; the escape hatch remains for a deliberate, visible override. A gate
  nobody can override gets disabled wholesale, which is worse.
- **R2 — A PR workflow that fails for environmental reasons trains people to
  ignore it.** Mitigation: offline by construction — no gateway, no database, no
  secret. It can only fail on the repository's own code, which is the only thing
  it should be able to fail on.
- **R3 — The capability refusal could brick an existing deployment.** Every
  document indexed before step 11 has no fingerprint. Mitigation: absent
  fingerprint **warns**, never refuses; only a *differing* one refuses. Its own
  test, same shape as the plan-hash guard's backward-compatibility case in Tier 2.
- **R4 — Fingerprinting could become its own lie.** A fingerprint that records
  configuration rather than availability reproduces T5.1 one layer up.
  Mitigation: F-2 — probe the extractor, do not read a flag; step 10's
  verification requires observing the host and the container disagree.
- **R5 — Step 14 removes evidence.** The 3 host-only table sections disappear.
  They are evidence the runtime cannot reproduce, so keeping them is the actual
  hazard; but it is a corpus change and it clears the same `verify-addresses`
  gate the bibliography lift did.
- **R6 — Step 6 could lock somebody out of a working local setup.** Mitigation:
  the refusal names the variable and the `--development` flag in the same
  message, and Compose already requires a real secret, so nothing that works
  today stops working.

### Rollback plan

- **Phase A** — two branches in one function. `git revert` restores the old
  behaviour, including the bug; step 1's exit-2 test makes that visible.
- **Phase B** — a documentation sentence and a guard test.
- **Phase C** — a new workflow file. Deleting it removes CI; nothing else moves.
- **Phase D** — steps 8–11 are localised. Step 8 (webhook) and step 11 (the
  vision contract test) are the behaviour changes; step 8 is a refusal, so
  reverting loosens rather than breaks.
- **Phase E** — step 12 is additive metadata. Step 13 adds a refusal: reverting
  restores silent divergence, which is what the step exists to end. **Step 14 is
  the only one that changes data**, and it is a re-ingest, reversible by
  re-ingesting the other way. `raw/` is untouched (R-3.1) throughout.
- **Phase F** — documentation.

Nothing here deletes a wiki page, modifies `raw/`, or touches the vault's prose.

### Decisions needed

1. **Enable groundedness enforcement by default (step 4)?** *Recommended: yes,
   conditional on the re-measurement.* The reason it was advisory — 10 of 13
   pages unsupported — no longer holds. But this makes CI block on content, and
   the T4.1 compile will add 6–12 pages that must also pass. If you would rather
   land the compile first, steps 1–3 still fix the dangerous half — an outage passing
   silently, and a heal that mutates and pushes without ever being judged — and
   step 4 can wait.
2. **The capability boundary (Phase E) is an architecture change.** *Recommended:
   build it, strict refusal first.* It changes what `ingest` accepts and adds a
   record to every document. I conceded the argument to Codex and I think it is
   right, but it is your call, and Phases A–D stand without it.
3. **Step 14 changes the live corpus.** *Recommended: yes, after Phase E.* The
   present corpus contains 3 table sections `sync-job` cannot reproduce and will
   drop unannounced. Re-ingesting through the container makes the corpus match
   the runtime. It costs embedding calls only (Gemini), not judge budget.
4. **Item 2's real fix — basic-memory coverage — is not in this plan.** Step 10
   only stops the test's name overstating. Actual `snp-wiki` end-to-end coverage
   is a new integration test against a live basic-memory, and it is worth doing;
   it is scoped out here because renaming is honest and cheap while the coverage
   is neither.
