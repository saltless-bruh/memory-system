# One-page host-snapshot latency investigation

**Measured:** 2026-09-09 02:07–03:30 UTC

**Repository revision measured:** `febfba835ee30ffe86870fc3ddee485988afe5e3`

**Disposition:** retain full immutable snapshots; address Gitea queue wake-up,
not snapshot materialization

## Answer

The 3.900-second p95 span was named too broadly. It is not mainly a
host-snapshot span. For the ten original one-page additions, **3.736 seconds
p95 elapsed before Gitea completed host-sync's first fetch request**, while the
remainder from that point to atomic snapshot publication was only **0.256
seconds p95**.

Host-sync does materialize all 433 files for every new commit. That hypothesis
is true, but it is not the latency problem: archiving and extracting the full
1,889,354-byte `wiki/` tree took **0.133 seconds p50 / 0.209 seconds p95** over
ten runs in the running host-sync container. An incremental snapshot could not
roughly halve the total 7.781-second p95. It could remove at most a few tenths
of a second while adding copy-on-write and deletion correctness risks to an
otherwise simple immutable-publication design.

The dominant wait is in Gitea. A received push passes through two asynchronous
queues before the webhook reaches host-sync:

```text
git receive completes
  -> persistent queue "push_update"
  -> webhook preparation
  -> persistent queue "webhook_sender"
  -> host-sync webhook
  -> fetch + archive + extract + atomic symlink publication
```

The deployment has no queue override, so both queues use Gitea's default
LevelDB implementation. In the pinned Gitea 1.24.7 source, an empty LevelDB
queue polls with exponential backoff from 50 milliseconds to 2 seconds. Two
independently idle queues can therefore contribute almost four seconds. That
matches the live 3.736-second p95 before the first host fetch.

## Live decomposition

The source distribution is the existing ten-sample I-3 run in
`/tmp/snp-i3-latency/active.json`, correlated in order with the retained Gitea
container logs. Every add was followed by its cleanup before the next add, so
the ten odd-numbered host fetches correspond to the ten recorded add commits.
All clocks were on the same Docker host.

`Host fetch seen` is the completion timestamp of Gitea's first successful
`GET ...info/refs?service=git-upload-pack` from the host-sync container. It is
a conservative boundary: the queues and webhook have completed by then, while
the pack transfer and snapshot work remain after it.

| Sample | Push complete -> host fetch seen | Host fetch seen -> snapshot | Push complete -> query | Queue-free replay |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2.218 s | 0.256 s | 6.027 s | 3.809 s |
| 2 | 3.460 s | 0.170 s | 7.781 s | 4.321 s |
| 3 | 3.736 s | 0.164 s | 5.520 s | 1.783 s |
| 4 | 3.452 s | 0.160 s | 6.003 s | 2.551 s |
| 5 | 3.481 s | 0.152 s | 6.362 s | 2.881 s |
| 6 | 3.028 s | 0.165 s | 4.979 s | 1.951 s |
| 7 | 3.137 s | 0.159 s | 5.653 s | 2.517 s |
| 8 | 2.678 s | 0.161 s | 5.181 s | 2.503 s |
| 9 | 3.403 s | 0.150 s | 5.604 s | 2.201 s |
| 10 | 3.443 s | 0.152 s | 5.794 s | 2.351 s |

Nearest-rank summaries at N=10 are:

| Span | Minimum | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Push complete -> host fetch seen | 2.218 s | 3.403 s | 3.736 s | 3.736 s |
| Host fetch seen -> snapshot | 0.150 s | 0.160 s | 0.256 s | 0.256 s |
| Measured push complete -> query | 4.979 s | 5.653 s | 7.781 s | 7.781 s |
| Queue-free replay | 1.783 s | 2.503 s | 4.321 s | 4.321 s |

The queue-free replay subtracts each sample's own push-to-fetch wait from its
own end-to-end latency and then recalculates nearest-rank percentiles. It is a
counterfactual from measured timestamps, not a claim that an unshipped fix was
live-tested. It shows the expected shape if persistent queue pushes wake their
consumers immediately and every downstream span stays the same.

The current five-second wording fails at the **median**: 5.653 seconds against
five seconds. With the queue wait removed, the same samples would be 2.503
seconds p50 / 4.321 seconds p95, but that does not yet prove a five-second
operating bar; it leaves only 0.679 seconds above this ten-sample p95.

For the alternative scope that stops when the changed row is indexed, the
same samples measured **4.954 seconds p50 / 5.290 seconds p95** from push
completion to `row_visible`. Subtracting each sample's own pre-fetch queue wait
produces **1.502 seconds p50 / 2.044 seconds p95**. That scope says nothing
about whether an agent can retrieve and read the page, so it must be named
`push -> indexed row`, not presented as a push-to-answer result.

## Full-snapshot microbenchmark

The running `febfba8` host-sync container supplied the same filesystem,
repository, Git version, Python version, and named volume used in the live
path. A reviewed harness ran ten iterations of the individual operations. It
did not print or inspect page content; it recorded only timing, file count, and
byte count.

The successful command was:

```bash
docker cp /tmp/snp-post-latency/host_sync_microbench.py \
  snp-memory-host-sync-1:/tmp/host_sync_microbench.py
docker exec snp-memory-host-sync-1 python /tmp/host_sync_microbench.py
```

Both commands exited 0. The measured tree held **433 files** and **1,889,354
bytes**. Nearest-rank results were:

| Operation | N | p50 | p95 |
| --- | ---: | ---: | ---: |
| Validate/bind existing remote | 10 | 0.008 s | 0.009 s |
| No-op fetch from local Gitea | 10 | 0.029 s | 0.061 s |
| Resolve commit | 10 | 0.002 s | 0.004 s |
| Archive all of `wiki/` | 10 | 0.023 s | 0.099 s |
| Extract all of `wiki/` | 10 | 0.110 s | 0.117 s |
| Archive plus extract, paired per run | 10 | 0.133 s | 0.209 s |

The live `host fetch seen -> snapshot` result is the stronger end-to-end check
for an actual one-page commit. The microbenchmark explains why that live span
is small even though every page is materialized.

## Upstream status and options

This is a known queue implementation issue, not a repository-specific theory.
Gitea commit
[`d2bc0097`](https://github.com/go-gitea/gitea/commit/d2bc0097bc46dafc8377c43aa36f98460359a84d),
dated **2026-08-22**, adds a notification path so a local persistent queue's
blocked `PopItem` wakes when `PushItem` succeeds. The exact fix touches the
shared queue implementation and tests. It is present on Gitea `main` as of
this investigation.

The current stable release is
[`v1.27.3`](https://github.com/go-gitea/gitea/releases/tag/v1.27.3), published
**2026-08-29**, but its tagged `base_levelqueue_common.go` still has the polling
implementation. Merely changing this repository's image from 1.24.7 to 1.27.3
would therefore incur database migrations without fixing this latency.

Gitea's current
[queue documentation](https://docs.gitea.com/administration/config-cheat-sheet/#queue-queue-and-queue)
lists `level`, `channel`, and `redis` queue types and keeps `level` as the
default. Switching only `push_update` and `webhook_sender` to `channel` would
wake immediately, but those tasks would exist only in process memory. A Gitea
restart between a successful push and handling could lose the publication
trigger. That is the wrong default for W-2, whose point is that a successful
human edit reaches the next answer without recovery work.

## Recommendation

1. **Do not implement incremental snapshots.** Preserve the complete,
   commit-addressed archive/extract and atomic `current` retarget. Its measured
   p95 is not worth weakening that correctness boundary.
2. **Do not ship the `channel` queue workaround.** It buys latency by dropping
   the persistence property that makes accepted pushes recoverable.
3. **Adopt the upstream queue-notification fix after it reaches a stable Gitea
   release**, or backport it in a separately reviewed custom-image change if a
   sub-five-second target is required before then. Either route must rehearse
   Gitea backup/restore and migrations, prove an accepted push survives a
   Gitea restart, and rerun N>=10 live one-page samples.
4. Until that change is live-measured, use **10 seconds at p95** as the honest
   bar for the current shipped push-to-query path. After the queue fix, retest
   and target **6 seconds at p95** rather than assuming the old `<5 s` sentence
   is proven. Six seconds carries 1.679 seconds (38.9%) over the measured
   4.321-second queue-free p95.

The I-3 criterion in `docs/DEMO_OPENCODE.md` remains unchanged pending the
owner decision.

## Commands and exit status

The retained-log parser was:

```bash
python /tmp/snp-post-latency/parse_prior_gitea_timing.py
```

It exited **0** and produced **20 push/fetch pairs**, including **10 one-page
adds** with 3.403-second p50 / 3.742-second p95 from Gitea receive completion to
host fetch completion. The six-millisecond difference from the correlated
3.736-second p95 above comes from using Gitea's receive completion rather than
the client process's slightly later `git push` return.

The per-sample replay was:

```bash
python /tmp/snp-post-latency/counterfactual_latency.py
```

It exited **0**, paired all **10** add samples, and produced 2.503-second p50 /
4.321-second p95 after subtracting the measured pre-fetch wait.
