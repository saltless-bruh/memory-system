---
name: snp-plan-articles
description: "Use this skill to propose how one source document should become several wiki articles, reading the source's own headings to suggest a decomposition. Use it before compiling, when a source is too large or covers several subjects. It proposes a plan file and writes no vault page; snp-compile-batch then compiles that plan."
---

# Plan articles from one source

```bash
scripts/plan_articles.py raw/papers/paper.pdf --dept ai_eng --out plan.json
```

| flag | meaning |
|---|---|
| `--dept` | required; the department the articles are authored for |
| `--category` | `concept` by default |
| `--max-depth` | how deep into the source's headings to split |
| `--out` | where to write the plan; printed otherwise |

The proposal follows the source's **own** headings. It is a suggestion to read
and edit, not a decision — one subject per page is a judgement the plan cannot
make for you.

## Output

A receipt: `{schemaVersion, ok, command, input, checks[], checkCount}` with the
plan under `output`. Review the proposed split before compiling it, then hand
the plan to `snp-compile-batch`.

## Boundary

This writes a plan file and nothing else. No vault page is created, no index is
regenerated, and nothing is pushed.
