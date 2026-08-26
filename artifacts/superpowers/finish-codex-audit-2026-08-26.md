# Finish — the Codex audit items and the capability boundary

**Date:** 2026-08-26 · **Branch:** `fix/architecture-security-hardening`
**Plan:** `artifacts/superpowers/plan-codex-audit-2026-08-26.md` (= `plan.md`)
**Execution log:** `artifacts/superpowers/execution.md`
**Exchange:** `docs/conversation.md`

**15 of 16 steps landed. Step 14 stopped** — it would have made the corpus worse,
and it depends on a commit decision that is the owner's.

---

## Verification

| Command | Result |
| --- | --- |
| `uv run pytest -q` | **1381 passed, 21 skipped** |
| `uv run pytest -m 'not integration' --disable-socket -q` | 1381 passed, 21 deselected |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 339 files already formatted |
| `uv run mypy scout scripts` | Success — 72 source files |
| `snpmemory verify-vault` | 7 pages · **0 errors** |
| `snpmemory verify-addresses` | **5 PASS · 0 FAIL · 0 DRIFT · 0 NO_EVIDENCE** |
| `snpmemory verify-groundedness` | 5 GROUNDED · 0 UNSUPPORTED |
| migration `004` | applied live; `0 pending` |

---

## The seven, marked

| # | Item | State |
| --- | --- | --- |
| 1 | groundedness gate advisory, swallowing exit 2 | **FIXED**, and the mutating path with it |
| 2 | live wiki test overstates its coverage | **RENAMED**, gap tracked as T3.3 |
| 3 | `CLAUDE.md` multilingual claim | **FIXED**, with a guard |
| 4 | no PR workflow | **FIXED** — `.gitea/workflows/checks.yaml` |
| 5 | `snpmemory search` parity | **FIXED** — non-parity stated |
| 6 | `WEBHOOK_SECRET` default | **FIXED** — no default at all |
| 7 | live-VLM asset | **FIXED** — asserts the deployed contract |

Plus the capability boundary conceded to Codex: **built and verified live.**

---

## What Codex's review caught that I would have shipped

**The gate fixed only half the problem.** `_groundedness_exit` was called solely
inside `if initial == 0`. The failing branch heals — rewriting `sources[].hint` —
then calls `_post_heal_exit`, which runs address verification and lint and *no
groundedness*, then returns 0 for a PR or `git add`/`commit`/`push`es. The one
path that changes published frontmatter was the one path with no judgement, and
my original Phase A would have made the *non*-mutating branch honest and reported
the phase complete.

**"reports a mismatch" left the destructive half running.** My `sync-job`
wording would still have reached `reconcile_deletions` — purging rows for a
corpus the process had just declared it could not rebuild.

**A test that cannot pass is worse than a missing one.** My item-7 plan was to
"record the coupled decision", leaving a test requiring an absent asset *and* an
intentionally absent capability. Codex's version — assert the deployed contract —
is true today and fails the day the capability silently returns.

---

## Findings from execution

**A3 was falsified.** `rag_documents` had no metadata column. The plan said to
stop and say so; the migration path here is established and tested, so `004_…`
is routine — additive, nullable, applied live.

**The `.env` defect was in two places.** Threading resolved config into
`get_pg_connection` fixed `ConfigError: POSTGRES_HOST`, and revealed the same
defect one layer over in the embedder. Both fixed; verified with every relevant
variable unset in the shell.

**The CI workflow earned its place before it ran** — its four commands, executed
verbatim, immediately found 3 unformatted files from my own preceding edits.

**A measurement mistake of mine.** My first simulated judge outage passed when it
should have failed. Cause: `verify_groundedness.py:76` calls
`dotenv.load_dotenv()` at module scope, so the script reloaded `.env` and
defeated `env -u`. Re-tested against a dead port; all three states then correct.

---

## Why step 14 stopped

```
scout / sync-job image revision : 2d2b9dd  (= git HEAD)
uncommitted files              : 219
scout.references in image      : ABSENT
scout.capabilities in image    : ABSENT
```

Re-ingesting through the running image would **restore the 23 bibliography
chunks** — undoing a change that cleared a hard gate — and record no fingerprint.
Rebuilding first does not help alone: `SNP_GIT_REVISION` comes from
`git rev-parse HEAD`, so a rebuild now stamps `2d2b9dd` onto an image containing
219 files of uncommitted work, which is the dirty-tree hazard that stamp exists
to catch.

The corpus is still host-built — and now **says so in its fingerprint**. Once
this work is committed and the image rebuilt, `sync-job` refuses rather than
silently rewriting. That is a commit decision under a standing hold.

---

## Review pass

### Blocker
None.

### Major
None outstanding. Two were found during execution and closed:

1. **`snpmemory gate` would have crashed.** Inverting the enforcement flag left
   `scout/cli/commands/ci.py` appending `--enforce-groundedness`, which no longer
   exists — an argparse failure at runtime. Found by grepping for stale
   references after the rename; four surfaces updated together.
2. **The embedder ignored resolved configuration**, the same defect as the
   connection, one layer over.

### Minor

1. **The judge preflight costs one request per healing gate run.** Deliberate and
   documented, but it is a real cost on a route with a 50/day ceiling.
2. **`--allow-capability-change` is a genuine foot-gun.** It rebuilds the corpus
   with whatever the current environment can do. It is the only way to recover
   from a legitimate capability change, so it has to exist; it is named
   explicitly rather than implied.
3. **`corpus_fingerprint_mismatch` returns on the first differing document.**
   Fine for a corpus of one; a large corpus would report one difference at a time.
4. **`PARSER_REVISION` is a manual constant.** Nothing enforces bumping it, so a
   future parser change could silently claim equivalence — exactly the failure it
   exists to prevent, one level up.
5. **T3.3 is an accepted risk, not a fix.** No test proves the agent's first hop.

### Nit

1. `KNOWN_DEVELOPMENT_SECRETS` is a hand-written list; a novel placeholder passes
   the name check and is caught only by the length rule.
2. The vision test branches on whether Pillow is importable, so it asserts
   different things in the two environments. Deliberate — it must not be vacuous
   in either — but it is two tests wearing one name.
3. `describe_fingerprint_difference` ignores `python`, which is recorded but
   never compared. A Python-version change *could* alter parsing; treating it as
   a mismatch would block far more than it protects.

---

## Follow-ups

* **Step 14** — commit, rebuild the image with a clean revision stamp, re-ingest.
* **The judge "unknown" health status** — the probe answers liveness honestly,
  but `/health` still reports 503 for a route excluded from the background loop.
  Not designed; open rather than vague.
* **T3.3** — real basic-memory coverage, with OD-1 or the next live integration
  work.
* **T4.1** — the compile go/no-go, unchanged and still the owner's.

## Still on hold

15 commits local and unpushed, now alongside a working tree of ~219 files. The
five untracked `wiki/concepts/` pages are kept but not pushed.
