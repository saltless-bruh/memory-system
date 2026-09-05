# Lane: Engine + Infrastructure — Claude

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Read first:** `2026-09-05-three-lane-coordination.md`, then `2026-09-04-engine-completion-executor-brief.md`.

**Goal:** Close the two engine claims — the agent queries the index and gets back an address and a file (W-1), and a change in the vault reaches the index (W-2) — plus the infrastructure that has never run, plus the contract change that unblocks Agy.

**Architecture:** Tasks 2–7 are the existing engine plan, minus its Task 3 which moved to Codex. Tasks 1, 8, 9 are new: a heading-contract change that unblocks the vault lane, the CI runner that has never executed, and W-2's *first* hop, which nobody has looked at.

**Tech Stack:** Python ≥3.12, asyncpg, pgvector, watchfiles, FastMCP, pytest, ruff, mypy strict, Docker Compose, Gitea Actions.

**Spec:** `docs/superpowers/specs/2026-09-04-engine-completion-design.md`

## Global Constraints

- Offline suite baseline **1387 passed, 29 deselected**; static clean. Both stay green.
- Retrieval floors `recall@1 ≥ 0.60`, `recall@5 ≥ 0.85`, measured 0.80 / 1.00. **Floors never move.**
- Every gate asserting an absence must be shown failing against a planted positive.
- `rm -rf .agents .codex` before every suite run. Never weaken `test_agent_package_sync.py`.
- **Never push the feature branch to any remote.** Task 6's W-2 gate pushes a sentinel to `gitea/main` under the owner's recorded exception.
- Do not touch Codex's paths (`scout/cli/**`, `scout/mcp_server.py`, `scripts/verify_addresses.py`, `scripts/ci_address_gate.py`, `scout/healer.py`, `scripts/compile_*.py`, `docs/CLI_SPEC.md`) or Agy's (`wiki/**`).

## OWNS

```
scout/sync_job.py  scout/wiki_ingest.py  scout/ingest.py  scout/serve.py
scout/vault.py  scout/backends/**
docker-compose.yml  .gitea/workflows/checks.yaml  .gitea/workflows/security.yaml
artifacts/v3/**  .unlazy/**
tests/test_sync_job.py  tests/test_serve.py  tests/test_wiki_ingest.py
tests/test_backends.py  tests/test_vault.py
```

---

### Task 1: H2 — make the unauthorable headings optional ⚠️ DO THIS FIRST

**Agy is blocked until this lands.** Until then every page Agy fixes still fails the linter.

`REQUIRED_HEADINGS` demands four headings. Two of them cannot be honestly authored across the corpus:

| Heading | Present | Why it cannot be required |
|---|---|---|
| `TL;DR` | 12/432 | derivable — a summary of the page's own text |
| `Technical Specifications` | **0/432** | cannot be synthesised; a concept page has no specifications |
| `Provenance` | 91/432 | a **dated sourcing changelog**. Authoring one for the other 341 means inventing dates and sources. |
| `Cross-References` | 0/432 | derivable — 202 renameable headings, plus links already in bodies |

The code already contains this exact reasoning, for `Works Cited` at `vault.py:59`: *"making it mandatory would fail every page already in the vault and turn a new feature into a vault-wide lint error."* Apply it to the two that fabrication would be required to satisfy.

**Files:** Modify `scout/vault.py:50-62`. Test `tests/test_vault.py`.

**Interfaces produced:** `REQUIRED_HEADINGS = ("TL;DR", "Cross-References")`; `OPTIONAL_HEADINGS = {"Technical Specifications": "Cross-References", "Provenance": "Cross-References", "Works Cited": "Cross-References"}`. Agy's lane document quotes the resulting canonical page shape.

- [ ] **Step 1: Write the failing tests**

```python
def test_only_derivable_headings_are_required() -> None:
    """Provenance is dated sourcing; requiring it would require inventing it."""
    from scout.vault import REQUIRED_HEADINGS, OPTIONAL_HEADINGS

    assert REQUIRED_HEADINGS == ("TL;DR", "Cross-References")
    assert "Provenance" in OPTIONAL_HEADINGS
    assert "Technical Specifications" in OPTIONAL_HEADINGS


def test_a_page_with_every_optional_section_still_lints() -> None:
    """Three optionals anchored to one required heading must all be accepted."""
    from scout.vault import _headings_are_ordered

    assert _headings_are_ordered(
        (
            "TL;DR",
            "Technical Specifications",
            "Provenance",
            "Works Cited",
            "Cross-References",
        )
    )


def test_a_minimal_page_lints() -> None:
    from scout.vault import _headings_are_ordered

    assert _headings_are_ordered(("TL;DR", "Cross-References"))


def test_order_is_still_a_contract_not_a_suggestion() -> None:
    from scout.vault import _headings_are_ordered

    assert not _headings_are_ordered(("Cross-References", "TL;DR"))
    assert not _headings_are_ordered(("TL;DR", "Cross-References", "Provenance"))
```

- [ ] **Step 2: Run to verify they fail**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_vault.py -k "derivable or optional_section or minimal_page or still_a_contract" --disable-socket -q
```
Expected: FAIL on the first assertion.

- [ ] **Step 3: Change the contract**

```python
#: Headings every page **must** carry, in this order. Only headings a page can
#: honestly supply from its own material are required: `TL;DR` summarises the
#: page's own text, and `Cross-References` gathers links the body already holds.
#: `Technical Specifications` and `Provenance` are optional for the same reason
#: `Works Cited` is — a concept page has no specifications, and Provenance is a
#: dated sourcing changelog that cannot be written retroactively without
#: inventing the dates. Requiring either would make fabrication the only route
#: to a green lint.
REQUIRED_HEADINGS = (
    "TL;DR",
    "Cross-References",
)

#: Headings a page **may** carry, each at a fixed position immediately before
#: the required heading it is anchored to. Several may stack before the same
#: anchor; they are emitted in this dict's order, so this mapping defines the
#: canonical sequence between `TL;DR` and `Cross-References`.
OPTIONAL_HEADINGS: dict[str, str] = {
    "Technical Specifications": "Cross-References",
    "Provenance": "Cross-References",
    "Works Cited": "Cross-References",
}
```

Also correct `_headings_are_ordered`'s docstring — it says an optional appears *"only immediately before"* its anchor, but the implementation stacks all present optionals before it, which is what makes three-in-a-row legal.

- [ ] **Step 4: Run tests**

```bash
rm -rf .agents .codex
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_vault.py --disable-socket -q
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check scout/vault.py && uv run mypy scout scripts
```

Existing tests asserting the four-heading frame will fail. **Update them to the new contract — do not delete them.** They are checking that the frame is enforced, which still matters.

- [ ] **Step 5: Commit and tell Agy it is unblocked**

```bash
git add scout/vault.py tests/test_vault.py
git commit -m "fix(vault): require only the headings a page can honestly supply

Technical Specifications is on 0 of 432 pages and cannot be synthesised.
Provenance is a dated sourcing changelog on 91 of 432; authoring the other 341
means inventing dates and sources. Both become optional, for the same reason
Works Cited already is.

TL;DR and Cross-References stay required: both are derivable from what each
page already contains.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Tasks 2–7: the engine

Execute `docs/superpowers/plans/2026-09-04-engine-completion.md` **Tasks 1, 2, 4, 5, 6, 7, 8**, in that order. That document carries the full code, tests and commands.

**Its Task 3 (`ingest-wiki` CLI) is Codex's — skip it.** Nothing else in the engine plan depends on it: `WikiIndexer` calls `ingest_wiki()` directly.

Checkpoints from the executor brief still apply — stop and report at each:

| | After | Why |
|---|---|---|
| **CP-1** | engine Task 2 | two watchers in one process, before deployment |
| **CP-2** | engine Task 4 | `sync-job` starts for the first time; orphan destroyed |
| **CP-3** | engine Task 5 Step 5 | re-ingesting `raw/` changes the corpus compile reads |
| **CP-4** | engine Task 7 | pushes a sentinel to `gitea/main`; stops `sync-job` for the negative control |

---

### Task 8: Start the CI that has never run

`checks.yaml` and `security.yaml` are 🟩 keep components that have **executed zero times on any commit**, because `gitea-runner` is declared in compose and was never registered. Two written, tested workflows have never gated anything.

**Files:** Modify `docker-compose.yml` (the `gitea-runner` service).

- [ ] **Step 1: Record the starting state as evidence**

```bash
docker ps --format '{{.Names}}' | grep -c runner || echo "0 runners"
docker exec snp-memory-git-1 sh -c 'ls /data/gitea/actions_log 2>/dev/null | wc -l'
grep -n -A15 '^  gitea-runner:' docker-compose.yml
```
Expected: no runner, empty actions log.

- [ ] **Step 2: Register the runner**

Gitea Actions needs a registration token from the instance. Generate one, supply it to the service as `GITEA_RUNNER_REGISTRATION_TOKEN`, and confirm `GITEA_INSTANCE_URL` points at `http://git:3000` on the compose network — **not** `127.0.0.1`, which resolves to the runner container itself.

```bash
docker exec snp-memory-git-1 sh -c \
  'gitea --config /data/gitea/conf/app.ini actions generate-runner-token'
docker compose up -d gitea-runner
```

- [ ] **Step 3: Verify registration, then force a real run**

```bash
docker compose logs gitea-runner --tail 30 | grep -i "success\|registered\|declare"
git commit --allow-empty -m "ci: first execution of checks and security"
git push gitea HEAD:refs/heads/ci-probe        # a probe branch, NOT main, NOT the feature branch
```

- [ ] **Step 4: Confirm the workflows actually executed**

```bash
sleep 60
docker exec snp-memory-git-1 sh -c 'ls /data/gitea/actions_log 2>/dev/null | wc -l'
```
Expected: **greater than zero for the first time in this repository's history.**

Read the result. These workflows have never run, so a first-run failure is **information, not a regression** — report what failed rather than fixing it inside this task.

- [ ] **Step 5: Clean up the probe branch and commit**

```bash
git push gitea --delete ci-probe
git add docker-compose.yml
git commit -m "fix(ci): register the runner so checks and security actually execute

Both workflows were retained components that had never run on any commit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 9: W-2's first hop — the lead's vault is not a git checkout

**This is a finding, not yet a fix, and the fix is the owner's to choose.**

W-2 is *"the lead edits in Obsidian, pushes, and the next answer reflects it."* The engine plan closes `replica → index`. Nobody has examined `Obsidian → Gitea`:

```bash
ls -d ~/Documents/memo-project/"Obsidian Vault"          # 433 .md files
git -C ~/Documents/memo-project/"Obsidian Vault" status  # fatal: not a git repository
```

**He has nothing to push from.** The Gitea copy is a one-way snapshot pushed by hand. Every downstream hop can be perfect and W-2 still will not demonstrate.

- [ ] **Step 1: Confirm the gap and check for any other checkout**

```bash
find ~ -maxdepth 4 -name '.git' -type d 2>/dev/null | xargs -I{} dirname {} | grep -i vault
git -C ~/Documents/memo-project/snp-memory-system-main log --oneline -1 gitea/main
```

- [ ] **Step 2: Put the options to the owner — do not choose**

This changes how he works in Obsidian on his own machine. Present, with the exact commands for each:

- **A.** Make his vault a checkout — clone `snp-memory.git` with a sparse checkout of `wiki/`, and point Obsidian at `<clone>/wiki`. His 433 files currently sit at the vault *root*, while the repo holds them under `wiki/`, so the vault folder Obsidian opens changes. Truest to W-2 as written.
- **B.** A one-way sync script he runs — closer to today, but "no operator action" becomes false and W-2's own wording breaks.
- **C.** Demo the edit from a checkout that is not his daily vault. Honest if narrated as such; weaker.

- [ ] **Step 3: Execute the chosen option and prove it**

Whichever is chosen, the proof is the same: edit a page the way he would, push the way he would, and watch the answer change with no other command. That is engine Task 7's oracle pointed at his real workflow instead of a temporary clone.

---

## Standing duties across the lanes

**H3 — measure every Agy batch.** Before merge and after:

```bash
set -a && . ./.env && set +a
.venv/bin/python artifacts/v3/retrieval_quality.py
```

Report the number to Agy. Baseline 0.80 / 1.00. A drop means the batch is reverted, not debated. This matters most after a TL;DR batch — `## TL;DR` becomes chunk 0 of the indexed page, so a weak summary changes what that page retrieves on.

**H4 — verify every Codex removal.** After each lands:

```bash
rm -rf .agents .codex
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts
set -a && . ./.env && set +a && .venv/bin/python artifacts/v3/retrieval_quality.py
```

The suite total will drop by the number of tests deleted with their modules. That is correct. What must not change is recall, or the engine gates.

---

## Definition of done

1. `FIND READ CITE VERIFIED`, `VAULT CHANGE PROPAGATES`, `LIVE SQL VERIFIED` — all three through the gate runner with real `EVIDENCE:` lines, each observed failing against its control.
2. `docker ps` shows healthy `snp-memory-sync-job-1` and **no** `basic-memory` container.
3. `/data/gitea/actions_log` is non-empty — CI has executed at least once.
4. W-2's first hop has an owner decision recorded, and the chosen option demonstrated.
5. Offline suite green; ruff, format, mypy clean.
6. `retrieval_quality.py` at or above 0.80 / 1.00 after every merged batch from any lane.
7. Nothing added by this lane is declared and unwired.
