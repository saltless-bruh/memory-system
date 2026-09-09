# Post-latency owner decisions and verification

**Date:** 2026-09-09

## Result

The owner approved **10 s p95** for I-3 and **Option A with manual Git first**
for the daily Obsidian vault, then authorized implementing the plan. The demo,
measurement handoff, and executable runbook now record those choices. The I-3
scorecard remains unfilled; the E11 demonstration remains pending.

I-3 starts at successful push completion and ends at completion of the served
read confirming the search result, over ten consecutive warm one-page edits,
using nearest-rank p95. Report Obsidian save→push separately. The historical
ten samples measured the first search hit at 5.653 s p50 / 7.781 s p95 and
confirmed reads afterward without timing them. They informed the chosen bar;
they do not certify the revised read-completion deadline.

The full-snapshot implementation remains unchanged. The retained-log
decomposition shows 3.736 s p95 before host fetch and 0.256 s afterward through
publication. Full archive plus extraction measured 0.209 s p95. Improving Gitea
queue wake-up is separate follow-up work; subtracting the entire pre-fetch span
gives an optimistic 2.503 s p50 / 4.321 s p95 replay, not a live-tested fix.

The two install specifications and original latency handoff are tracked by
`3210bbf`. The specs were force-added; `.gitignore` remains unchanged.

## Owner assets and rollout boundary

The runbook is explicitly **owner-only** because the owner holds every
department's access. Sparse checkout does not restrict access to repository
contents or history. Department-limited onboarding requires a distribution
boundary decision before any colleague receives a clone.

`docs/VAULT_ACCESS_AND_PLURALISM.md` was read as context only, left untouched
and untracked. No repository split, lock service, plugin, or vault setup was
implemented. The runbook preserves the old vault, calls out local-only notes,
checks the starting Git index, reviews all staged changes before publication,
and guards reversal destinations. No new probe was pushed by this follow-up.

## Measured checks

The complete checks below were run by
`.venv/bin/python /tmp/snp-post-latency/run_full_verification.py` (exit 0).
The runner retains each command's output, exit status, and SHA-256 digest in
`/tmp/snp-post-latency/verification.json` and separate logs.

| Command | Exit | Measured result |
| --- | ---: | --- |
| `rm -rf .agents .codex` | 0 | Required root cleanup; no tracked harness files |
| `env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q` | 0 | **1382 passed / 29 deselected**; complete run without fail-fast |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | **378 files already formatted** |
| `uv run mypy scout scripts` | 0 | No issues in **75 source files** |
| `.venv/bin/python /tmp/snp-post-latency/verify_snapshot_report.py` | 0 | Ten samples agree with retained timestamps and calculated percentiles |
| `.venv/bin/python /tmp/snp-post-latency/verify_e11_runbook.py` | 0 | Approved criterion, owner-only workflow, pending demonstration, and Bash syntax checked |
| `.venv/bin/python /tmp/snp-post-latency/verify_ledger_reporting.py` | 0 | **14 unique gates**, **13 checked**, E11 unchecked with an abandonment record |

The suite remains at the 1382-test baseline. The formatter's current count is
378; the earlier 356-file report is not reused as a measurement of this run.

## Ledger identity and remaining handoff

This follow-up uses `/tmp/snp-post-latency/GATES.md`. It separately verifies
documents, the recorded owner answers, offline/static checks, commit scope,
remote delivery, and final image labels. Its completion does not close E11.

The authoritative engine ledger remains
`.unlazy/v3-retrieval/gates/engine-2026-09-04.md`: **14 unique gates**, **13
checked**, and E11 unchecked. E11 also has one historical `ABANDON` record for
the owner's 2026-09-07 workflow deferral. Those overlapping categories must not
be added to produce 15 gates. The present owner answers resolve the choice;
the owner must still perform and demonstrate the workflow. Engine gates were
not rerun or rewritten by this documentation change.

## Final delivery evidence

The final feature commit belongs on both existing feature refs and PR #2;
both `main` refs must remain unchanged. The final image build runs after that
commit:

```bash
SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose build scout sync-job host-sync
```

Post-commit results, whose final SHA cannot be embedded in its own commit, are
recorded in PR #2 and `/tmp/snp-post-latency/delivery-result.json`,
`build-result.json`, and `images-result.json`. Image verification checks the
newly built tags separately from the existing running containers. This task
rebuilds images; it does not restart services or claim that rebuilt tags are
already the running deployment.
