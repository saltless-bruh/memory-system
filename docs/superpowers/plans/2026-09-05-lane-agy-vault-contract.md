# Lane: Vault Contract — Antigravity

> Steps use checkbox (`- [ ]`) syntax. Work through them in order.

**Read first:** `2026-09-05-three-lane-coordination.md`.

**Goal:** Bring the 432-page knowledge vault to the two heading requirements that can be derived from what the pages already contain — `## TL;DR` and `## Cross-References` — and resolve the case-variant directory collision. Nothing in this lane invents content.

**Architecture:** Every change here is a *derivation* from material already in the page. Cross-References is gathered from `[[wikilinks]]` that already sit in page bodies, or renamed from a heading that already holds them. TL;DR is a summary of the page's own text. Where neither is derivable, the page is **reported, not filled**.

**Tech Stack:** Markdown, git, Python 3 for the batch scripts. No project toolchain needed.

**Spec:** `docs/proposal/V3_disposition_status_2026-09-05.md` §Layer 1.

---

## Your territory — and only this

```
<your own clone of snp-admin/snp-memory.git>
  branch: vault/contract   (off gitea/main, NOT off feat/v3-retrieval-inversion)
  you touch:  wiki/**
  you touch nothing else, ever
```

You are **not** working in `/home/ple/Documents/memo-project/snp-memory-system-main`. Two other agents are editing code there right now. `wiki/` on that branch holds an 8-page sample and is a deliberate fork — ignore it entirely.

```bash
git clone http://127.0.0.1:3000/snp-admin/snp-memory.git ~/vault-work
cd ~/vault-work
git checkout -b vault/contract origin/main
git config core.quotePath false      # the vault has Vietnamese filenames
find wiki -name '*.md' | wc -l       # expect 432
```

`git config core.quotePath false` is not optional. Without it git C-quotes non-ASCII paths and every script in this document will silently skip Vietnamese-titled pages.

## The rule that matters most here

**Never invent content.** This is not a style preference.

- A `## Provenance` section is a **dated sourcing changelog** — real history of where the page's material came from. Writing one for a page that has none means inventing dates and sources. **You are not asked to touch Provenance. Do not.**
- A `## Technical Specifications` section describes real specifications. **You are not asked to create these. Do not.**
- A `## TL;DR` summarises the page's *own existing text*. That is derivation and is in scope.
- A `## Cross-References` lists links the page *already contains*. That is gathering and is in scope.

Where a page offers nothing to derive from, **add it to the exception report and move on.** A page listed as an exception is a correct outcome. A page padded with an empty or invented section is a defect.

## You are blocked until handoff H2

The vault linter currently requires four headings:

```python
REQUIRED_HEADINGS = ("TL;DR", "Technical Specifications", "Provenance", "Cross-References")
```

Claude is changing this so that only `TL;DR` and `Cross-References` are required, and `Technical Specifications` / `Provenance` become optional. **Until that lands, every page you fix still fails the linter**, so your work cannot be verified.

Ask before you start: *"has H2 landed?"* If the answer is no, wait.

The canonical page shape after H2:

```markdown
---
title: ...
type: ...
---

# Page Title

## TL;DR
One to three sentences summarising this page.

## Technical Specifications   ← optional, leave alone
## Provenance                 ← optional, leave alone
## Works Cited                ← optional, leave alone
## Cross-References
- [[Some Other Page]]
- [[Another Page]]
```

**Order is enforced.** `## TL;DR` comes first among the headings; `## Cross-References` comes last. Optional sections sit between them.

## Baseline — measure before you touch anything

- [ ] **Step 0: Record the starting state**

```bash
cd ~/vault-work
python3 - <<'PY'
import pathlib, re
root = pathlib.Path("wiki")
pages = sorted(root.rglob("*.md"))
h = lambda name: sum(1 for p in pages if re.search(rf"^## {re.escape(name)}\s*$", p.read_text(encoding="utf-8", errors="replace"), re.M))
print("pages                    :", len(pages))
print("## TL;DR                 :", h("TL;DR"))
print("## Technical Specifications:", h("Technical Specifications"))
print("## Provenance            :", h("Provenance"))
print("## Cross-References      :", h("Cross-References"))
wl = sum(1 for p in pages if "[[" in p.read_text(encoding="utf-8", errors="replace"))
print("pages containing [[links]]:", wl)
PY
```

Expected, matching the audit: 432 pages · TL;DR 12 · Technical Specifications 0 · Provenance 91 · Cross-References 0 · with links ~369.

**If these numbers differ materially, stop and report.** Everything below is sized against them.

---

### Task 1: Rename existing link headings to `## Cross-References`

202 pages already carry their links under a differently-named heading. Renaming is pure mechanics — no content changes at all.

Heading variants measured in the corpus:

```
## Related           101      ## Related ideas        5
## Related pages      36      ## Related Concepts     5
## Links              26      ## Related provenance   4
## See also           21      ## Related Pages        4
```

⚠️ `## Related provenance` (4 pages) is **not** a links heading — the word is a coincidence. Inspect those four by hand before deciding; if they hold prose rather than links, leave them.

- [ ] **Step 1: Dry-run the rename and read the report**

```bash
cd ~/vault-work
python3 - <<'PY'
import pathlib, re
VARIANTS = ["Related pages", "Related Pages", "Related Concepts", "Related ideas",
            "Related", "Links", "See also"]
pat = re.compile(r"^## (" + "|".join(re.escape(v) for v in VARIANTS) + r")\s*$", re.M)
hits = []
for p in sorted(pathlib.Path("wiki").rglob("*.md")):
    t = p.read_text(encoding="utf-8", errors="replace")
    m = pat.findall(t)
    if m:
        body_after = t[pat.search(t).end():]
        links = "[[" in body_after.split("\n## ")[0]
        hits.append((str(p), m, links))
print(f"{len(hits)} pages match a variant heading")
print(f"{sum(1 for _,_,l in hits if not l)} of them have NO wikilinks under it -- inspect by hand:")
for path, m, l in hits:
    if not l: print("   ", path, m)
PY
```

Read that list. A heading with no links under it is not a Cross-References section; **exclude those paths in the next step**.

- [ ] **Step 2: Apply the rename**

```bash
cd ~/vault-work
python3 - <<'PY'
import pathlib, re
VARIANTS = ["Related pages", "Related Pages", "Related Concepts", "Related ideas",
            "Related", "Links", "See also"]
EXCLUDE = set()          # <-- paste the no-links paths from Step 1 here
pat = re.compile(r"^## (?:" + "|".join(re.escape(v) for v in VARIANTS) + r")\s*$", re.M)
n = 0
for p in sorted(pathlib.Path("wiki").rglob("*.md")):
    if str(p) in EXCLUDE: continue
    t = p.read_text(encoding="utf-8", errors="replace")
    new, k = pat.subn("## Cross-References", t)
    if k:
        p.write_text(new, encoding="utf-8"); n += k
print(f"renamed {n} headings")
PY
```

- [ ] **Step 3: Verify and commit**

```bash
grep -rc "^## Cross-References" wiki --include='*.md' | grep -c ':1$'   # pages now carrying it
git add wiki && git commit -m "vault: rename link headings to Cross-References

Pure rename. No content added, changed or removed."
```

- [ ] **Step 4: HANDOFF H3 — stop here**

Tell Claude the batch is ready. Claude measures `retrieval_quality.py` before and after. **Do not merge and do not start Task 2 until Claude reports the recall number.** Baseline is recall@1 0.80, recall@5 1.00; a drop means this batch broke retrieval and must be reverted, not argued with.

---

### Task 2: Gather `## Cross-References` for pages that have links but no heading

After Task 1, some pages still carry `[[wikilinks]]` inline in their prose with no dedicated section.

- [ ] **Step 1: Find them and report the ones you cannot serve**

```bash
cd ~/vault-work
python3 - <<'PY'
import pathlib, re
WL = re.compile(r"\[\[([^\]]+)\]\]")
need, empty = [], []
for p in sorted(pathlib.Path("wiki").rglob("*.md")):
    t = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^## Cross-References\s*$", t, re.M): continue
    links = sorted({m.split("|")[0].split("#")[0].strip() for m in WL.findall(t)})
    (need if links else empty).append((str(p), links))
print(f"{len(need)} pages have links to gather")
print(f"{len(empty)} pages have NO links at all -- these become the exception report")
for path, _ in empty[:20]: print("   ", path)
PY
```

- [ ] **Step 2: Append the section, links only, nothing invented**

```bash
cd ~/vault-work
python3 - <<'PY'
import pathlib, re
WL = re.compile(r"\[\[([^\]]+)\]\]")
n = 0
for p in sorted(pathlib.Path("wiki").rglob("*.md")):
    t = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^## Cross-References\s*$", t, re.M): continue
    links = sorted({m.split("|")[0].split("#")[0].strip() for m in WL.findall(t)})
    if not links: continue                      # never create an empty section
    body = t.rstrip("\n")
    body += "\n\n## Cross-References\n" + "\n".join(f"- [[{l}]]" for l in links) + "\n"
    p.write_text(body, encoding="utf-8"); n += 1
print(f"added Cross-References to {n} pages")
PY
```

Cross-References is the **last** heading, so appending at end of file is correct placement.

- [ ] **Step 3: Write the exception report**

Create `wiki/_contract-exceptions.md` listing every page with no wikilinks at all, under a heading explaining that these pages are genuinely isolated and that a Cross-References section was deliberately **not** created rather than padded. This file is your report to the owner, not a vault page — name it exactly as above so it is obvious it is not content.

- [ ] **Step 4: Commit, then HANDOFF H3 again**

```bash
git add wiki && git commit -m "vault: gather Cross-References from links already in the body

Links only. Pages with no links are reported in _contract-exceptions.md rather
than given an empty section."
```

Stop. Wait for Claude's recall measurement.

---

### Task 3: `## TL;DR` — 12 pages have one, 420 do not

This is the only task in this lane requiring judgment, and it is bounded judgment: **summarise what the page already says.** One to three sentences. If you cannot write a TL;DR without knowing something the page does not tell you, the page goes in the exception report.

- [ ] **Step 1: Read the twelve that already have one**

```bash
cd ~/vault-work
grep -rl "^## TL;DR" wiki --include='*.md' | head -12 | while read -r f; do
  echo "=== $f"; awk '/^## TL;DR/{p=1;next} p&&/^## /{exit} p' "$f" | head -4
done
```

Match their length and voice. Do not invent a house style.

- [ ] **Step 2: Work in batches of no more than 50 pages**

For each page: read it, write one to three sentences drawn only from its own content, and insert `## TL;DR` immediately after the `# Title` line — it is the first heading in the canonical order.

Do not batch more than 50 before a commit and a handoff. A 420-page single commit cannot be reviewed and cannot be partially reverted.

- [ ] **Step 3: Commit each batch and HANDOFF H3**

```bash
git add wiki && git commit -m "vault: TL;DR for <N> pages (batch <k>)

Each summary drawn from the page's own text. Pages whose content does not
support a summary are listed in _contract-exceptions.md."
```

Stop after each batch. Claude measures recall. **A recall drop after a TL;DR batch is the signal that matters most in this lane** — TL;DR becomes chunk 0 of the indexed page, so a bad summary changes what the page retrieves on.

---

### Task 4: The case-variant directory collision

`Concepts/` and `concepts/` coexist, as do `Entities/` and `entities/`. Harmless on Linux and in git; **breaks on checkout for macOS or Windows.** If the owner's lead clones this vault on a Mac, it fails in front of him.

- [ ] **Step 1: Measure the collision**

```bash
cd ~/vault-work
find wiki -type d | sed 's|.*/||' | sort | tr 'A-Z' 'a-z' | uniq -d
find wiki -type d -maxdepth 2 | sort
```

- [ ] **Step 2: Ask before choosing a direction**

Which case wins is the owner's call, not yours — it changes every `[[wikilink]]` that targets a moved page. Report the collision with counts on each side and **wait for a decision**.

- [ ] **Step 3: After the decision — move, then repair links**

Use `git mv` with a two-step rename (`Concepts` → `Concepts.tmp` → `concepts`); a direct case-only rename is a no-op on some filesystems. Then update every `[[wikilink]]` whose target moved, and re-run the Task 2 script to confirm no link now points at a missing page.

- [ ] **Step 4: Commit and HANDOFF H3**

This batch changes paths, so `source_uri` changes for every moved page. Claude's recall measurement matters more here than anywhere else in this lane.

---

## Never touch

- **`wiki/index.md`** — it carries 306 authored descriptions. A generator would replace them with blanks. Leave it entirely alone.
- **`wiki/log.md`** — authored, and excluded from indexing on purpose.
- **`wiki/SCHEMA.md`** — the contract itself. Changing it to match the vault is backwards.
- **`## Provenance`** and **`## Technical Specifications`** — see "The rule that matters most here".
- Anything outside `wiki/`.

## Definition of done

1. `## Cross-References` present on every page that has links to put under it.
2. `## TL;DR` present on every page whose own content supports one.
3. `wiki/_contract-exceptions.md` names every page deliberately skipped, with the reason.
4. Heading order correct: `TL;DR` first, `Cross-References` last.
5. Every batch measured by Claude, with recall at or above 0.80 / 1.00.
6. `wiki/index.md` byte-identical to where it started.
7. No content invented anywhere — no Provenance, no Technical Specifications, no empty sections.

## How to report

Give the measured counts before and after, the batch commits, and the exception list length. If a batch drops recall, say so and revert it — a reverted batch reported honestly is a good outcome; a merged batch that quietly cost retrieval is not.
