# I-3 latency distribution and Obsidian-hop decision

**Measured:** 2026-09-09 02:07:18–02:09:32 UTC
**Disposition:** pipeline measured; editor workflow and replacement criterion await
owner approval

## Outcome

The warm push-to-query path is slower than the current five-second I-3 bar.
Samples: **10** consecutive single-page add/read/delete cycles. The latency from
a successful `git push` return to the first `wiki_search` result had:

- p50: **5.653 s**
- p95: **7.781 s**
- minimum: **4.979 s**
- maximum: **7.781 s**
- under 5 seconds: **1/10**
- at or over 5 seconds: **9/10**

The percentile method is nearest-rank. At N=10, p95 is deliberately the slowest
sample. Each search hit was confirmed with a full `wiki_read`. Each temporary
page was then deleted from private Gitea `main`, and its disappearance was
confirmed by two consecutive served searches before the next sample began.

The exact command was:

```bash
set -a && . ./.env && set +a
SNP_PROPAGATION_SAMPLES=10 \
  SNP_PROPAGATION_POLL_SECONDS=0.25 \
  SNP_I3_LATENCY_REPORT=/tmp/snp-i3-latency/active.json \
  .venv/bin/python artifacts/v3/checks/engine_acceptance.py \
    --group vault-change-latency
```

It exited **0** and printed:

```text
N=10 push-complete-to-query p50=5.653s p95=7.781s
VAULT CHANGE LATENCY MEASURED
```

Obsidian-to-push is not measured. The clock starts only after the push has
returned successfully; the local Git push duration is reported separately.

## Where the time goes

The structured sync-job events used the published vault commit SHA as their
correlation ID. Every sample recorded watcher wake, changed-page chunk complete,
embed request sent, embed response received, PostgreSQL commit, and row visible
in that order. Stage values below are seconds:

| Span | N | Minimum | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| Local `git push` command | 10 | 0.333 | 0.345 | 0.383 | 0.383 |
| Push returned → host snapshot published | 10 | 2.473 | 3.553 | 3.900 | 3.900 |
| Host snapshot published → watcher wake | 10 | 0.054 | 0.069 | 0.100 | 0.100 |
| Watcher wake → changed page chunked | 10 | 0.617 | 0.647 | 0.691 | 0.691 |
| Chunked → embed request sent | 10 | 0.000 | 0.000 | 0.000 | 0.000 |
| Embedding round trip | 10 | 0.465 | 0.594 | 0.974 | 0.974 |
| Embed response → PostgreSQL commit | 10 | 0.026 | 0.044 | 0.095 | 0.095 |
| PostgreSQL commit → row visible | 10 | 0.000 | 0.000 | 0.000 | 0.000 |
| Row visible → query observed | 10 | 0.314 | 1.008 | 2.584 | 2.584 |
| Cleanup push returned → absent | 10 | 4.770 | 5.436 | 6.822 | 6.822 |

The per-span percentiles are calculated independently and therefore should not
be added together. The finding is still unambiguous: the largest measured
component is between push completion and host snapshot publication. That span
combines webhook delivery, host-sync fetch, snapshot materialization, and
publication; the current host log cannot separate those sub-stages. Embedding
is not the first optimization target: its p95 is 0.974 seconds, while the
combined host span is 3.900 seconds.

A follow-up decomposition found that this label hid the real boundary. For the
same ten edits, 3.736 seconds p95 elapsed before host-sync's first fetch reached
Gitea, while fetch through atomic snapshot publication took only 0.256 seconds
p95. Full archive plus extraction of all 433 files measured 0.209 seconds p95.
The dominant delay is two polling persistent Gitea queues, not snapshot
materialization; the evidence and recommendation are in
[`2026-09-09-host-snapshot-investigation.md`](2026-09-09-host-snapshot-investigation.md).

The watcher itself wakes within 0.100 seconds of publication. Reaching the
changed page's chunk takes up to 0.691 seconds because the cycle still walks and
hash-checks the warm 430-page indexed corpus before it reaches that page. The
final 2.584-second tail includes the 250 ms polling interval, query embedding,
MCP transport, and search execution; it is the observation bound rather than a
claim that PostgreSQL hid a committed row for that duration.

One separate cold-start observation is not part of the distribution: after the
instrumented sync-job container was recreated, an unchanged 430-page vault
cycle took **82.473 seconds** while the raw-corpus watcher started alongside it.
It should inform restart expectations, but it must not be mixed into the warm
single-edit I-3 statistic.

## Passive-history limit

The passive reconstruction produced no defensible latency sample. The retained
host-sync logs contained **2** publication records, while vault history contained
**30** `V3-PROP-*` commits. The only matching cleanup commit was replayed when a
replacement container started roughly 15 hours later. Therefore the historical
push-to-publication result is **N=0**, not an inferred duration.

## Obsidian-to-Git options

The daily vault is not a Git checkout, so none of the choices below has been
selected or demonstrated. This changes the owner's normal editing workflow and
requires his decision.

### Option A — sparse checkout

Clone private Gitea, materialize only `wiki/`, and open `<checkout>/wiki` as the
Obsidian vault. Current Git guidance uses `sparse-checkout set`; the older
`sparse-checkout init` step is deprecated. A setup shaped like this keeps the
remote explicit:

```bash
git clone --filter=blob:none --sparse --branch main \
  http://127.0.0.1:3000/snp-admin/snp-memory.git <private-checkout>
git -C <private-checkout> sparse-checkout set wiki
git -C <private-checkout> remote get-url origin
```

Then open `<private-checkout>/wiki` in Obsidian and use ordinary commits and
pushes. This most closely matches W-2, but changes the folder the owner opens and
still requires explicit Git actions. Git documents both cone-mode directory
selection and the current `set` command in its
[sparse-checkout manual](https://git-scm.com/docs/git-sparse-checkout).

### Option B — manual sync script

Keep the current daily vault and run an explicit script that copies approved
vault paths into a private checkout, shows the diff, commits, and pushes. This is
closest to today's directory layout, but it makes “no operator action required”
false. The script would also need an owner-approved deletion policy and explicit
exclusions for `.obsidian/`, private attachments, and unrelated files before it
could be safe.

### Option C — separate demo checkout

Clone private Gitea into a dedicated demo directory, open that checkout's
`wiki/` folder as a second Obsidian vault, and perform the rehearsal edit there.
This is honest only if narrated as a demo-only workflow. It proves the
downstream system without claiming the owner's daily vault already has a
publication path.

### Option D — sparse checkout plus Obsidian Git

Use Option A, then let the Obsidian Git plugin commit and sync from inside
Obsidian. This is the only listed option that can make “zero operator commands”
literal, but it introduces automatic staging, pull/conflict behavior, credential
storage, and a time-based commit/push interval that the owner must accept. The
plugin's primary documentation confirms that automatic commit-and-sync runs on
a configurable interval and that commit-and-sync stages, commits, pulls, and
pushes by default: [Obsidian Git feature documentation](https://github.com/Vinzent03/obsidian-git/blob/master/docs/Features.md).

Its interval is measured in minutes. Any nonzero interval therefore becomes a
separate lower bound on editor-to-answer latency and makes a sub-five-second
end-to-end claim impossible by construction.

The executable setup, daily flow, safe reversal, and no-action consequences
for every option are in
[`2026-09-09-e11-obsidian-gitea-options.md`](../runbooks/2026-09-09-e11-obsidian-gitea-options.md).

## Ledger identity

The authoritative engine ledger is
`.unlazy/v3-retrieval/gates/engine-2026-09-04.md`. It currently has **15 gate
entries: 13 met, 1 open, and 1 abandoned**. The open gate and abandoned handoff
are both E11: its acceptance box remains open and its `ABANDON` line records
that the owner decision was deferred. Abandonment therefore does not mean E11
closed.

Earlier reports of “11 met, 0 abandoned” and “12 met, 0 abandoned” referred to
the separate execution ledger `/tmp/snp-i3-latency/GATES.md`, but failed to name
it. This post-latency work has another temporary execution ledger at
`/tmp/snp-post-latency/GATES.md`. Future counts must name the ledger; only the
engine ledger speaks for E11's product state.

## Proposed measurable replacement for I-3

I-3 remains unchanged in `docs/DEMO_OPENCODE.md`. The following is a proposal,
not an accepted criterion:

> **I-3: Pushed edit propagates.** With host-sync and sync-job healthy and the
> index warm, a commit changing one Markdown page on the private vault repo's
> `main` is returned by `wiki_search` and confirmed by `wiki_read` within
> **10 seconds of a successful push at p95 over 10 consecutive edits**.

Ten seconds gives about 29% headroom over the measured 7.781-second p95 while
remaining tight enough to expose a regression. The proposed scope explicitly
excludes Obsidian-to-push. If Option D is selected, its configured auto-sync
interval must be a second, separately reported editor-to-push measure; it must
not be hidden inside the ten-second pipeline budget.

## OWNER DECISION REQUIRED

The owner must choose Option A, B, C, or D; decide whether Obsidian-to-push is
inside or outside the product claim; and approve, reject, or alter the proposed
ten-second p95 bar. No option is selected here, and the shipped I-3 wording has
not been changed.
