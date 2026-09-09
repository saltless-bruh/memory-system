# E11 runbook — connect Obsidian to private Gitea

**Status:** owner selected Option A with manual Git first on 2026-09-09;
setup and demonstration remain pending

**Primary path:** Option A, a sparse checkout whose `wiki/` directory Obsidian
opens as the vault

## Owner decision and access boundary

The owner chose **“A: Daily clone (Recommended)”** and then **“Manual Git first
(Recommended)”** through the interactive requests. These commands are for the
owner, who is authorized for all departments in the private repository.

A sparse checkout controls which paths appear locally; it is not an access
control boundary. A clone of this shared repository grants access to its Git
contents and history regardless of the department scope enforced by Scout's
retrieval RLS. Do not use this runbook to onboard a department-limited colleague.
The repository distribution boundary must be decided before a second person
receives a clone. This owner setup does not choose a per-department repository
design or implement a lock service.

Manual staging and commits are the initial publication boundary: a saved draft
is not automatically published. Plugin automation remains deferred; enabling it
later also requires accepting that unfinished edits may be staged and exposed
to readers. Concurrent human/agent editing coordination remains a separate
unimplemented concern.

## What this changes

The current daily vault is:

```text
~/Documents/memo-project/Obsidian Vault
```

It is not a Git checkout. Its Markdown files sit at that directory's root. The
private Gitea repository instead stores canonical pages under `wiki/`, so the
folder Obsidian opens under Option A becomes:

```text
~/Documents/memo-project/snp-vault-obsidian/wiki
```

The setup below does **not** move, overwrite, or delete the current vault. It
creates a second directory, checks out only `wiki/`, and lets the owner switch
back simply by reopening the old folder. After the switch, edits in the two
folders are independent; do not keep editing both.

The new checkout starts from Gitea's canonical pages. It does not copy local-only
notes, attachments, or unpublished changes from the old folder. Before switching
daily work, compare the two vaults in your editor and preserve any local-only
material deliberately; the old folder remains available throughout.

Git's current [`clone --sparse`](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt---sparse)
starts a sparse checkout, and
[`sparse-checkout set`](https://git-scm.com/docs/git-sparse-checkout)
selects the directory to materialize. No deprecated `sparse-checkout init`
step is needed.

## Option A — sparse checkout for the daily vault

### 1. Preflight without changing either vault

Run these commands in a terminal. Stop if the destination already exists or
if Gitea's health check is not `ok`.

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
test ! -e "$SNP_OBSIDIAN_CHECKOUT"
curl -fsS http://127.0.0.1:3000/api/healthz
git --version
```

Expected results are exit status 0 for all four commands, an `ok` Gitea
response, and a modern Git version. The existing daily vault is deliberately
not used as the clone destination.

### 2. Clone private Gitea and materialize only `wiki/`

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
git clone --filter=blob:none --sparse --branch main \
  http://127.0.0.1:3000/snp-admin/snp-memory.git \
  "$SNP_OBSIDIAN_CHECKOUT"
git -C "$SNP_OBSIDIAN_CHECKOUT" sparse-checkout set wiki
git -C "$SNP_OBSIDIAN_CHECKOUT" sparse-checkout list
git -C "$SNP_OBSIDIAN_CHECKOUT" remote get-url origin
git -C "$SNP_OBSIDIAN_CHECKOUT" status --short --branch
```

Git may ask for the owner's Gitea username and password or token. Use the
machine's credential helper; do not put a token in the remote URL. The expected
sparse-checkout output is exactly `wiki`, the remote must be the private local
Gitea URL above, and status must name `main` with no changes.

If Git warns that the filter was ignored but the clone succeeds, continue with
that clone. The blob filter only reduces transfer size. If cloning fails because
the server rejects the filter, retain the incomplete directory under a different
name and retry without the filter:

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
test ! -e "${SNP_OBSIDIAN_CHECKOUT}.failed" &&
  if test -e "$SNP_OBSIDIAN_CHECKOUT"; then
    mv "$SNP_OBSIDIAN_CHECKOUT" "${SNP_OBSIDIAN_CHECKOUT}.failed"
  fi &&
  git clone --sparse --branch main \
    http://127.0.0.1:3000/snp-admin/snp-memory.git "$SNP_OBSIDIAN_CHECKOUT" &&
  git -C "$SNP_OBSIDIAN_CHECKOUT" sparse-checkout set wiki
```

Use this fallback only for the failed initial clone, then repeat the remote and
status checks above. It preserves any incomplete directory for inspection.

Obsidian creates local application state beneath the vault it opens. Keep that
state out of commits without changing the shared repository ignore rules:

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
printf '%s\n' '/wiki/.obsidian/' >> \
  "$SNP_OBSIDIAN_CHECKOUT/.git/info/exclude"
git -C "$SNP_OBSIDIAN_CHECKOUT" check-ignore -v wiki/.obsidian/
```

The final command should identify `.git/info/exclude`. This exclusion is local
to this clone; it cannot hide or alter another contributor's tracked files.

### 3. Open the new vault

In Obsidian, choose **Open folder as vault** and select:

```text
~/Documents/memo-project/snp-vault-obsidian/wiki
```

Do not select the checkout root. The repository's `wiki/` directory is the
vault root; opening one directory too high changes wikilink and plugin paths.
Confirm visually that the expected notes are present, then close the old vault
window so edits cannot land in the wrong copy.

### 4. Daily human-edit flow

Pull before editing, commit only the intended page, and push directly to the
private vault repository's `main`, as the repository's human-editing contract
allows:

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
test "$(git -C "$SNP_OBSIDIAN_CHECKOUT" branch --show-current)" = main &&
  git -C "$SNP_OBSIDIAN_CHECKOUT" diff --cached --exit-code &&
  git -C "$SNP_OBSIDIAN_CHECKOUT" pull --ff-only origin main

# Stop if a check above fails. Resolve pre-existing staged work first.

# Edit and save the page in Obsidian, then inspect the exact change.
git -C "$SNP_OBSIDIAN_CHECKOUT" status --short -- wiki
git -C "$SNP_OBSIDIAN_CHECKOUT" diff -- wiki/path-to-page.md

git -C "$SNP_OBSIDIAN_CHECKOUT" add -- wiki/path-to-page.md
git -C "$SNP_OBSIDIAN_CHECKOUT" diff --cached
git -C "$SNP_OBSIDIAN_CHECKOUT" diff --cached --check
# Review ALL staged changes; commit only if they are the intended publication.
git -C "$SNP_OBSIDIAN_CHECKOUT" commit -m "docs(wiki): describe the edit"
git -C "$SNP_OBSIDIAN_CHECKOUT" push origin main
```

Replace `wiki/path-to-page.md` with the one page actually edited. Never use
`git add -A` from the vault: explicit staging prevents Obsidian settings,
attachments, or unrelated notes from joining the push.
Git commits the whole index, so explicit `add` alone does not exclude work
already staged by another tool. The initial index check and final staged-diff
review are part of every publication.

After `git push` returns, the existing Gitea webhook, host-sync, and sync-job
path handles publication and indexing. No SNP command is required. The current
measured pipeline is 5.653 seconds p50 / 7.781 seconds p95 from successful push
return to the first search hit; each hit was read and confirmed afterward.
The owner-approved I-3 criterion is 10 seconds p95 over ten warm one-page edits,
including read confirmation. Read completion must be timed in the rehearsal;
the historical samples did not record it. Report editor save→push separately.

### 5. Reversal

First preserve any work that exists only in the new checkout:

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
git -C "$SNP_OBSIDIAN_CHECKOUT" status --short --branch
git -C "$SNP_OBSIDIAN_CHECKOUT" log --oneline origin/main..main
```

If either command shows uncommitted or unpushed work, commit and push it or copy
it somewhere safe before continuing. Then close the new Obsidian vault and use
**Open folder as vault** to reopen:

```text
~/Documents/memo-project/Obsidian Vault
```

No filesystem deletion is required. To make the reversal obvious while
retaining a recoverable copy, rename the checkout only after the two checks
above are clean:

```bash
SNP_OBSIDIAN_CHECKOUT="$HOME/Documents/memo-project/snp-vault-obsidian"
test ! -e "${SNP_OBSIDIAN_CHECKOUT}.retired" &&
  mv "$SNP_OBSIDIAN_CHECKOUT" "${SNP_OBSIDIAN_CHECKOUT}.retired"
```

Opening the original folder restores the old workflow immediately. Any edit
made there after reversal again has no automatic path to Gitea.

## Obsidian Git variant of Option A

This variant is deferred by the owner's **manual Git first** decision. If the
owner elects to enable it later, first demonstrate manual pull/commit/push.
Configure the plugin against the checkout Obsidian
already opened; do not let it initialize a second repository inside `wiki/`.

The plugin's primary
[feature documentation](https://github.com/Vinzent03/obsidian-git/blob/master/docs/Features.md#automatic-commit-and-sync)
says automatic commit-and-sync runs every configured **X minutes**, or X
minutes after editing stops. Its source-control view can stage, commit, pull,
and push. Those behaviors add four owner-visible choices:

- which files automatic staging may include;
- how pull conflicts are surfaced before a push;
- where Gitea credentials are stored;
- how many minutes may pass before an edit is committed and pushed.

That interval is part of editor-to-answer latency. A five-minute interval, for
example, adds up to five minutes before the measured 5.653-second p50 /
7.781-second p95 push-to-query pipeline even begins. Do not hide it inside the
pipeline number or describe the result as sub-ten-second Obsidian propagation.
Report two spans: **edit saved -> push complete** and **push complete -> served
read**.

The plugin makes “no operator commands required” plausible, but it does not
make publication instantaneous. It also broadens automatic staging, so verify
the plugin's status view before enabling a timer.

## Other owner choices

### Option B — explicit one-way sync

Keep opening `~/Documents/memo-project/Obsidian Vault` and run a reviewed script
that copies allowed Markdown paths into a private checkout, shows deletions and
the diff, then commits and pushes.

Daily cost: the owner retains his current folder but must remember and run a
sync action for every publication. The script needs an explicit deletion
policy and exclusions for `.obsidian/`, attachments, and unrelated files.
Most importantly, Option B makes W-2's **“no operator action required”** wording
false. That phrase is load-bearing in `docs/DEMO_OPENCODE.md`; choosing B
requires changing the claim, not pretending the script is automatic.

No copy script is supplied here because its source/destination and deletion
policy are themselves owner choices. Running a guessed one against the daily
vault would be the unsafe part of this option.

### Option C — separate rehearsal checkout

Create the same sparse checkout as Option A, but open it only as a second,
demo-specific Obsidian vault. Continue daily work in the current vault.

Daily cost: none outside rehearsal. Rehearsal cost: the owner must consciously
switch vaults and ensure the demonstration edit is made in the clone. This
proves the downstream Git-to-answer system but not the claim that an edit in
his daily vault already publishes. The narration must call it a demo checkout.

### Comparison

| Choice | Daily folder | Publication action | W-2 as currently worded | Main cost |
| --- | --- | --- | --- | --- |
| A | New checkout's `wiki/` | Manual Git commit/push | Closest match, but still explicit Git actions | Change the vault folder and pull before edits |
| A + Obsidian Git | New checkout's `wiki/` | Timed plugin automation | Can make operator commands disappear | Timer enters latency; auto-staging/conflicts/credentials |
| B | Existing vault | Run a sync script | **False**: requires operator action | Safe copy/deletion policy and human discipline |
| C | Existing daily vault plus demo clone | Manual Git in demo clone | Demo-only, not daily workflow | Weaker claim and explicit vault switching |

## If the owner does nothing

Nothing breaks in search, read, host-sync, or indexing. What remains broken is
the first hop of W-2:

- edits saved in the current Obsidian vault do not reach private Gitea;
- therefore they do not trigger host-sync or sync-job;
- E11 remains unverified: the workflow choice is recorded, but setup and
  demonstration still require owner action;
- the rehearsal must either use Option C and say so, or record W-2 as
  unverified;
- I-3 continues to measure only a synthetic pushed edit, not an edit from the
  owner's daily Obsidian workflow.

Option A with manual Git first is selected. No vault setup, migration, plugin
installation, or demonstration was executed by this documentation task.
