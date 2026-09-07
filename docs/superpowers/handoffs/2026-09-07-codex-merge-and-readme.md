# Prompt for Codex — consolidate three histories, then rewrite the README

```text
Two tasks. The first is a merge with one real hazard in it; read the topology
before you touch anything. The second is a README rewrite.

=========================================================================
TASK 1 — CONSOLIDATE
=========================================================================

THE TOPOLOGY, MEASURED (do not take this on trust; re-derive it):

  merge base of everything          26abe20
  origin  = GitHub   https://github.com/saltless-bruh/memory-system.git
  gitea   = local    http://127.0.0.1:3000/snp-admin/snp-memory.git

  local main  == origin/main  == 26abe20     the old pre-feature history
  feat/v3-retrieval-inversion                48 commits ahead of gitea/main
                                             LOCAL ONLY -- never pushed anywhere
  gitea/main                                 27 commits feat does not have
  fix/architecture-security-hardening        local 16 ahead; also on GitHub

So there are three divergent lines, all sharing 26abe20:
  - the engine work        (feat, local only)
  - the vault + Agy work   (gitea/main)
  - the old baseline       (local main == origin/main)

THE HAZARD -- READ THIS TWICE:

  wiki/ pages on feat        :   8
  wiki/ pages on gitea/main  : 432
  wiki/ pages on origin/main :   8

This is not drift. It is deliberate and documented in
docs/superpowers/plans/2026-09-05-three-lane-coordination.md §2: the feature
branch carries an 8-page sample, and gitea/main carries the lead's real vault.
The replica that the running system serves is built from gitea/main.

A naive `git merge gitea/main` into feat pulls 432 vault pages into the code
history. A naive merge the other way can delete the lead's vault. Neither is
acceptable. Decide explicitly what wiki/ should contain on the consolidated
main and say so in the PR description before you merge. If the answer is not
obvious to you, stop and ask -- this is the one place where a wrong call
destroys content that took weeks to author.

WHAT TO PRODUCE:

One consolidated `main` on BOTH remotes, containing the engine work, the vault
work, and a resolved answer to the wiki/ question. Route it through a pull
request, not a direct push -- R-6.4 and R-7.3. The one standing exception in
this project is the W-2 acceptance gate pushing a sentinel to the vault repo's
main, and that is not you.

Order that keeps each step reviewable:
  1. Push feat/v3-retrieval-inversion to BOTH remotes as-is, so 48 commits of
     work stop existing on one disk only. Do this first, before any merging.
  2. Open the PR. Resolve wiki/ deliberately.
  3. Decide what to do with fix/architecture-security-hardening -- it is 16
     commits ahead and may be stale or may be superseded. Report which; do not
     assume.

EXPECT CONFLICTS in at least these, because both lines moved:
  scout/vault.py  scout/wiki_ingest.py  scout/ingest.py  scout/sync_job.py
  scout/cli/**    artifacts/v3/**       docker-compose.yml
Take the feat side for engine code unless the gitea/main side is demonstrably
newer, and verify by reading the code rather than by commit date.

CI IS BEING DISABLED, so do not wait on it and do not treat its absence as a
problem. Note also that main currently has 2 real ruff errors that the feature
branch already fixes -- scripts/host_sync.py (unsorted imports) and
tests/test_host_sync.py:546 (nested with). They resolve themselves when feat
lands.

DONE MEANS:
  - feat/v3-retrieval-inversion exists on both remotes.
  - One PR, reviewable, with the wiki/ decision stated in its description.
  - After merge: the offline suite passes, ruff/format/mypy are clean, and
    `find wiki -name '*.md' | wc -l` on the merged main is the number you said
    it would be.
  - The running stack still serves: `snpmemory verify-secrets` exits 0 and the
    replica still has 433 files.

=========================================================================
TASK 2 — REWRITE README.md
=========================================================================

The current README is 256 lines and predates all of this. Rewrite it to
describe the system that exists today. Four things it must contain:

1. A DIAGRAM of the actual architecture. Not a wish. The real path is:

     Obsidian vault -> git push -> Gitea -> webhook -> host-sync
       -> vault-replica (immutable snapshots + a `current` symlink)
       -> sync-job (two independent watchers: raw/ and the vault)
       -> PostgreSQL 16 + pgvector
       -> scout (MCP over HTTP, static-token auth)
       -> agent: wiki_search finds the page, wiki_read returns it from disk

   The inversion is the point and should be stated as one line: the index
   finds, the vault answers. Measured: a push reaches the served index in
   about 7 seconds.

2. THE WORKFLOWS, as they actually run:
   - retrieval: wiki_search -> wiki_read -> cite path + heading
   - vault edit: edit, push to main, next answer reflects it, no command run
   - raw ingest: drop a file in raw/, sync-job indexes it on a watch
   - compile: the deliberately manual Nhịp B path

3. THE TECH, with versions from the lock files rather than memory:
   Python 3.12, PostgreSQL 16 + pgvector, FastMCP, cyclopts, asyncpg,
   watchfiles, LiteLLM gateway, Gitea, Docker Compose. State that embeddings
   are 1024-dimensional and that the index is a disposable derivative of the
   vault.

4. TWO SEPARATE LISTS, clearly marked, in their own sections:

   "Not finished" -- things that exist in name but not in working form.
   Read docs/proposal/V3_disposition_status_2026-09-05.md, especially its
   2026-09-07 update section, and take the PAPER and PARTIAL tiers from there.
   At time of writing that includes: source fetch + content-addressed cache,
   read_source, the index inspector, blue-green embedding migration, the
   similarity graph, and security.yaml's Gitleaks scan, which fails on a
   bind-mount bug and has never scanned a commit.

   "Removed" -- what was taken out and why, so nobody looks for it. That
   includes basic-memory (a second 384-dim vector space beside a 1024-dim
   index), the auto-healer, the `gate` and `heal` commands. `mint` and `fetch`
   are still present and were deliberately not removed; say so rather than
   implying the list is complete.

RULES FOR THE README:
  - Every number in it must be one you measured, and say when. Do not copy
    figures out of the old README or out of the blueprint; several are stale.
    Retrieval is currently recall@1 0.82, recall@5 0.97 over 40 questions.
  - Do not describe anything as working that you have not run. If you are
    unsure whether a feature works, put it in the "Not finished" list and say
    what is unverified.
  - Mermaid for the diagram is fine.

=========================================================================
VERIFY BEFORE YOU REPORT
=========================================================================

  rm -rf .agents .codex
  env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY \
    uv run pytest -m 'not integration' --disable-socket -q
  uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts

Run the suite whole. Report the command, its exit status, and the figure it
produced. If the merge blocks on something you cannot resolve safely -- above
all the wiki/ question -- stop with the evidence and say so. A blocker reported
honestly costs far less than a vault merged wrong.
```
