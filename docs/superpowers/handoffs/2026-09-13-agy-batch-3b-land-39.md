# H3 for Agy — land 39 of the 40. One page, and the rule I gave you was wrong.

**Measured:** 2026-09-13, against `gitea/vault/tldr-batch-3b` @ `0673170`
**Verdict:** revision 2 measures **identically** to revision 1 — but the batch is
fine. Drop one TL;DR and it lands. **Owner approved this on 2026-09-13.**

---

## 1 · What the measurement says

```
  main @ cc55382             en 0.95   vi 0.80   ALL 0.88
  batch 3b rev1 (7956bf1)       0.90      0.75       0.82
  batch 3b rev2 (0673170)       0.90      0.75       0.82   ← no change
```

Same two queries, same displacing page — **with `DrawDB` removed from its chunk 0,
exactly as I asked.** Your execution was correct. The rule was not.

Your scope was clean again: 40 files, **+120 lines, 0 deletions**, nothing outside
`wiki/`, control documents and `wiki/raw/` byte-identical, ingest exactly
`40 indexed, 390 unchanged, 0 purged`. TL;DR coverage 122 → 162.

## 2 · The category-qualifier rule was mine, and it was wrong

I told you a category page must not name its own exemplars in chunk 0. I inferred
that from one observation and shipped it to you without testing it. You applied it
to 14 pages, faithfully, and it moved nothing.

Here is the test I should have run before writing that rule — same branch tree,
TL;DR removed from **one** page, nothing else touched:

```
  branch as pushed, 40 TL;DRs           0.90 / 0.75 / 0.82
  branch minus ONE TL;DR, 39            0.95 / 0.80 / 0.88   ← full baseline
                                        ingest: 1 indexed, 429 unchanged
```

**All** of the regression is that one page. Your other 39 are neutral or positive.

## 3 · What is actually happening

It was never about the exemplar's name.

```
  query   "drawing an entity relationship diagram in the browser
           and exporting SQL"                         ← phrased as a CAPABILITY

  concepts/Online ERD & SQL Generator.md              ← IS that capability
     chunk 0 restates the query almost term for term   → rank 1
  Entities/DrawDB.md                                  ← a tool that has it
                                                       → rank 2
```

Once the concept page has *any* chunk-0 summary, it becomes the better match for a
capability-phrased question than the specific tool page. Removing `DrawDB` from
the text changed nothing because `DrawDB` was never the matching term — "ERD",
"browser", "SQL DDL" were.

Whether that is even wrong is a fair question: the benchmark expects the tool page,
`recall@3` is 1.00, and an agent asking that question gets both pages. The owner
chose not to touch the benchmark to settle it.

## 4 · What to do — small and exact

1. On `vault/tldr-batch-3b`, **remove the `## TL;DR` section from
   `wiki/concepts/Online ERD & SQL Generator.md`.** Leave the rest of that page
   exactly as it is. Leave the other 39 pages exactly as they are.
2. Force-push the branch and hand off. Expected result, already measured on this
   exact configuration: **en 0.95 · vi 0.80 · ALL 0.88**, with
   `FIND READ CITE VERIFIED` and `MCP EVAL VERIFIED`.
3. Coverage after merge: **161 / 429**.

**Do not** try to rewrite that TL;DR into something cleverer. That is exactly the
untested-guess move that cost you two batches, and I would be the one guessing
again. A page with no TL;DR is the measured-safe state; if we want one there
later, it gets its own single-page experiment.

**Do not** revert the 14 category-page rewrites from revision 2. They cost nothing
and they read better. Keep them.

## 5 · Standing correction

Three rules I have given you have now been contradicted by measurement: language
matching, then entity density stated without a qualifier, then the category
qualifier itself. Each time you executed what the brief said and the brief was
wrong. I am not going to send a fourth rule inferred from one observation — from
here, a hypothesis that changes how you write gets a single-page test before it
reaches you as an instruction.
