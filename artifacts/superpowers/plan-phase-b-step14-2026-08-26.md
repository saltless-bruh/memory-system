# Plan — closing Phase B and step 14 (deployment coherence)

**Date:** 2026-08-26 · **Branch:** `fix/architecture-security-hardening`
**Follows:** `plan-residual-risk-2026-08-26.md`, which landed 18/18 and left
exactly these two open, deliberately, because its own no-restart rule forbade
them.

**What this is.** The plan that lifts assumption A4 ("no Dockerfile in this plan
is built"). It is *not* a plan to bring the deployment up to date wholesale —
that is a decision, and this plan's job is to make the decision cheap and
informed rather than to take it.

---

## Two findings that reshaped this plan before it was written

**1. Most of Phase B can be closed with zero restarts.** `docker compose build`
does not start anything, and a built image can be interrogated with
`docker run --rm` — a throwaway container that touches no volume, no database,
and no running service. So *build the image, then prove its installed set equals
its lock* needs no deployment decision at all. That is the verification Phase B
actually lacked, and it is available now.

**2. The rebuilt `sync-job` will refuse to sync, permanently, and that is the
guard working.** Measured:

```
corpus fingerprint (recorded)  tables available 0.11.10 · figures available 12.3.0 · py 3.14
scout container (now)          pdfplumber ABSENT · Pillow ABSENT
rag_chunks by kind             text 107 · figure 25 · table 8   (140 total)
```

The corpus was built on the host **with** both extractors; the deployed image
deliberately has neither (T5.1, decided 2026-08-25). The new image contains
`corpus_fingerprint_mismatch`, so its first `sync_once` finds
`tables available -> unavailable` and raises rather than re-ingesting. Nothing is
lost — and nothing is synced, ever again, until somebody decides.

So Phase B does not merely need a build. **It surfaces a decision T5.1 deferred**,
and this plan's Phase I is that decision, costed rather than guessed.

---

## What the 2026 search contributes, and what it cannot

**The `-dirty` suffix is the standard answer to the stamp problem.** The blocker
recorded in the last finish doc was that `SNP_GIT_REVISION=$(git rev-parse HEAD)`
stamps a clean-looking commit onto an image built from a tree with 200+
uncommitted files. Docker Buildx solved this the same way: it appends `-dirty`
to `org.opencontainers.image.revision` when the source is not clean
([docker/buildx#1297](https://github.com/docker/buildx/pull/1297)). Adopting that
convention **unblocks the build without needing the commit hold lifted** — the
image says what it is.

**Bit-for-bit reproducibility is not available here, and the plan does not
pretend otherwise.** The 2026 mechanism is `SOURCE_DATE_EPOCH` plus
`--output type=docker,rewrite-timestamp=true`, which needs BuildKit ≥ v0.13
through Buildx ([buildkit build-repro docs](https://github.com/moby/buildkit/blob/master/docs/build-repro.md)).
Measured on this host: `docker buildx` is **not installed** — the only CLI plugin
present is Compose 5.5.0. Two builds here will differ in timestamps, so
"reproducible" cannot be claimed and is not a step. What *is* verifiable is the
thing the pins were for: **the built image's installed set equals the lock**.
That is a stronger claim about supply chain than a matching digest would be, and
it is available.

**Runner registration needs an admin token from the Gitea UI** — Site
Administration → Actions → Runners
([Gitea runner setup](https://blog.ayjc.net/posts/gitea-actions-runner/)), which
this checkout cannot mint. The security guidance is the same one already recorded
in OD-3: a job that can reach the Docker socket can usually become root on the
host, so use it for private repositories and never for untrusted pull requests.

---

### Goal

1. Build all three images from the pinned digests and hash-locked requirements,
   and **prove the pins are correct** by comparing each built image against its
   own lock.
2. Make the revision stamp honest on a dirty tree, so a build is possible under
   the standing hold and the resulting image cannot be mistaken for a clean one.
3. Record the container-side parser golden that could not honestly exist before.
4. Put the capability decision in front of the owner **with its cost measured**,
   not described.
5. Get `auto-healer.yaml`'s scheduled path exercised on demand, or state exactly
   what blocks it.

**Non-goals:** bit-for-bit reproducibility (no Buildx); lifting the push hold;
taking the capability decision; Docling (T5.1's own note about the 2026 parser
answer stays a future item); OD-1; T4.1.

### Assumptions

- **B1. Building is not deploying.** `docker compose build` creates images and
  starts nothing. Every step through Phase H uses `build` and `docker run --rm`
  only. The running stack is untouched until Phase I, which is gated on a
  decision.
- **B2. `docker run --rm` on the new image is safe** as long as it mounts no
  volume that the live stack writes and receives no database credentials. Steps
  that interrogate an image pass neither.
- **B3. The corpus is 140 chunks — 107 text, 25 figure, 8 table.** Verified
  against the live database. Any option that re-ingests through the current
  image loses the 33 non-text chunks.
- **B4. The wiki does not cite table or figure locs.** Verified: the five pages
  cite `p.3`, `p.6`, `p.12`, `p.17`, `p.18`; table and figure chunks carry
  suffixed locs (`p.4 (Table 1) (1/3)`). So option B breaks no address
  *directly* — but hints were minted against the whole index, and removing 24%
  of it can move rankings. That is measurable, and Phase I measures it rather
  than assuming either way.
- **B5. Gitea has a repository, and it is stale.** `snp-admin/snp-memory`,
  Actions enabled, `main` at `92f5b42b` from **2026-08-18** — none of this work.
  Step 14 therefore needs a push, which is under the standing hold.

---

## Plan

### Phase H — build, and prove the pins (no restarts, no decisions)

**1. Make the revision stamp honest on a dirty tree**
   - Files: `docker-compose.yml`, `docs/runbook.md`
   - Change: document and use
     `SNP_GIT_REVISION="$(git rev-parse HEAD)$(git diff --quiet HEAD || echo -dirty)"`.
     The image then carries `2d2b9dd…-dirty`, which is true, instead of a clean
     SHA that is a lie about 200+ uncommitted files.
   - Verify: on the current tree the computed value ends in `-dirty`; with a
     clean tree (simulated in a scratch clone) it does not.

**2. Teach the preflight what a dirty stamp means**
   - Files: `scripts/preflight_stack.py`, `tests/test_preflight_stack.py`
   - Change: `classify_image_revision` currently does an exact match, so
     `2d2b9dd-dirty` against HEAD `2d2b9dd` reports the nonsense *"image was
     built from 2d2b9dd, checkout is at 2d2b9dd"*. Three outcomes instead:
     clean stamp equal to HEAD → **ok**; dirty stamp whose base equals HEAD →
     **unverifiable**, because the tree may have changed since the build; any
     other → **stale**, as today.
   - Verify: a unit test per branch; the nonsense message is impossible.

**3. Build the three images**
   - Files: none
   - Change: `SNP_GIT_REVISION=… docker compose build scout basic-memory host-sync`
   - Verify: three builds succeed. **A failure here is the finding** — it means a
     digest, a hash, or an apt pin written in the last pass was wrong, which is
     exactly what A4 said would surface at this build and nowhere earlier.

**4. Prove each image installed exactly what its lock says**
   - Files: `tests/test_supply_chain_pins.py`
   - Change: for each image, `docker run --rm --entrypoint pip <image> freeze`
     compared package-by-package against its lock — the same comparison that
     generated them, run in the opposite direction.
   - Verify: 70 / 163 / 13 packages, zero missing, zero extra, zero version
     differences. **This is the verification Phase B was missing**, and it says
     more than a reproducible digest would: not "the build repeated" but "the
     build installed what was locked."

**5. Prove the base digest and the apt pins landed**
   - Files: none
   - Change: `docker image inspect` each new image for its base layer;
     `docker run --rm --entrypoint dpkg-query snp-memory-host-sync -W …`.
   - Verify: base digest matches `sha256:57cd7c3a…`; `git` and `curl` report
     `1:2.47.3-0+deb13u1` and `8.14.1-2+deb13u4`.

**6. Record the container-side parser golden**
   - Files: `tests/fixtures/parser-golden/` (one new file)
   - Change: `docker run --rm -v "$PWD/raw:/app/raw:ro" snp-scout python -m
     scout.parser_golden_record`. The new image has the current parser and
     neither extractor, so this records the `r2_figures-absent_tables-absent`
     branch — the one that could not be recorded before, because the old image
     predates `scout.references` and would have encoded a stale parser.
   - Verify: two goldens exist with different keys; the host suite still passes;
     the recorded fingerprint reports both extractors unavailable.

### Phase I — the decision Phase B surfaces (measure, then stop)

**7. Measure what a container re-ingest would actually produce**
   - Files: `artifacts/superpowers/capability-decision-2026-08-26.md` (new)
   - Change: `docker run --rm` the new image against a **copy** of the corpus
     with `--dry-run`, and record what it would emit: section count, kinds, and
     which of the 140 chunks have no counterpart.
   - Verify: the numbers come from a run, not arithmetic. Nothing writes to the
     live database — dry-run and no ingest credentials (B2).

**8. Measure whether losing 24% of the index moves retrieval**
   - Files: same artifact
   - Change: the five minted hints are the thing at risk (B4). Re-run
     `verify-addresses` against a **restored copy** of the database with the 33
     non-text chunks removed, and record PASS/FAIL/DRIFT per page.
   - Verify: a snapshot is taken first and restored after; the live database is
     byte-identical afterwards, checked by row counts and a chunk checksum.
   - **If a snapshot/restore cycle cannot be done safely, this step does not
     run and the decision is presented without it** — an unmeasured guess is
     better than a measurement taken by damaging the thing being measured.

**9. Put the three options up, with the measurements attached**
   - Files: `docs/REMAINING_TASKS.md` (T5.1), `docs/ARCHITECTURE_STATUS.md`
   - The options, stated now so the plan is honest about where it stops:

     | | Option | Cost | Effect |
     | --- | --- | --- | --- |
     | **A** | Add `pdfplumber` + `pillow` to the scout image | image size; SH-7 parser attack surface; reverses the 2026-08-25 decision | host and container agree; 140 chunks kept; sync resumes |
     | **B** | Re-ingest through the container as-is | loses 33 chunks (24%); retrieval effect measured in step 8 | deployment coherent, corpus smaller |
     | **C** | Leave `sync-job` refusing | none to the data | **Nhịp A auto-ingest is dead**; ingestion becomes a manual host act |

   - **Recommendation: A.** The reason T5.1 gave for excluding them —
     *"grows the image and widens the parser attack surface for one document"* —
     is undercut by its own corpus: those extractors produced **33 of the 140
     live chunks**, and the exposure was not avoided by leaving them out of the
     image, only relocated to an unaudited host. Option C is the honest fallback
     if the size or surface cost is unacceptable, but it must then be *written
     down* that auto-ingest is off, rather than discovered when a document does
     not appear.
   - Verify: whichever is chosen is recorded with its measurement; **this plan
     stops here and does not execute the choice.**

### Phase J — step 14, and what actually blocks it

**10. Inventory what a real dispatch needs, before asking for anything**
   - Files: `docs/runbook.md`
   - Change: enumerate the preconditions — `workflow_dispatch` on the workflow
     (**done**, last pass); the workflow present on the Gitea repo (**absent** —
     `main` is at `92f5b42b` from 2026-08-18); a registered runner (**none**);
     Gitea secrets `BOT_TOKEN`, `POSTGRES_QUERY_PASSWORD`, `LITELLM_MASTER_KEY`
     and the `vars.*` fallbacks; and a runner host willing to hold the socket.
   - Verify: each precondition marked present or absent with the evidence.

**11. Ask for the two things this checkout cannot produce**
   - Change: a runner **registration token** (Site Administration → Actions →
     Runners) and a decision on pushing the branch to the **local Gitea only**.
   - Verify: neither is invented or worked around. A registration token cannot
     be minted from here; a push is under the standing hold and is the owner's.
   - **This is a blocking dependency and the plan says so** rather than
     substituting a local simulation and calling it verification.

**12. (Gated on 11) Bring the runner up and register it**
   - Files: `config/gitea/runner-config.yaml`, runbook
   - Change: `docker compose --profile runner up -d gitea-runner` with
     `GITEA_RUNNER_REGISTRATION_TOKEN` supplied out of band.
   - Verify: `docker logs` shows successful polling, and the runner appears in
     Gitea's runner list. **This is the moment OD-3's latent exposure becomes
     live** — noted in the decision record at the same time, not afterwards.

**13. (Gated on 11) Dispatch once and observe**
   - Change: trigger `auto-healer.yaml` via `workflow_dispatch` on the branch.
   - Verify: the run reaches `ci_address_gate.py --mode scheduled`; the
     groundedness preflight is visible in the log; the outcome (heal PR opened,
     or clean no-op) is recorded with the run id. If `BOT_TOKEN` lacks a scope,
     **that failure is the deliverable** — it answers OD-2 empirically, which
     reading the settings page only approximates.

### Phase K — close out

**14. Report, and mark what moved**
   - Files: `artifacts/superpowers/finish-phase-b-step14-2026-08-26.md`
   - Verify: `pytest -m 'not integration' --disable-socket -q`, `ruff`, `mypy`
     clean; the two new pin tests green; both parser goldens present;
     `preflight_stack` reports the new images correctly; the live corpus row
     counts unchanged from B3 unless Phase I was executed by explicit decision.

---

### Risks & mitigations

- **R1 — A build failure is a success of the previous plan, not of this one.**
  If step 3 fails, the pins written under A4 were wrong and shipped unverified.
  Mitigation: it fails at a build, not in production, and nothing is deployed
  from it. This is the risk A4 named; realising it is the point.
- **R2 — The three images are rebuilt but the running containers keep the old
  ones.** Between step 3 and any decision, `docker compose ps` shows old
  containers against new images — the exact staleness `preflight_stack` exists
  to catch. Mitigation: steps 1–2 make the stamp honest first, so the preflight
  reports *unverifiable* rather than *healthy*.
- **R3 — Step 8 could damage what it measures.** Removing chunks to see whether
  retrieval degrades is a destructive experiment. Mitigation: it runs against a
  restored copy, with a checksum of the live table before and after; if that
  cannot be done safely the step is skipped and the decision is presented
  without it, explicitly.
- **R4 — Phase J turns a latent exposure live.** Registering the runner gives a
  container the Docker socket on a machine holding credentials. Mitigation:
  OD-3's revisit condition is exactly this; the decision record is updated at
  the moment it changes, and `pr-heal` still never executes PR content.
- **R5 — No reproducibility claim is available.** Without Buildx there is no
  `rewrite-timestamp`, so two builds differ. Mitigation: the claim made is the
  one that can be proved — installed set equals lock — and the gap is stated
  rather than implied closed.
- **R6 — `--require-hashes` is all-or-nothing.** If any transitive dependency is
  missing from a lock, the build fails outright rather than installing it.
  Mitigation: the locks came from `pip freeze` of the running containers, so the
  closure is complete by construction; step 3 is where that is tested.

### Rollback plan

- **Phase H** — steps 1, 2, 6 are ordinary reverts. Step 3 creates images and
  changes no running container; the previous images remain until something is
  restarted, so rollback is *doing nothing*.
- **Phase I** — steps 7 and 8 write no repository code and no live data. Step 9
  writes documentation. Nothing to roll back.
- **Phase J** — `docker compose --profile runner down` removes the runner and
  returns OD-3 to latent. A dispatched run that opens a heal PR is reviewable
  and closable; it cannot merge itself (R-6.4/R-7.3).

**Nothing in this plan modifies `raw/`, `wiki/`, or the live corpus.** Phase I
measures against copies and stops at a recommendation.

### Decisions needed

1. **The capability decision (Phase I).** *Recommended: A — put `pdfplumber` and
   `pillow` in the scout image.* The measurements from steps 7 and 8 land before
   this is answered, and C is a legitimate answer provided it is written down.
2. **Push the branch to the local Gitea (step 11).** *Recommended: yes, Gitea
   only, not GitHub.* It is the only way to exercise the scheduled path, the
   instance is self-hosted, and the GitHub hold is untouched. If the answer is
   no, step 14 stays open with its blocker named — which is still better than
   the current state, where it was open with no blocker named.
3. **A runner registration token (step 11).** Cannot be produced from this
   checkout.
4. **Whether to install Buildx** on the runner host, if reproducible digests are
   wanted later. Not needed for anything in this plan.
