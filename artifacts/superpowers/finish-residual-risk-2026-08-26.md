# Finish — supply-chain hardening (plan revision 3)

**Date:** 2026-08-26 · **Branch:** `fix/architecture-security-hardening`
**Plan:** `artifacts/superpowers/plan-residual-risk-2026-08-26.md` (= `plan.md`)
**Execution log:** `artifacts/superpowers/execution.md`

**All 18 steps landed.** Two are complete in the repository and carry a
verification that could not be performed here; both are named below rather than
counted as done.

---

## Verification

| Command | Result |
| --- | --- |
| `uv run pytest -m 'not integration' --disable-socket -q` | **1417 passed**, 29 deselected |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 348 files already formatted |
| `uv run mypy scout scripts` | Success — 74 source files |
| `docker compose config` | exit 0; four third-party images digested |
| `SNP_CANONICAL_ENV=1 pytest tests/test_parser_golden.py` | 5 passed |
| postgres integration (RLS + append-only) | **13 passed** |
| `snpmemory verify-vault` | 7 pages · **0 errors** · 2 warnings |
| migration `005` | applied **and recorded**; `0 pending` |

**Not re-run:** `verify-addresses`, `verify-groundedness` — nothing in `wiki/`
changed and the judge route has a 50 requests/day ceiling.

---

## What is now pinned

| Class | Count | Pinned to |
| --- | --- | --- |
| Actions | 9 | commit SHA |
| Third-party images | 4 | `@sha256:` digest |
| Base images | 3 | `@sha256:` digest |
| Python packages | **246** | version **and** hash, `--require-hashes` |
| apt packages | 2 | exact version |

And one thing is **un**pinned: `curl -LsSf https://astral.sh/uv/install.sh | sh`,
removed from the workflow that pushes.

Each class is asserted by `tests/test_supply_chain_pins.py` (19 tests), and every
guard was falsified before being trusted:

| Injected regression | Caught by |
| --- | --- |
| floating action tag | `test_every_action_is_pinned_to_a_commit` |
| `curl … \| sh` in a step | `test_no_step_pipes_a_download_into_a_shell` |
| undigested compose image | `test_every_pulled_image_is_pinned_to_a_digest` |
| undigested `FROM` | `test_every_base_image_is_pinned_to_a_digest` |
| `pip install` without hashes | `test_every_pip_install_requires_hashes` |
| unversioned apt package | `test_every_apt_install_names_a_version` |
| lock entry without a hash | `test_the_locks_cover_every_hashed_install` |
| parser change, revision not bumped | `test_the_parse_matches_the_golden_for_this_environment` |
| unknown parsing environment in CI | same, under `SNP_CANONICAL_ENV=1` |
| the 003 `GRANT ... FOR ALL` pattern on the audit table | 4 integration tests |

---

## What execution found that the plan did not

**1. The plan's own inventory was wrong.** It said "3 third-party images". There
are four. The missing one was `gitea/act_runner:0.2.11` — the image the entire
exposure narrative is about. Three paragraphs about the runner, and its image
absent from the pin list.

**2. The exposure is latent, not live.** `gitea-runner` is behind
`profiles: [runner]` and **has never been started on this host** — no container,
no local image. The chain is fully assembled in the repository and fires on
`docker compose --profile runner up`. Still worth removing; not an active
compromise, and the plan implied it was. Corrected in the plan text and in OD-3.

**3. The ranges had already drifted across majors.** `pypdf>=4.0.0` was running
**6.16.1** — two majors above its floor, in the library that parses every
document in the corpus. `watchfiles>=0.21` was on **1.2.0**. Nobody chose these.

**4. `basic-memory==0.22.1` pinned 1 package out of 164.**

**5. The Python version does not change this parse.** The corpus document
digests identically under CPython 3.12 and 3.14 with the same extractor
versions. That measurement is why the golden key excludes `python` — and it
retroactively justifies `describe_fingerprint_difference` ignoring it, which
until now was a judgement call with nothing behind it.

**6. `REMAINING_TASKS.md` contradicted itself.** The judge-health paragraph
claimed an on-demand `GET /health` probes the route, then two sentences later
described the same endpoint filtering the *cached* result. Both halves shipped.

**7. The services table in the runbook was split in two** by a subsection
inserted between its header and its body.

---

## Two steps whose verification could not be performed here

Named as acceptance criteria, not counted as done:

* **Phase B is written and unbuilt.** Three Dockerfiles, three hash-locked
  requirement files and the apt pins change; **A4 forbids building** because A3
  forbids restarts. A wrong digest or a bad hash surfaces at the release build.
  Static tests cover every input; that is not the same as a build.
* **Step 14's manual `workflow_dispatch` run.** The trigger is added — it was
  absent, which is why the plan's own verification was unrunnable — but
  exercising it needs a runner, and `gitea-runner` has never been started here.

---

## Review pass

### Blocker
None.

### Major
None outstanding. Two were found and closed during execution:

1. **Migration 005 was applied outside the ledger.** `psql -f` left
   `schema_migrations` disagreeing with the database, so the next migration run
   would have re-applied it. Re-applied through `scripts/migrate_postgres.py`;
   grants re-verified afterwards to prove re-application did not widen them.
2. **My apt check mis-parsed its own pin.** The regex split
   `git=1:2.47.3-0+deb13u1` at the epoch colon and reported the version half as
   an unversioned package. Replaced with `shlex.split` over the `&&`-separated
   segment.

### Minor

1. **The audit log is immutable to the ingest identity, not to a DBA.**
   `postgres` bypasses both the grant and RLS. Stated in the migration. A
   tamper-evident hash chain would be the next increment; it is not proposed.
2. **The golden covers one document.** It is the entire corpus today, so the
   coverage is complete and the *statement* is not: a second document could
   exercise parser paths this one never reaches.
3. **No container golden exists.** It cannot be recorded honestly until the
   image is rebuilt — the running one predates `scout.references` and would
   encode the stale parser as expected output.
4. **Pinning trades drift for staleness.** Nothing here keeps a pin fresh, and
   the apt pin will eventually stop resolving as Debian drops superseded
   versions. Loud, not silent — recorded in the Dockerfile and the runbook.
5. **`BOT_TOKEN`'s granted scopes remain unknown.** OD-2 records what the
   workflow requires and names reading the actual scopes as the owner's act.

### Nit

1. `SNP_AGENT_REF` defaults to `main`, which is not a pin. It cannot be one
   until a release is tagged (T6.6).
2. The `--acknowledge-capability-change` comparison normalises whitespace and
   strips surrounding quotes, so it is not byte-exact. Deliberate: a caller who
   copies the text correctly and is rejected over a quote character learns the
   check is noise and reaches for the flag that skips it.
3. `record_capability_override` derives `source_uri` by splitting the mismatch
   string on `:`. It works because `corpus_fingerprint_mismatch` builds that
   string, but the two are coupled through a format rather than a type.

---

## A process note

Two measurement errors this session, both the same shape as one from the
previous session:

* A falsification of the pipe-to-shell guard **passed when it should have
  failed**, because the injection anchored on text that exists in a different
  workflow file — `str.replace` was a silent no-op and the test never ran. I
  read "7 passed" as evidence the guard was weak.
* An earlier check for `curl … | sh` grepped raw file text and flagged **my own
  comment** documenting the removed line.

Every falsification after that asserts its setup applied — the anchor exists,
and the injected change is visible in the parsed structure — before drawing any
conclusion from a test result.

Separately: probing the 3.12 parse with `uv run --python 3.12` **rebuilt `.venv`
from 3.14 to 3.12.14**. Gitignored, and arguably closer to the deployment
(containers run 3.12.13), but unintended, and every verification after that point
ran on 3.12.

---

## Follow-ups

* **The clean release build** — the acceptance criteria are plan step 18: the
  three images build from the pinned digests and locks; the stack comes up on
  them; `snapshot.debian.org` evaluated with a real build behind it.
* **A manual `workflow_dispatch` run** of `auto-healer.yaml`, once a runner exists.
* **Read `BOT_TOKEN`'s scopes** and decide on splitting (OD-2).
* **Tag a release** so the agent installer can be pinned (T6.6).
* **Record a container parser golden** after the rebuild.
* Unchanged and still the owner's: **T4.1**, **OD-1**, **T4.3**, **Tier 5**, **T3.3**.

## Still on hold

15 commits local and unpushed. The five untracked `wiki/concepts/` pages are
kept and not pushed. Nothing in `raw/`, `wiki/`, the corpus, or any running
container was modified by this pass.
