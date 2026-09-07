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

THE HARD RULE -- THE LEAD'S VAULT NEVER GOES TO GITHUB.

  wiki/ pages on origin/main (GitHub, PUBLIC) :   8   generic sample stubs
  wiki/ pages on feat                          :   8   the same stubs
  wiki/ pages on gitea/main (LOCAL)            : 432   the lead's real vault

github.com/saltless-bruh/memory-system is public. The 432 pages on gitea/main
are the lead's personal knowledge base and are NOT to be published. The 8 pages
on feat and on GitHub today are generic deep-learning stubs used as an offline
test fixture; those are fine and stay.

So this is not one consolidated history on both remotes. It is two targets with
different content, and the difference is deliberate:

  GitHub main   = the system.  wiki/ stays at the 8 sample pages.
  Gitea  main   = the system + the lead's 432-page vault, which the running
                  replica serves.

CONCRETE CONSEQUENCE: do NOT merge gitea/main's wiki/ into feat. If you merge
gitea/main into feat for the code, you must keep feat's wiki/ at 8 pages --
`git checkout --ours wiki/` or equivalent -- and you must verify the count
before any push to GitHub. Publishing the vault is not a mistake that can be
undone by a later commit; it is public the moment it lands.

MANDATORY CHECK, run before every push to origin and paste the output in your
report:

    git ls-tree -r --name-only <ref-you-are-pushing> wiki | grep -c '\.md$'

If that is not exactly 8, do not push to GitHub. Stop and report.

WHAT TO PRODUCE:

Two targets, deliberately different:
  - GitHub main: the system, wiki/ at 8 sample pages.
  - Gitea  main: the same system code, with the lead's 432-page vault intact.

Route it through a pull request, not a direct push -- R-6.4 and R-7.3. The one
standing exception in this project is the W-2 acceptance gate pushing a
sentinel to the vault repo's main, and that is not you.

Order that keeps each step reviewable:
  1. DO THIS FIRST AND THEN STOP. Push feat/v3-retrieval-inversion to both
     remotes exactly as it is -- it already carries 8 wiki pages, so it is safe
     for GitHub without any editing. This gets 48 commits off a single disk and
     touches no shared branch. Report, and wait for confirmation before step 2.
  2. GitHub: open the PR from feat into main. Code only; wiki/ stays at 8.
  3. Gitea: bring the same code onto gitea/main WITHOUT disturbing wiki/. The
     432 pages there are what the running replica serves, and Agy is still
     adding to them, so confirm with the owner that Agy is paused before you
     start this step.
  4. Report on fix/architecture-security-hardening -- 16 commits ahead, on both
     remotes, and possibly superseded. Say which; do not assume, and do not
     merge it as part of this.

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
  - One PR on GitHub, reviewable, code only.
  - GitHub main carries exactly 8 wiki pages. Paste the count.
  - Gitea main carries 432 (or more, if Agy landed a batch). Paste the count.
  - After merge: the offline suite passes, ruff/format/mypy are clean.
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
