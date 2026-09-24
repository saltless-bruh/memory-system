---
name: snp-compile-batch
description: "Use this skill to run, monitor, or stop a batch compile from a plan file — the scripted operations compile-plan, compile-status and compile-cancel. Use it when a plan already exists and articles must be produced from it. To author or revise a single page by hand against the schema, use snp-compile-wiki instead; to produce the plan itself, use snp-plan-articles."
---

# Run a batch compile

```bash
scripts/compile_plan.py plan.json --dry-run      # rehearse, write nothing
scripts/compile_plan.py plan.json --confirm      # approve this write
scripts/compile_plan.py plan.json --confirm --background   # returns a handle
scripts/compile_status.py <handle>               # progress, read-only
scripts/compile_cancel.py <handle>               # stop at the next article
```

Every script prints a receipt: `{schemaVersion, ok, command, input, checks[],
checkCount}`, plus the command's own output under `output`. Read `checks[]`
rather than guessing from prose.

## Mutation is approved per call

`compile_plan` refuses to write without `--confirm`. That is deliberate: the
approval belongs to the call, not to having the skill installed. Rehearse with
`--dry-run` first when the plan is new or has changed.

## Stopping

`compile_cancel` asks the batch to stop at its next article boundary, so
finished articles are kept. A handle that names no batch is reported as such —
do not retry it with a different spelling.

## Boundary

These scripts write pages into a checkout. They never push and never merge;
review happens on a feature branch through a pull request (R-6.4, R-7.3).
