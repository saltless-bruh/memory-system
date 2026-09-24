# Vault access and the pluralism principle — a finding

**Date:** 2026-09-08
**Status:** finding, not a fix. The decision is the owner's.
**Supersedes nothing.** Records a divergence between the design and the build.

---

## The principle, from the original proposals

`docs/proposal/legacy/Proposal_LLM_Wiki_v0.3.md:49` states requirement **R5**:

> **R5** — *"Ai cũng dùng được tool mình quen"* — everyone uses the tool they
> already know, and it still syncs.

The mechanism is the substrate, not an adapter:

> `Git là file → BẤT KỲ file-based tool nào cũng edit được → pluralism (R5)`
> *Git is files → ANY file-based tool can edit it.*

And `Proposal_LLM_Wiki_v0.2.md:100` names the editing surfaces:

> **Con người:** Obsidian **trên clone của họ**, hoặc IDE, hoặc bất kỳ markdown
> editor nào. **Agent:** đọc/ghi file và **chỉ mở PR**.
> *Humans: Obsidian **on their clone**, or an IDE, or any markdown editor.
> Agent: reads/writes files and only opens PRs.*

`Proposal_LLM_Wiki_v0.2.md:112` (§5.1) gives the ingest flow:

> 1. Member (or agent) adds/edits a file → **commit**.
>    - Human: may commit to their own branch and open a PR, or push straight to
>      `main` depending on team policy.
>    - Agent: **always** opens a PR, never writes to `main` directly.

**The clone is what grants the freedom.** Pluralism depends on the vault being a
checkout; it is not restricted by it. R-6.4/R-7.3 as enforced today ("no exposed
tool performs a git push") is the same rule as v0.2's *"Agent: luôn mở PR"* — it
is continuity, not drift.

---

## The divergence

`Proposal_SNP_Memory_System_v2.md:121` specifies the store as:

> `W1["Git vault (per-department): index.md · các trang .md"]`

**Built instead:** one repository, `snp-admin/snp-memory`, holding all 432 wiki
pages, with department scoping expressed only in page frontmatter and enforced
only by PostgreSQL row-level security at query time.

```
  DESIGNED                          BUILT
  ────────────────────              ────────────────────────────
  vault per department              ONE repo · 432 pages
  git access = the boundary         RLS gates the INDEX
                                    git gates NOTHING
```

---

## Why this matters

Row-level security protects **retrieval**. It does not protect **distribution**.

Pluralism requires each person to hold a clone — that is the whole mechanism.
But a clone of a single all-departments repository hands the holder every page,
including departments they cannot query through `wiki_search`. The retrieval
boundary is bypassed by the distribution mechanism that pluralism depends on.

This is invisible today because one person owns every department. It becomes
live the moment a second person, or another IT department, is given a clone so
they can use their own editor.

**Consequence:** "everyone uses whatever tool they like" is safe for the owner
and not yet safe for anyone else.

---

## Two further gaps named in the proposals and never built

**The lock service.** `Proposal_LLM_Wiki_v0.1.md:117` is explicit that
human-versus-agent write collisions cannot be solved by locking, because a human
editing in their own surface never passes through the agent's chokepoint, and a
CRDT merge of an agent's regenerated section with a human's half-written
paragraph produces coherent-looking nonsense. v0.2 §6 specified a lock service
holding `file → {holder, role}`. It does not exist. Harmless with one human;
a real gap once agents author against pages people are editing.

**Commit granularity.** A notes application encourages many small edits; git
wants deliberate commits. An auto-commit plugin resolves that by publishing
everything, which makes in-progress thinking immediately retrievable by every
agent in scope. The vault and the repository hold the same pages (433 against
432), so this is not hypothetical.

---

## What follows

Ordered by what blocks what:

1. **Does the vault stay one repository?** This outranks the Obsidian question.
   It decides whether pluralism can extend past the owner at all, and it is a
   prerequisite for the per-team rollout described in the install design.
2. **Restore the owner's vault as a clone** (E11 option A). Small, and it
   restores the design as originally written rather than adopting a workaround.
   Safe today precisely because the owner holds every department.
3. **Lock service, or an explicit decision not to have one.** Deferring is
   defensible; leaving it unrecorded is not.

Nothing here is urgent for the Monday rehearsal. Item 1 is urgent before any
second person receives a clone.
