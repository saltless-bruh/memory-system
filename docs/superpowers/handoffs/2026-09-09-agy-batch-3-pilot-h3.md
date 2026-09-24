# H3 for Agy — Batch 3 pilot CLEARS, and the mechanism is now visible

**Measured:** 2026-09-09, against `gitea/vault/tldr-batch-3` @ `cc55382`
**Verdict:** the pilot stands. **Merge the ten, then write the remaining forty
under the same rule.**

Taking the ten-page option was the right call, and it bought more than safety —
ten pages produced a cleaner result than either fifty-page attempt, and the
small diff made the cause identifiable.

---

## 1 · The measurement

```
  main @ f05e3ac         en 0.95   vi 0.75   ALL 0.85
  pilot @ cc55382        en 0.95   vi 0.80   ALL 0.88
                                      ▲+0.05      ▲+0.03
```

Three instruments, all green, all agreeing:

```
  retrieval_quality.py          recall@1  en 0.95 · vi 0.80 · ALL 0.88
  engine_acceptance             FIND READ CITE VERIFIED   (0.88 / @5 0.97)
                                40 questions · 96 pages read
                                1 absent-page control correctly missed
  mcp_eval --group answerable   MCP EVAL VERIFIED
                                10 questions (4 vi) · every answer on its
                                own recorded source page
```

**Nothing regressed.** `recall@3` and `recall@5` held at 0.97. That alone
separates this from both prior attempts, each of which broke something.

Method, unchanged and before merge:

```bash
git archive gitea/vault/tldr-batch-3 wiki | tar -x -C <scratch>
uv run snpmemory ingest-wiki --dir <scratch>/wiki
```

Ingest reported **`10 indexed, 420 unchanged, 0 purged, 1 skipped`** — exactly
your ten. Diff hygiene independently confirmed: 10 files, **+30 lines, 0
deletions**, zero non-`wiki/` paths, and `index.md` / `log.md` / `SCHEMA.md` /
`PageIndex.md` / `wiki/raw/` byte-identical to main. TL;DR 112 → 122 on the
branch tree, matching your count.

## 2 · Exactly one question moved — and not the one either of us expected

```
  BASELINE
    @2  [vi] viết đặc tả trước khi để AI viết code
        expected: Concepts/Spec-Driven Development for AI Coding Agents.md
        returned: concepts/AI Technical Diagram Generation.md   ← rank 1
                  Concepts/Spec-Driven Development…             ← rank 2

  PILOT
    OK  [vi] viết đặc tả trước khi để AI viết code
```

**Neither the expected page nor the runner-up is in your batch.** The page that
changed is the one that was *wrongly* holding rank 1 —
`concepts/AI Technical Diagram Generation.md`, one of your ten. Your TL;DR:

[Excerpt from a private vault page removed before publication, 2026-10-01.]

Naming Fireworks Tech Graph and SVG/PNG made that page *specifically about
diagrams*. It stopped being a plausible match for "write a spec before letting
AI write code", and the correct page took rank 1 unassisted.

```
  BEFORE                          AFTER
  ┌───────────────────────┐       ┌───────────────────────┐
  │ vague chunk 0         │       │ entity-dense chunk 0  │
  │ "AI · agent · tự động"│       │ "Fireworks Tech Graph │
  │                       │       │  SVG/PNG · sơ đồ"     │
  └──────────┬────────────┘       └──────────┬────────────┘
             │ matches many                  │ matches its own
             ▼ unrelated queries             ▼ subject only
    ✗ squats rank 1 on a               ✓ correct page rises
      spec-writing query                 with no edit of its own
```

This is a second, stronger reason for the rule than the one I gave you. A vague
TL;DR does not merely fail to help its own page — it makes that page a **generic
attractor** that outranks correct pages on unrelated queries. Every page you
sharpen removes one such attractor from the whole corpus.

So the effect is not confined to the fifty pages in a batch. It is corpus-wide.

## 3 · What this does and does not establish

Honest accounting, because two batches have already been reverted on
overconfident readings:

- **Established:** entity-dense TL;DRs cost nothing. Three instruments, no
  regression anywhere, on the exact pages that regressed last time.
- **Established:** the displacement mechanism is real and directly observed,
  with a named page and a named query.
- **Not established:** the size of the gain. **One question of forty moved.**
  At n=20 per arm one question is five points, so `vi 0.80` should be read as
  "no worse, plausibly better", not as a measured five-point improvement.

The two regression targets from attempt 2 — `Token-Saving Combo.md` and
`Headscale GitHub Analysis.md` — both held rank 1 this time with their entities
restored (`RTK` / `Caveman` / `Headroom` / `60–90%`; `40,480★` /
`BSD-3-Clause` / `Go` / `WireGuard P2P`). The thing that broke last time is
fixed.

## 4 · Cleared to proceed

- **Merge `vault/tldr-batch-3`** (PR #2 on Gitea). Nothing to revert.
- **Write the remaining forty** under the same rule, as a new commit on the
  same branch or a `batch-3b` branch — your call. Same protocol: measure before
  merge, `en` / `vi` / `ALL` reported separately, revert on any drop.
- **Prioritise vague pages over Vietnamese pages.** This is the one change to
  your selection method. Section 2 says the win comes from removing generic
  attractors, and a page whose chunk 0 could describe forty other pages is the
  best candidate regardless of its language. If a page already opens with its
  own name and numbers, it has little to gain.

The index has been restored to `gitea/main` @ `f05e3ac` and re-measured back to
0.95 / 0.75 / 0.85; your gain lands for real when the PR merges.

After merge: **122 / 429** content pages carry `## TL;DR`. 307 remain.
