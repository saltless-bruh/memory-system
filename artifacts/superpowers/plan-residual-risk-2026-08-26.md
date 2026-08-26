# Plan — supply-chain hardening (Codex's residual-risk review)

**Date:** 2026-08-26 · **Branch:** `fix/architecture-security-hardening`
**Revision:** 3 — amended after Codex's second review ("approve after five
corrections"). Revision 2 fixed a step that would have caused the data loss the
plan existed to prevent; revision 3 fixes work that was **declared but not
wired**.

**What this is.** A hardening plan. It removes one exposure and makes two guards
enforce themselves. **It is not a production-readiness plan**, and completing it
would not close the release blockers — deployment coherence, real basic-memory
coverage, the multilingual decision, and the source-health lifecycle. Those are
named in *What this plan does not close* and left as decisions.

---

## What changed in revision 3

Five corrections, each verified against the tree before accepting:

| # | Correction | Verified |
| --- | --- | --- |
| 1 | The lock file was created but nothing installed from it | **confirmed** — `scout/Dockerfile:15` installs `requirements.txt`, and step 6 never listed the Dockerfile. basic-memory's transitives were out of scope entirely |
| 2 | "Append-only" was a property of the code, not the database | **confirmed, and worse** — `002:19` grants `rag_ingest_role` `SELECT, INSERT, UPDATE, DELETE`, and `003` gives it `FOR ALL … USING(true)` |
| 3 | The PR job pushes; it is not read-and-comment | **confirmed** — `auto-healer.yaml:108` `git push origin HEAD:$PR_HEAD_REF`. `workflow_dispatch` is absent from that workflow |
| 4 | An unknown parser environment would skip, not fail | **confirmed** — `pytest -q` already reports **21 skipped**; a 22nd is invisible |
| 5 | The "complete inventory" was not complete | **confirmed** — `install-agent.sh:36` clones the default branch, and `:6` documents piping that installer from `main` into `bash` |

Plus the note on sources, accepted in its stronger form: the research section is
**removed**, not re-cited. The decisions it was propping up are justified from
this repository instead, where the evidence actually is.

---

## The exposure, measured here

```
.gitea/workflows/auto-healer.yaml    on: schedule (weekly), runs-on: self-hosted
                             :142    curl -LsSf https://astral.sh/uv/install.sh | sh
docker-compose.yml           :322    act_runner: /var/run/docker.sock:/var/run/docker.sock
.gitea/workflows/auto-healer.yaml    :108, :169  git push … / POST /pulls
```

Composed: one unpinned install script, fetched weekly and unattended, on a
runner with a root-equivalent socket, on a machine holding push credentials.
Every other item on Codex's list is a limitation. This one is a path.

**Corrected at step 1, because the first version overstated it.** `act_runner` is
declared in Compose and **has never been brought up on this host** — no
container, no local image. So the chain is fully assembled in the repository and
fires the first time someone runs `docker compose up act_runner`; it is not
currently executing. Still worth removing — a latent root-equivalent path is a
path — but it is not an active compromise, and revision 3 implied it was.

**Unpinned inputs, inventory — revision 3, with the class revision 2 missed:**

```
9 action references           actions/checkout@v4, actions/setup-python@v5, astral-sh/setup-uv@v5
4 third-party images          gitea/gitea:1.24, litellm:main-stable, pgvector/pgvector:pg16,
                              gitea/act_runner:0.2.11   <- omitted until step 1 counted them
3 Dockerfile base images      FROM python:3.12-slim  (basic-memory, scout, scripts/Dockerfile.sync)
1 apt install                 apt-get install -y git curl        <- and the repo state behind it
1 pip install                 pip install fastapi uvicorn        <- no versions at all
4 dependency ranges           watchfiles>=0.21, asyncpg>=0.29.0, pypdf>=4.0.0, pyyaml>=6.0
1 transitive closure          basic-memory==0.22.1 is pinned; what it pulls is not
1 installer clone             install-agent.sh:36  git clone --depth 1 …  (default branch, no ref)
```

`litellm:main-stable` deserves naming separately: it is a **rolling tag off
`main`**, so today's gateway and next week's are unrelated builds.

A pinning trap worth stating because it looks like pinning: the tag ref and the
commit are different objects. `astral-sh/setup-uv@v5` resolves to `e58605a…` as
a tag object and `d4b2f3b…` as a commit. Only the second is a pin.

**Two things this plan deliberately does not do, justified from here rather than
from the literature:**

- **It generates no build provenance.** This is an internal, self-hosted tool
  with no external consumers; an attestation nobody outside this repository
  verifies is ceremony. Pinning inputs and removing network fetches from builds
  is what actually narrows the path above.
- **It does not remove the Docker socket.** `act_runner` requires it, and every
  rootless alternative is a **runner-host** change, not a repository one — out of
  scope for a plan that touches this checkout. Step 14 records it as a decision
  with its cost, which is different from nobody having looked.

---

### Goal

Remove the one exposure, pin every input a build or workflow executes, and make
the two guards that depend on human discipline enforce themselves. Concretely:

1. No workflow executes code fetched from an unpinned source.
2. Every external action pinned to a **commit** SHA; every third-party image and
   **base image** to a digest; every runtime dependency hash-locked **and
   actually installed from that lock**.
3. A parser behaviour change cannot claim compatibility — and cannot hide in a
   skip.
4. A capability override is high-friction and leaves a record the **database**
   will not let the writer alter.
5. No comment claims a health endpoint answers a question it cannot.
6. The Docker socket and the real credential's scope are recorded decisions.

**Non-goals:** provenance generation; removing the socket; OD-1; the compile
budget; Tier 5's bad-source lifecycle; and — stated because revision 1 blurred
it — anything that requires the stack to be rebuilt or re-ingested.

### Assumptions

- **A1.** Commit SHAs resolved 2026-08-26: `actions/checkout@v4` →
  `11d5960a326750d5838078e36cf38b85af677262`; `actions/setup-python@v5` →
  `a26af69be951a213d495a4c3e4e4022e16d87065`; `astral-sh/setup-uv@v5` →
  `d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86`. Step 1 re-resolves rather than
  trusting this list.
- **A2.** Image digests are taken from the copies **running and healthy now**,
  not from a registry lookup of what a tag means today. Confirmed available:
  `docker image inspect python:3.12-slim` → `sha256:57cd7c3a…`.
- **A3.** **No step restarts a container or re-ingests.** Verified why this
  matters: `scout/sync_job.py::_async_main` runs a full `sync_once` on start, and
  the running image has no `CapabilityMismatchError`, so a restart would
  re-ingest with the pre-`references` parser — restoring the 23 bibliography
  chunks and dropping the tables, unrefused. Every verification here is static.
  `docker exec` and `docker image inspect` read a running container without
  restarting it; both are confirmed working on all three Python services.
- **A4.** **No Dockerfile in this plan is built.** A3 forbids it. So every
  Phase B change is verified statically here, and *"the images build from the
  pinned inputs"* is an acceptance criterion of the clean release build, listed
  in step 15. Phase B that is written but unbuilt is stated as such rather than
  reported as proven.
- **A5.** No step needs a model call.

---

## Plan

### Phase A — the exposure (item 9)

**1. Record the pins before changing anything**
   - Files: `artifacts/superpowers/supply-chain-pins-2026-08-26.md` (new)
   - Change: for each action the **commit** SHA (not the tag object); for each
     third-party image and base image the digest of the copy in use now; for each
     unpinned dependency its currently-installed version, read from the running
     container.
   - Verify: every action SHA resolves via `/repos/{repo}/commits/{tag}`; every
     digest matches `docker image inspect --format '{{index .RepoDigests 0}}'`.
     **No container is started or restarted** — `inspect` and `exec` only.

**2. Remove the `curl | sh` from the workflow that pushes**
   - Files: `.gitea/workflows/auto-healer.yaml`
   - Change: replace line 142's `curl -LsSf … | sh` with the pinned
     `astral-sh/setup-uv` action; keep `uv sync --extra dev` as its own step.
   - Verify: no `curl … | sh` remains in `.gitea/workflows/`; YAML parses; step
     list otherwise unchanged. **The highest-value step here** — it is the link
     that makes the other two into a chain.

**3. Pin every action to a commit SHA, and keep it pinned**
   - Files: all three `.gitea/workflows/*.yaml`
   - Change: `uses: owner/action@<40-hex>  # vN`.
   - Verify: a test asserts every `uses:` matches `@[0-9a-f]{40}`. **The test is
     the deliverable** — a one-time pin decays the moment somebody adds a
     workflow.

**4. Pin third-party Compose images to digests — statically verified**
   - Files: `docker-compose.yml`
   - Change: `image: name:tag@sha256:…`, keeping the readable tag. **All four**,
     including `act_runner` — step 1 found revision 3's table listed three and
     silently dropped the one the exposure narrative is about. Its digest is the
     one value in this plan taken from a registry rather than a running copy,
     because no copy exists here; that deviation from A2 is recorded in the pins
     file rather than hidden by using the same wording as the other three.
   - Verify: **`docker compose config` only.** Revision 1 said "`up -d`, seven
     services healthy", which would restart `sync-job`, whose cold-start
     `sync_once` would re-ingest the host-mounted corpus using the old image —
     the exact re-ingestion the previous plan stopped, and its "no data changes"
     claim was false. Dynamic verification moves to step 15.
     A test asserts every non-locally-built `image:` carries `@sha256:`.

### Phase B — the inputs those images are built from (Codex #1)

Revision 2 pinned what Compose pulls, then wrote a lock nothing installs from.
`snp-scout` backs three of the seven services.

**5. Pin the base images by digest**
   - Files: `scout/Dockerfile`, `basic-memory/Dockerfile`,
     `scripts/Dockerfile.sync`
   - Change: `FROM python:3.12-slim@sha256:57cd7c3a…` in all three, digest read
     from the layer the current images were built on.
   - Verify: a static check that all three `FROM` lines carry a digest, plus the
     record of which digest and when. Not rebuilt (A4).

**6. Generate the locks from what is deployed, not from what resolves today**
   - Files: `scout/requirements.lock` (new), `basic-memory/requirements.lock`
     (new), `scripts/sync-service.lock` (new)
   - Change: for each of the three images, read the installed set out of the
     **running container** (`docker exec … pip freeze`), then compile it to a
     hash-pinned lock. Mechanism, because it is the trap in R3:
     `uv pip compile --generate-hashes --python-version 3.12 --python-platform
     linux` — compiling on the host without those flags selects host wheels,
     whose hashes the container build would then reject.
     This closes basic-memory too: `basic-memory==0.22.1` is a direct pin whose
     transitive closure was never fixed.
   - Verify: every entry in each lock carries `--hash=sha256:…`; every version
     matches what the corresponding running container reports. The locks
     describe the deployment, so a mismatch is a finding about the deployment.

**7. Install from the locks — the step revision 2 omitted**
   - Files: `scout/Dockerfile`, `basic-memory/Dockerfile`,
     `scripts/Dockerfile.sync`, `scout/requirements.txt`
   - Change:
     - `scout/Dockerfile:15` → `COPY scout/requirements.lock` and
       `pip install --no-cache-dir --require-hashes -r /app/requirements.lock`.
     - `basic-memory/Dockerfile:12` → install from its lock, keeping
       `--prefer-binary` (without it pip builds litellm's Rust sdist and fails).
     - `scripts/Dockerfile.sync:4` → `pip install fastapi uvicorn` carries **no
       version at all**; install from its lock.
     - `scout/requirements.txt` keeps the four ranges as the *human* input to
       recompiling the lock, and says so in a comment; nothing builds from it.
   - Verify: no build step in the repository installs from anything but a
     `--require-hashes` lock. A test asserts it, so a future Dockerfile cannot
     quietly reintroduce a loose install. **First actual build is step 15's
     deferred criterion** (A4).

**8. Pin apt, and say what pinning apt does not buy**
   - Files: `scripts/Dockerfile.sync`, `docs/runbook.md`
   - Change: `apt-get install -y git=<ver> curl=<ver>`, versions read from the
     running container.
   - **The limitation, recorded rather than papered over:** Debian's mirrors drop
     superseded versions, so a version pin against a rolling mirror eventually
     fails to resolve. `snapshot.debian.org` is the mechanism that makes the
     repository state reproducible, and it is **not** adopted here for one
     reason: A4 means this plan cannot build the image, and an untested archive
     URL in a Dockerfile is precisely the kind of change that looks complete and
     is not. It is written up as a release-build candidate.
   - Verify: the versions match the running container; the runbook states that
     package versions are pinned and repository state is not, and names
     `snapshot.debian.org` as the open option. Trade accepted knowingly: a pin
     that stops resolving fails **loudly**, where the range drifts silently.

**9. Say what "pinned" now means, and what it still does not**
   - Files: `docs/runbook.md`
   - Change: an inventory of every pinned input and how to bump one. State
     plainly that pinning removes silent drift and **adds staleness** — nothing
     here keeps a pin fresh, and a stale pin is its own risk.
   - Verify: every pinned input in the repository appears in the inventory; the
     docs contract test passes.

### Phase C — guards that enforce themselves (Codex #4, #2)

**10. A parser change cannot silently claim compatibility — and cannot hide in a skip**
   - Files: `tests/fixtures/parser-golden/` (new), `tests/test_capabilities.py`,
     `.gitea/workflows/checks.yaml`
   - Change:
     - Golden hashes of the **parsed structure** (section locs, text, metadata
       statuses) — not raw bytes, the same reasoning that made the plan-hash
       guard hash parsed articles.
     - One expected hash **per `capability_fingerprint`**: host and container
       differ by design, so a single hash is ambiguous.
     - An unknown fingerprint **skips locally and fails in the canonical
       environment** (Codex #4). Local development gets an explanatory skip; CI
       sets `SNP_CANONICAL_ENV=1` and the test calls `pytest.fail` instead. The
       reason is measured: the suite already reports **21 skipped**, so a 22nd is
       invisible — a guard that can be satisfied by not running is not a guard.
   - Verify: change a parser behaviour → the test fails naming `PARSER_REVISION`;
     bump and re-record → green. Then run the unknown-fingerprint case both ways
     and confirm skip-locally / fail-under-`SNP_CANONICAL_ENV`.

**11. An append-only ingest-event record the writer cannot rewrite**
   - Files: `config/postgres/migrations/005_ingest_events.sql` (new),
     `scout/ingest.py`, `tests/test_ingest_events.py` (new)
   - Change: revision 1 stored "overrode previous fingerprint" inside the
     document fingerprint, which the next ingest overwrites. Revision 2 replaced
     it with a table and called it append-only because no code updates it —
     which, as Codex says, is not a guarantee. **It is now a grant.** Measured
     first: `002:19` grants `rag_ingest_role` `SELECT, INSERT, UPDATE, DELETE`
     and `003` adds `FOR ALL … USING(true) WITH CHECK(true)`, so a table
     following the house pattern would be fully mutable by the writer.
     Migration 005 therefore:
     - `GRANT INSERT, SELECT ON ingest_events TO rag_ingest_role;` followed by an
       explicit `REVOKE UPDATE, DELETE, TRUNCATE`, so a later blanket grant does
       not silently re-open it;
     - `ENABLE` + `FORCE ROW LEVEL SECURITY` with an `INSERT`-only policy
       (`WITH CHECK (true)`) and a `SELECT` policy — matching how `002`/`003`
       already constrain the other two tables;
     - `event_id bigint GENERATED ALWAYS AS IDENTITY` — **not `bigserial`**: a
       serial needs a separate `GRANT USAGE` on its sequence, and omitting it
       makes every insert fail at runtime.
     - **`actor` is defined, not passed.** `actor text NOT NULL DEFAULT
       session_user` — the authenticated database identity, which the client
       cannot forge. The workflow/run context goes in a separate nullable
       `actor_hint`, documented as **unverified caller-supplied text**. Codex is
       right that an environment variable is not an audit identity; the fix is to
       stop calling it one.
   - Verify, as `rag_ingest_role` over a real connection: an override INSERT
     succeeds; `UPDATE` and `DELETE` on that row **raise insufficient privilege**;
     a subsequent normal ingest leaves the row byte-identical.
   - **The honest limit, stated in the migration:** `postgres` is superuser and
     bypasses both the grant and RLS. This makes the record immutable *to the
     ingest identity*, which is the actor the audit is about — not to a DBA.

**12. A distinct acknowledgement, not `--confirm`**
   - Files: `scout/cli/commands/ingest.py`, `scout/cli/declarations.py`,
     `tests/test_cli_ingest.py`
   - Change: `--confirm` is already required for any write, so requiring it again
     acknowledges nothing. The override takes
     `--acknowledge-capability-change=<the mismatch text the refusal printed>` —
     a value the caller can only supply by having read the specific mismatch.
   - Verify: `--allow-capability-change` alone is refused; a wrong or stale
     acknowledgement is refused; the correct one proceeds and writes the audit
     row; a normal ingest writes none.

### Phase D — the credential that actually matters (Codex #3, #5)

**13. Inventory the real token before restricting the wrong one**
   - Files: `docs/runbook.md`, `docs/ARCHITECTURE_STATUS.md`
   - Change: revision 1 proposed job-level `permissions:`, which governs the
     **ambient Actions token**. The healer authenticates with `secrets.BOT_TOKEN`
     at lines 57, 127, 132 and 167, including the direct `POST /pulls`. So the
     proposed mitigation would not have restricted the credential in use.
     Inventory `BOT_TOKEN`'s actual Gitea scopes and record them.
   - Verify: the record names the scopes the token holds and which each job
     needs; any gap is stated rather than assumed absent.

**14. Scope the tokens to what each job actually does**
   - Files: `.gitea/workflows/auto-healer.yaml`, runbook
   - Change, corrected — **both jobs push** (Codex #3):
     - `pr-heal` pushes healed Markdown to the contributor's branch
       (`:108 git push origin HEAD:$PR_HEAD_REF`). It needs repository write. It
       does **not** create pull requests.
     - `scheduled-sweep` pushes a `heal/*` branch **and** creates a PR through
       the API (`:169`).
     - So the split, if Gitea can express it, is *write without PR creation* vs
       *write with it* — which the step 13 inventory answers. A single token is
       an acceptable outcome, recorded as such.
     - Add `workflow_dispatch:` to the workflow's `on:` — **it is absent**, so
       the manual verification below is currently impossible. (`checks.yaml` and
       `security.yaml` already have it.)
     - Job-level `permissions:` narrow the **ambient** token — plausibly
       `contents: read` for both, since every push uses `BOT_TOKEN` — and are
       described as exactly that, never as restricting `BOT_TOKEN`.
     - `persist-credentials: false` is **already correct** and needs no change:
       `:52` has it on the trusted checkout, and the other three checkouts push.
   - Verify: **manually, on demand.** A weekly cron cannot verify itself
     promptly, so the scheduled path is triggered by hand via `workflow_dispatch`
     and observed once, and the result recorded.

**15. The installer path, brought into scope or named (Codex #5)**
   - Files: `scripts/install-agent.sh`, `docs/CONNECT_AGENTS.md`,
     `docs/REMAINING_TASKS.md`
   - Change: `:36` clones the default branch with no ref, and `:6` documents
     piping that same file from `main` into `bash` — an unpinned remote-execution
     path that revision 2's "complete inventory" missed.
     - Add `--branch "${SNP_AGENT_REF:-main}"` to the clone and echo the resolved
       commit, so an install is at least *identifiable* after the fact.
     - **Do not pretend it is pinned.** The repository has **0 tags**, so there is
       no release to pin to; a SHA default would freeze every future curl install
       on today's code. The real fix is to tag releases and default `SNP_AGENT_REF`
       to the latest tag.
   - Verify: the installer prints the commit it installed from; the docs and
     `REMAINING_TASKS.md` carry the unpinned-installer entry with tagging named
     as its precondition.

### Phase E — stop claiming what cannot be answered (item 6)

**16. Correct the health claim**
   - Files: `config/litellm/config.yaml`, `docs/runbook.md`
   - Change: line 50 claims an on-demand `GET /health` still probes the judge. It
     does not — that endpoint serves the cached background result and this route
     is excluded from the loop, so it reports 503 regardless. Replace with:
     health is **unknown, not unhealthy**, and the way to ask is
     `verify_groundedness --probe`, which exists.
   - Verify: no surviving claim that `/health` probes `snp-judge`; the runbook
     names the probe.

### Phase F — decisions, stated rather than absorbed

**17. Record the trust boundary and fold the rest into the backlog**
   - Files: `docs/ARCHITECTURE_STATUS.md`, `docs/REMAINING_TASKS.md`
   - Change: an OD entry for the Docker socket — what it grants, why
     `act_runner` has it, that the alternatives are runner-host changes, and that
     the mitigation taken was removing the unpinned install and pinning inputs.
     Map Codex's items 1, 2, 3, 5, 7, 8 onto existing backlog entries (T3.3,
     OD-1, T4.1, T4.3, Tier 5), each marked **owner decision** or **tracked
     risk** — never "todo".
   - Verify: each of the nine appears once, findable by number.

### Phase G — close out

**18. Static verification now; every build and every restart deferred**
   - Files: `artifacts/superpowers/finish-residual-risk-2026-08-26.md`
   - Verify **now, statically**:
     - `pytest -q` green; `ruff check`, `ruff format --check`, `mypy` clean.
     - `docker compose config` resolves with every third-party image digested.
     - No `uses:` without a 40-hex SHA; no unpinned `FROM`; no install that is not
       `--require-hashes`; no `curl … | sh` in any workflow.
     - Migration 005 applied; the privilege test passes against live Postgres.
   - **Deferred to the clean release build** (A3, A4), written down as that
     build's acceptance criteria rather than performed here:
     1. all three images **build** from the pinned digests and locks;
     2. the stack comes up on the pinned digests, seven services healthy,
        `preflight_stack.py` clean;
     3. `snapshot.debian.org` evaluated for `Dockerfile.sync` with a real build
        behind it.

---

## What this plan does not close

Stated because Codex's verdict is right and the distinction matters: **9/10 as
hardening, 4/10 as production readiness.** Completed in full, this plan leaves
every release blocker open.

| Blocker | Why it stays open |
| --- | --- |
| **Deployment coherence** | The running images predate this work; the corpus was built on the host. Needs a commit, a clean image build, and a container-side re-ingest — a decision under the standing hold. It is a **precondition** for the two below, not a peer, and now also for all of Phase B, which is written but unbuilt. |
| **Real basic-memory coverage** | T3.3. Writing it against the current deployment tests an artefact nobody can rebuild. |
| **Multilingual search** | OD-1. The live container runs `bge-small-en-v1.5-onnx-q`. Fixing the sentence did not fix the product. |
| **Source-health lifecycle** | Tier 5. Quarantine, aggregation, operator visibility, parser sandboxing — all unbuilt. |
| **Corpus breadth** | Five pages from one document demonstrates the mechanism, not usefulness. |

---

### Risks & mitigations

- **R1 — A verification step could itself cause the data loss the work exists to
  prevent.** This happened in revision 1 and is the reason for A3: every step is
  static. Restated because it is the failure mode most likely to recur.
- **R2 — Pinning trades drift for staleness.** Nothing here keeps a pin fresh.
  Mitigation: the `# vN` comment keeps each greppable, step 3's and step 7's
  tests catch new unpinned entries, step 9 documents the bump path — and the
  trade is stated rather than presented as a pure win. The apt pin in step 8 is
  the sharpest instance and is called out there.
- **R3 — Hash-locking can break a build on another platform.** The locks are
  compiled with explicit `--python-version`/`--python-platform` from what the
  running images installed, so they describe a known-good target; a different
  platform needing different wheels is a real limit, recorded not hidden.
- **R4 — Phase B is unbuilt.** A4. Three Dockerfiles change and none is built in
  this plan, so a typo in a digest or a bad hash surfaces at the release build,
  not here. Mitigated by static tests over every build input and by making the
  build an explicit acceptance criterion rather than an assumption. **This is the
  weakest part of the plan and is not disguised.**
- **R5 — Step 14 cannot be verified on its own schedule.** A weekly cron is not a
  test. Mitigated by adding `workflow_dispatch` (absent today) and observing one
  manual run.
- **R6 — The socket stays.** Deliberate: `act_runner` requires it, and the
  alternatives are runner-host changes. Step 17 records it with the alternative
  costed.
- **R7 — The audit table is immutable to the writer, not to a DBA.** `postgres`
  bypasses grants and RLS. Stated in the migration; a tamper-evident chain would
  be the next increment and is not proposed here.

### Rollback plan

- **Phase A** — pins and one step replacement. `git revert` restores the floating
  tags and the `curl | sh`. `docker-compose.yml` changes take effect only on the
  next `up`, which this plan does not perform.
- **Phase B** — Dockerfile, lock and apt pins take effect only on the next build,
  which this plan does not perform. Reverting restores mutable inputs.
- **Phase C** — step 10 is a new test and fixtures. Step 11 adds a table
  (additive; an absent table means no audit rows, not a failure) — note its
  grants are part of the migration, so a partial apply is the one case to check.
  Step 12 adds a refusal: reverting loosens rather than breaks.
- **Phases D–F** — documentation, workflow triggers and permissions, the
  installer's ref knob, and comments.

No step modifies `raw/`, the vault, the corpus, or any running container.

### Decisions needed

1. **The Docker socket.** *Recommended: keep, record, revisit with the runner
   host.* `act_runner` needs it; the alternatives are runner-host changes; this
   plan removes the chain that made it reachable. If the runner ever executes a
   workflow from an untrusted branch, that changes.
2. **Token splitting (step 14).** *Recommended: attempt it, accept a single token
   if Gitea cannot scope one narrowly enough* — recorded either way. Both jobs
   push, so the split is narrower than revision 2 implied. The step 13 inventory
   is worth doing regardless of the outcome.
3. **Debian snapshot pinning (step 8).** *Recommended: defer to the release
   build, where it can be built and tested.* Adopting an untested archive URL now
   would be the same mistake Phase B was corrected for.
4. **Deployment coherence.** *Recommended: commit, rebuild with a clean revision
   stamp, re-ingest through the container.* Blocked on the standing hold — and it
   now also gates the validation of Phase B. Until then the deployed system
   should not be described as running this code.
5. **Release tagging (step 15).** *Recommended: tag one.* The repository has 0
   tags, which is why the installer cannot be honestly pinned.
6. **OD-1 and T4.1.** Unchanged, unresolved, and not this plan's to take.
