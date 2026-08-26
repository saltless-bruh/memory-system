# Finish — Tier 3: the distributed agent package

**Date:** 2026-08-25 · **Branch:** `fix/architecture-security-hardening`
**Plan:** `artifacts/superpowers/plan-tier3-2026-08-25.md` (= `plan.md`)
**Execution log:** `artifacts/superpowers/execution.md`

Decisions taken as recommended: superpowers is **repo-local** and declared;
**Agent Plugins 1.0.0 adopted**; `SNP_MEMORY_ROOT` ships empty with a clear
refusal.

---

## Verification

| Command | Result |
| --- | --- |
| `uv run pytest -q` | **1309 passed, 21 errors** — the 21 are the opt-in live-integration tests (T6.3), unchanged in number and identity |
| `uv run ruff check .` | All checks passed |
| `uv run mypy scout scripts` | Success: no issues found in 69 source files |
| `uv run snpmemory verify-secrets` | `Secret scan passed` exit 0 |
| `uv run pytest -k mcp_policy` | 7 passed — no undecided commands |
| `snpmemory schema -o json` vs `clispec-v0.3.json` | **VALID**, 27 commands |
| `plugin.json` / `mcp.json` vs vendored Agent Plugins 1.0.0 schemas | **VALID** |
| `docker compose ps` | all 7 services healthy |

Test count went 1180 → **1309** across the tier (969 at the start of Tier 3's
first step, before the 211-case spec suite landed).

### Live checks

* **The installed project works end to end.** `snpmemory install-agent` into a
  scratch directory → 8 skills, an `.mcp.json` with all three servers; then the
  **exact argv from that project's own `.mcp.json`**, run from that project,
  listed all four tools and exited 0.
* **The refusal holds.** Re-installing without `--confirm` exited **5** and left
  the project untouched; `--dry-run` created **0 files**.
* **The repo-local decision holds where it matters.** No `superpowers-*` skill
  was installed into the consumer project, and none appears in the distribution
  tarball.
* **The built bundle is a valid plugin.** `plugin.json` and `mcp.json` extracted
  *from the tarball* both validate against the vendored schemas.
* **`SNP_MEMORY_ROOT` behaves in all three states**, tested from `/tmp`: set →
  four tools, exit 0; empty (as shipped) → exit 3 naming the variable; unset
  outside a checkout → exit 3.

---

## What changed

**Phase A — fix what was wrong.** 42 `SKILL.md` frontmatters rewritten as
double-quoted scalars; a 211-case spec suite; both Agent Plugins schemas
vendored; the tool namespace renamed across 30 files; the fifth config surface
collapsed into the one generator.

**Phase B/C — the package is a decision, and a standard shape.**
`manifest.json` → `plugin.json` + `mcp.json`, validated; `ships` and `repoLocal`
declared and enforced in both directions.

**Phase D — `snpmemory install-agent`.**

**Phase E — `docs/REMAINING_TASKS.md` Tier 3 rewritten as was/is-now**, with two
new items (T3.3, T3.4, T3.5) recording what the tier actually found.

---

## Three findings the backlog did not have

1. **T3.1 was three problems, not one.** The six broken frontmatters live in
   `.agent` **and** `.claude` (12 files, not 6), and the `>-` folded scalar that
   "fixed" the other eight skills is itself flagged by the specification —
   angle brackets in frontmatter are a prompt-injection risk. 26 files carried
   them.
2. **There were five config surfaces, not three.**
   `scripts/export_agent_bundle.py` authenticated with `SCOUT_AUTH_TOKEN` while
   the documentation and every other emitter used `SCOUT_AUTH_HEADER`. A user
   following the docs got a config from that path that could not authenticate.
3. **The 25-file gap was structurally invisible.** Parity was enforced
   `.agent` ↔ `.claude` for four subtrees and `packages` ↔ `.agent` for root
   files only — the package could lose any number of components silently. That
   is why the fix had to be a declaration a test reads, not a file copy.

---

## Review pass

### Blocker
None.

### Major
None outstanding. Two were found and fixed during execution:

1. **`export_agent_bundle.py` replaced the target config outright** rather than
   merging. Overwriting somebody's `.mcp.json` is data loss. Not named in the
   plan; fixed and pinned by a test.
2. **The `<path to the memory-system checkout>` placeholder** I introduced in
   Tier 2 could never work, and was an angle bracket besides. Replaced by
   `SNP_MEMORY_ROOT` with a refusal that names it.

### Minor

1. **`.agent/` and `packages/snp-agent/` are still maintained by hand-mirroring**,
   with `export_agent_bundle.py --sync` as the tool and a parity test as the
   guard. Generation from one source would be better; three trees kept in step
   by a test is the arrangement this tier inherited, not one it chose.
2. **Agent Plugins 1.0.0 is 19 days old.** R1's mitigations all hold —
   `install-agent.sh` is unaffected, the schemas are vendored and pinned, the
   format is a superset — but this is a bet, and it should be re-examined if the
   spec revises before clients implement it.
3. **`plugin.json`'s `ships` list duplicates the filesystem.** That duplication
   is the point (F-4: the decision must live in a file a test reads), but it is
   still two places to update when a skill is added. The test fails loudly on
   drift, which is the intended trade.
4. **`install-agent` shells out and captures 120 s of output.** A hung script
   times out cleanly, but the wrapper cannot report partial progress — the user
   sees nothing until it finishes.
5. **`.claude/RESUME.md` exists outside the four mirrored subtrees** and is
   unguarded by any parity test. Harmless today; noted because it is the kind of
   file that later turns out to matter.

### Nit

1. `test_agent_skills_spec.py` parses each frontmatter once per rule — five
   parses per file. Negligible at 42 files, wasteful in principle.
2. The `repoLocal.prefixes` check matches any path *part* starting with
   `superpowers-`, so a file merely named that way inside a shipped skill would
   trip it. Correct today, slightly broader than intended.
3. `_WRITES` in `agent.py` hardcodes the four directories the shell script
   creates. If the script grows a fifth, `--dry-run` under-reports until someone
   notices.

---

## Follow-ups (recorded in `docs/REMAINING_TASKS.md`)

Tier 3 leaves nothing open. The backlog now starts at **Tier 4**:

* **T4.1** — compile the remaining 10 articles (no new code; costs model calls).
* **T5.1** — stop reporting `figures_status: "ok"` for a capability the image
  does not have.
* **T4.3** — the `derived/` asset store, the last thing blocking `extract`, the
  only unimplemented command (27 of 28 specified now exist).
* **T4.2**, then Tier 5 and the Tier 6 housekeeping.

## Still on hold

15 commits remain local and unpushed at the owner's instruction, and the five
untracked `wiki/concepts/` pages are kept but not pushed. Nothing in this tier
changes that.
