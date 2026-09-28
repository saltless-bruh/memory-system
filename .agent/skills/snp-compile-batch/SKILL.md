---
name: snp-compile-batch
description: "Use this skill to run, monitor, or stop a batch compile from a plan file — the scripted operations compile-plan, compile-status and compile-cancel. Use it when a plan already exists and articles must be produced from it. To author or revise a single page by hand against the schema, use snp-compile-wiki instead; to produce the plan itself, use snp-plan-articles."
---

# Run a batch compile

```bash
scripts/compile_plan.py plan.json --dry-run      # rehearse, publish nothing
scripts/compile_plan.py plan.json --confirm      # approve this write
scripts/compile_plan.py plan.json --confirm --background   # returns a handle
scripts/compile_status.py <handle>               # progress, read-only
scripts/compile_cancel.py <handle>               # stop at the next article
```

Every script prints a receipt: `{schemaVersion, ok, command, input, checks[],
checkCount}`, plus the command's own output under `output`. Read `checks[]`
rather than guessing from prose.

## The lane is draft-only

A batch generates and judges every article into `<plan>.staging/`, one page per
article, before anything touches the vault. It then tries to publish, and
publishing ends by regenerating `index.md`. On a vault whose index is written
by hand — the production vault is one — that regeneration is refused, because
it would replace every authored description with a blank. The batch then
reports `draft-only`, writes nothing into the vault, and names the staging
directory. **Expect that outcome.** The drafts in staging are the product: take
them to a feature branch by hand and open a pull request.

Publishing does complete on a vault whose index is generated (every page
carries `summary:`), such as the repository's own sample tree.

## Mutation is approved per call

`compile_plan` refuses to write without `--confirm`. That is deliberate: the
approval belongs to the call, not to having the skill installed. Rehearse with
`--dry-run` first when the plan is new or has changed. `--dry-run` still spends
model calls and stages drafts, but publishes nothing, including when combined
with `--background`.

## Stopping

`compile_cancel` asks the batch to stop at its next article boundary, so
finished articles are kept. A batch whose process is alive but silent past its
heartbeat ttl reads `stalled` and may be hung; cancelling it still writes the
request, and says to stop the process by hand if it never reaches a boundary.
A handle that names no batch is reported as such — do not retry it with a
different spelling.

## Finding the checkout

These scripts run `snpmemory` inside the system checkout. They find it by
walking up from the script and from the working directory. An installer that
copies this skill into another project (`.opencode/skills/`, `.agent/skills/`)
leaves no checkout above it, and every script then fails with
`checkout-found: false` and exit 2. Set `SNP_REPO_ROOT` to the checkout's path
— the directory holding `pyproject.toml` — and run the script again.

## Boundary

These scripts write drafts into a checkout. They never push and never merge;
review happens on a feature branch through a pull request (R-6.4, R-7.3).
