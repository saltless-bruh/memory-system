# Finish — Tier 0: restore the running stack

**Date:** 2026-08-24 · **Branch:** `fix/architecture-security-hardening` · **Base:** `2d2b9dd`
**Plan:** `artifacts/superpowers/plan-tier0-2026-08-24.md` (amendments AM-1…AM-5)
**Execution log:** `artifacts/superpowers/execution.md`

## Verification commands and results

| Command | Result |
|---|---|
| `uv run pytest -q` | **790 passed**, 21 errors (all opt-in live-integration, unchanged — T6.3) |
| `uv run ruff check .` | All checks passed |
| `uv run mypy scout scripts` | Success: no issues in 62 source files |
| `uv run snpmemory verify-secrets` | exit 0 — no prohibited values |
| `uv run python scripts/preflight_stack.py` | 2× PASS, exit 0 |
| `docker compose ps` | 7/7 services `healthy`, every `RestartCount` = 0 |
| corpus | 1 document, 127 chunks |

**The check that mattered.** `litellm` stopped, `sync-job` restarted into the
outage — the exact condition that produced 238 restarts:

```
t+20s  health=starting   restarts=0 state=running
t+100s health=unhealthy  restarts=0 state=running
[sync-job] cold-start sync failed: error:EmbeddingError; retrying in 5s (attempt 1, readiness cleared)
[sync-job] cold-start sync failed: error:EmbeddingError; retrying in 10s (attempt 2, readiness cleared)
```

`litellm` restored → `RECOVERED health=healthy restarts=0`. The container never
restarted across the whole outage-and-recovery cycle.

## What changed

**Diagnosis and infrastructure**
- `docker-compose.dns.yml` (new) — opt-in `SNP_DNS_SERVERS` override for hosts
  that cannot supply routable nameservers. Default path untouched (verified:
  `docker compose config` renders zero `dns:` keys).
- `scripts/preflight_stack.py` + `tests/test_preflight_stack.py` (new, 12 tests) —
  names both faults in one command, on the project's 0/1/2 exit contract.
- `scout/Dockerfile`, `docker-compose.yml` — `SNP_GIT_REVISION` build arg stamped
  into `org.opencontainers.image.revision`.
- `docs/runbook.md` — three incident rows, new §6.1 (DNS) and §6.2 (image drift),
  and `--build` + revision stamp added to the documented bring-up.

**Crash-loop containment**
- `scout/sync_job.py` — `SyncFailure` carries `retryable`; `watch()` propagates it;
  `_is_transient` classifies `httpx.HTTPStatusError` by status; `_async_main`
  retries in-process with capped backoff (5s → 300s) on both the cold start and
  the watch loop, holding readiness cleared. Permanent failures still exit 1.
- `tests/test_sync_job.py` — 6 new tests, written red first.

**Health-check scoping**
- `config/litellm/config.yaml` — `snp-judge` excluded from the background health
  loop.
- `docker-compose.yml` — gate on `snp-embed` + `snp-llm` only, via cached
  `?model=` probes; `start_interval` on both services.

**Documentation**
- `docs/ARCHITECTURE_STATUS.md` — two new prohibited claims about figure/table
  extraction.
- `docs/REMAINING_TASKS.md` — Tier 0 rewritten as was/is-now; new T5.1 and T6.5.

## Review pass

### Blocker
None.

### Major
1. **Four days of live results are void.** `scout` — the only door into RAG — ran a
   pre-`268af30` image from 2026-08-20 to 2026-08-24, i.e. before request-scoped
   auth and document ACLs. Anything "verified live" in that window was verified
   against code the repository does not contain. The rebuild fixes it going
   forward; **the affected verifications should be re-run before any of them is
   cited again.** Recorded in `REMAINING_TASKS.md` T0.2.
2. **Nothing enforces the revision stamp.** A plain `docker compose build` leaves
   the label `unknown`. That fails *safe* — the preflight reports unverifiable,
   never healthy — but no CI step runs the preflight, so drift is still caught
   only by someone choosing to look. Wiring it into `snpmemory status` / `check`
   belongs with the Tier 1 CLI work.

### Minor
3. **Backoff never resets within a process lifetime.** Deliberate (resetting on a
   successful cycle is exactly how Docker's own backoff failed here), but the
   cost is real: recovery from a long outage waits at the ceiling. Observed ~40s
   in the test; the ceiling is 300s.
4. **The DNS override covers `litellm` only.** Correct today — `scout` and
   `sync-job` reach providers only through the gateway — but it is an assumption
   that would break quietly if either gained a direct outbound call.
5. **The required-routes list lives inside an inline compose script.** Adding a
   load-bearing route means editing YAML-embedded Python.

### Nit
6. `scripts/preflight_stack.py` is a script, not a CLI command. Intentional —
   `snpmemory status` does not exist yet (Tier 1) — but it means the check is not
   discoverable from `snpmemory --help`.

## Follow-ups

- Re-run the live verifications invalidated by Major 1.
- `REMAINING_TASKS.md` **T5.1** — the ingester reports `figures_status: "ok"` for a
  capability the image does not have (Pillow absent). SH-1/SH-2 with a live repro.
- `REMAINING_TASKS.md` **T6.5** — a failing cold start re-parses the corpus on every
  inner retry.
- **T1.1** (`snpmemory --help` exits 2) was *not* in this run's scope and is still open.

## Manual validation

```bash
# 1. Both preflight checks
uv run python scripts/preflight_stack.py            # expect 2x PASS, exit 0

# 2. Crash-loop containment (the regression this run exists to prevent)
docker compose stop litellm
docker compose restart sync-job
sleep 100
docker inspect --format '{{.State.Health.Status}} restarts={{.RestartCount}}' snp-memory-sync-job-1
#   expect: unhealthy restarts=0     (before this work: starting, restarts climbing)
docker compose start litellm
#   sync-job returns to healthy on its own, still restarts=0

# 3. An optional route must not gate ingestion
#    point LITELLM_VLM_MODEL at a nonexistent model, recreate litellm:
#    expect litellm healthy and sync-job running
```
