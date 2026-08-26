# Finish — Tier 2: the MCP follow-ups

**Date:** 2026-08-25 · **Branch:** `fix/architecture-security-hardening`
**Plan:** `artifacts/superpowers/plan-tier2-2026-08-25.md` (= `plan.md`)
**Execution log:** `artifacts/superpowers/execution.md`

---

## Verification

| Command | Result |
| --- | --- |
| `uv run pytest -q` | **969 passed, 21 errors** — the 21 are the opt-in live-integration tests (T6.3), unchanged in number and identity |
| `uv run ruff check .` | All checks passed |
| `uv run mypy scout scripts` | Success: no issues found in 68 source files |
| `uv run snpmemory verify-secrets` | `Secret scan passed: no prohibited values found.` exit 0 |
| `uv run pytest -k mcp_policy` | 7 passed — no undecided commands |
| `snpmemory schema -o json` vs `tests/fixtures/clispec-v0.3.json` | **VALID** against clispec 0.3, 26 commands |
| `docker compose ps` | all 7 services `running` / `healthy` |
| restart counts | every container `restarts=0` (Tier 0 guard holds) |
| `scripts/preflight_stack.py` | `PASS container-dns` · `PASS image-revision: image built from 2d2b9dd` · exit 0 |

Test count went 870 → **969** (+99) across Tier 1's carry-over and Tier 2.

### Live checks, not just tests

* **The exported config actually starts the server it advertises.** The exact
  argv from `snpmemory mcp-config --client claude`, run from `/tmp`, lists all
  four tools and exits 0. The same command **without** `--root`, from the same
  directory, exits 3 "this command needs a repository checkout" — which is what
  every client that launches from its own directory would have got.
* **The installer scaffolds three servers.** Ran `scripts/install-agent.sh` into
  a scratch directory: exit 0, valid JSON, `snp-wiki` + `scout` + `snpmemory`
  with `--root` pinned.
* **A handle means one thing.** The same batch reported from the repository root
  and from `docs/` via a cwd-relative path returns one identical absolute handle
  and one identical state. `../../etc/plan.json` and `/etc/plan.json` both exit 3
  naming the root *and* what the path resolved to.
* **Cancellation, end to end, against a real detached run.** A real
  `scripts.compile_plan` child with only the model calls stubbed — the loop, the
  markers, the boundary check and the CLI all real. Cancelled while article 2 was
  in flight: article 2 **finished and stayed staged**, article 3 never started,
  state `cancelled: 2/3`, exit 1, staging holding two pages, `.run.json`, and
  nothing half-written. Re-running printed `resume: reusing staged …` for both
  and compiled only the third — so the promise in the cancellation message is
  true.
* **A1 measured against a real `fastmcp.Client`** — see below.

---

## What changed

### Phase A — the two commands Tier 1 left half-built

* **`ingest` tests** (`tests/test_cli_ingest.py`, 17). Boundary written first and
  proved to have teeth: swapping `candidate.resolve()` for `candidate.absolute()`
  turned the symlink and `..` cases red, then green again on revert.
* **`snpmemory mcp-config`** — `--client` required and closed; prints by default
  (the config *is* the summary, so `> .mcp.json` yields a valid file); `--out`
  merges rather than replaces and needs `--confirm` over an existing file.

### Phase B — T2.1, the local server is discoverable

Three servers now in the exporter, both manifests, the installer's scaffold, and
`docs/CONNECT_AGENTS.md`. The local entry is
`{"command": "snpmemory", "args": ["mcp", "--root", "<checkout>"]}`.

### Phase C — T2.2, handles mean one thing

`resolve_plan_path` + absolute handles + `snpmemory mcp --root`.

### Phase D — T2.3 / T2.4, honest states

Plan fingerprinting with a per-slug drift description; `FAILED` / `CANCELLED`;
heartbeat, TTL and poll interval; `snpmemory compile-cancel`.

### Phase E — documentation

`docs/REMAINING_TASKS.md` Tiers 1 and 2 rewritten as was/is-now, with a new
**T2.5** recording the Tasks measurement, a new **T3.0**, and a re-sequenced
backlog. `docs/CLI_SPEC.md` gained `compile-cancel`, `--root`, and `mcp-config`'s
real flags.

---

## The one step that did not land, and why

**Step 9 — make `compile_plan` task-capable — stopped, exactly as the plan
instructed it to.** Assumption A1 ("adopting Tasks is a `TaskConfig` on the
decorator, not a rewrite") is **false** at the pinned versions. Measured against
a real `fastmcp.Client`:

| Probe | Result |
| --- | --- |
| `get_task_capabilities()` | `None` |
| server capabilities over a real client | no `tasks` key at all |
| `call_tool(..., task=True)` | `McpError: does not support task-augmented execution` |
| `@tool(task=TaskConfig(...))` | `ImportError: requires the 'tasks' extra` — **at registration**, so the server would not build |

`fastmcp==3.3.1` gates every task path on **pydocket**, a distributed task system
requiring **`redis>=5`** and 13 other packages; task functions must also be
`async`, and these are synchronous wrappers around a blocking command path.

The trade, stated plainly: that buys a `taskId` scoped to the server process,
while what already exists is a handle that is a path on disk backed by a staging
directory and a `.run.json` carrying state, heartbeat, TTL and poll interval —
surviving a server restart, a reboot, and a client that has never heard of Tasks.
**Steps 7 and 8 delivered all three unmet normative Tasks rules** (terminal
`failed`/`cancelled`, TTL + poll interval, cooperative cancellation) with none of
that cost.

What was deliberately **not** done: declaring `TaskConfig` behind an
`if is_docket_available()` guard. It would compile and it would be a code path
this repository can never execute or test — a surface that exists only
cosmetically.

---

## Review pass

### Blocker
None.

### Major
None outstanding. Three were found during execution and fixed before the step
closed:

1. **`compile-status` returned exit 0 for a `failed` batch.** Its unfinished set
   was still `{stalled, not_started}`, so a caller chaining
   `compile-status && publish` would have proceeded on a dead run. Now exit 1 for
   `failed` and `cancelled`, with a regression test.
2. **`ingest` resolved relative paths against the process cwd** while its corpus
   root was anchored to the checkout, so it refused its own corpus from every
   directory but the repository root. Fail-safe but wrong (AM-1).
3. **A pre-flight refusal in a detached batch reported `not_started`** — the most
   misleading state available, because it says nothing happened when something
   did. The run marker is now written *before* the pre-flight (AM-6).

### Minor

1. **`mcp_config` imports `_load_existing` and `_write_exports`** from
   `scripts/export_mcp_config.py` — private-by-convention names crossing a module
   boundary. Deliberate: duplicating atomic-write-with-rollback would be worse
   than reaching for it, and the alternative (promoting them) is churn in a file
   this tier already touched. Worth promoting when that script is next revised.
2. **`snpmemory mcp --root` calls `os.chdir`.** A global process mutation, chosen
   because `invoke()` resolves configuration and `.env` from `Path.cwd()` on
   every tool call — no amount of threading a root through call sites would pin
   those. It happens once at startup, before any tool runs, in a process the
   server owns entirely. Tests contain it with `monkeypatch.chdir`.
3. **`status_for(root=None)` still resolves against the cwd.** Every caller in
   the repository passes a root or an already-resolved path, so this is only
   reachable by a future caller that forgets. Left as-is rather than making
   `root` required, because `_start_background` legitimately has an absolute
   path already.
4. **The heartbeat is one write per article.** For a 100-article plan that is 100
   small writes into the staging directory. Cheap next to two generations and a
   judge per article, but it is not free.

### Nit

1. `describe_plan_drift` says "the plan's source path changed" when the article
   sets match but the overall fingerprint differs. Accurate for the only case
   that can produce it today, but it is an inference from absence rather than a
   direct observation.
2. `TaskStatus` now carries eleven fields. It is still one dataclass derived
   entirely from disk, but it is at the size where a nested `progress` /
   `schedule` split would read better.
3. `.agent/manifest.json` and `packages/snp-agent/manifest.json` are kept
   byte-identical by a parity test rather than by generation. That is the
   existing arrangement; noted because this tier edited both by hand.

---

## Follow-ups (recorded in `docs/REMAINING_TASKS.md`)

* **T2.5** — MCP Tasks, priced. Revisit if the stack gains Redis for another
  reason, or if fastmcp ships a task backend with no external dependency.
* **T3.0** (new) — five distributed package files still tell agents to call
  `basic-memory.search_notes(...)`, a namespace no agent configured by our own
  exporter has. Belongs with the Tier 3 sync.
* **Re-verification debt** (from T0.2) — `scout` ran pre-`268af30` code from
  2026-08-20 to 08-24. Any "verified live" claim from that window was made
  against code the repository does not contain. The ACL boundary check was
  re-run afterwards and holds; anything else from that window should be re-run
  before it is quoted.

## Still on hold

15 commits remain local and unpushed at the owner's instruction, and the five
untracked `wiki/concepts/` pages are kept but not pushed. Nothing in this tier
changes that.
