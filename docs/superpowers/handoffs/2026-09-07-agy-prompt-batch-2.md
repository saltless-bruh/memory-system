# Prompt for Agy — H3 result and the reframe for Task 2

Paste the block below to Agy.

```text
Batch 1 is accepted. Before you merge it, one measurement changes what Task 2
is for, so read this in full before touching a page.

1. YOUR NUMBERS WERE RIGHT AND MINE WERE WRONG.

I reported ## TL;DR on 12 pages against your 9, and ## Provenance on 91
against your 70, and told you your regex was too strict. That was my error.
I matched the heading as a prefix; you matched it as a heading. Yours is the
one that describes the contract:

  ## TL;DR       9 bare  + 3 variants  (## TL;DR Quick Pick, ## TL;DR decision tree)
  ## Provenance  70 bare + 21 variants (17 of them ## Provenance-only role)

A variant is a different heading. scout/vault.py::_headings_are_ordered reads
"## TL;DR Quick Pick" as the heading "TL;DR Quick Pick", which satisfies
nothing -- and adding a bare ## TL;DR beside it leaves the page with both,
which is also rejected. Measured:

  ('TL;DR','Cross-References')                     -> True
  ('TL;DR Quick Pick','Cross-References')          -> False
  ('TL;DR','TL;DR Quick Pick','Cross-References')  -> False

Those 24 pages need per-page judgement. Report what they are; do not
bulk-rename them. ## TL;DR Quick Pick probably is that page's TL;DR.
## Provenance-only role almost certainly is not a sourcing changelog -- leave
it alone.

2. THE HEADING CONTRACT CANNOT BE SATISFIED BY THIS VAULT, AND THAT IS NOT
   YOUR FAULT OR YOUR PROBLEM TO FIX.

I ran the real linter over all 430 content pages:

  pages passing the heading contract today : 0
  pages failing                            : 430
  distinct non-contract H2 headings in use : 1006
     47 ## Trade-offs      45 ## Liên quan     30 ## Cluster position
     29 ## Why it matters  29 ## Dùng để      28 ## What it is

_headings_are_ordered requires the page's H2 headings to equal the contract
sequence exactly. Any additional heading fails it. So a page carrying
## Trade-offs fails no matter how perfect its TL;DR is, and adding TL;DR and
Cross-References to all 430 pages would still leave 0 passing.

The reason is in scripts/compile_note.py:556-568: the contract was written for
*compiled* pages, which that script generates with exactly that frame. The
lead's vault is hand-authored with 1006 section headings. The contract was
never designed for it, and nothing blocks on it -- the three consumers
(verify-vault, gen_index, compile_note) all report rather than enforce.

Do not try to make pages pass. Do not delete sections to make them pass. That
would destroy the vault to satisfy a linter written for different documents.
Whether that contract should change is the owner's decision and mine to
implement; it is not vault work.

3. SO TASK 2'S PURPOSE CHANGES: WRITE FOR RETRIEVAL, NOT FOR THE LINTER.

This is not a demotion. It is the more valuable half, and it is measurable.

scout/wiki_ingest.py makes the ## TL;DR section **chunk 0** of the indexed
page. It is the first thing the retriever scores a query against, and it
carries the page's identity. A page with a sharp TL;DR is found by the
question a person actually asks; a page without one is found by whatever
fragment happens to match. That is the whole job.

Which means the bar for a TL;DR is not "a heading exists". It is: does this
sentence contain the words someone would search for when they want this page?
A TL;DR that restates the title tells the retriever nothing it did not have.

4. MERGE BATCH 1 FIRST. IT IS NOT PUSHED YET.

git ls-remote --heads origin shows only refs/heads/main -- vault/contract
exists only in your working copy, one commit ahead. Nothing I can measure.
Also note main has moved since you branched (acceptance-gate probe commits,
already reverted, net zero to the vault).

  cd ~/vault-work
  git fetch origin
  git rebase origin/main          # expect no conflicts; you touched headings only
  git checkout main && git merge --ff-only vault/contract
  git push origin main

Then stop and tell me. I will measure recall on the served index and report
the number back.

5. THE H3 PROTOCOL HAS CHANGED, FOR THIS BATCH ONLY.

H3 said I measure before and after your batch. I cannot measure "before a
merge": recall is a property of the served index, the index is built from the
vault replica, and the replica follows gitea/main. A branch that never lands
is invisible to it.

So the order is now: merge, I measure, and it is reverted if recall dropped. A
push reaches the served index in about 7 seconds and unchanged pages are
skipped, so a revert is a commit and a few seconds, not an operation.

That argument holds for a heading rename, which cannot change page prose. It
does not automatically hold for Task 2, which writes sentences. Show me the
first TL;DR batch as a branch before it goes anywhere near main.

6. WHAT BATCH 1 ACTUALLY DID, SINCE YOUR REPORT DID NOT SAY.

192 renames, and they are not equivalent. scout/wiki_ingest.py:65 defines
_LINK_SECTION_NAMES = {crossreferences, related, seealso, links, lienquan},
and a section whose heading normalises into that set has its pure-wikilink
lines stripped from the indexed text:

  Related 101, See also 21, Links 20   -> 142 already link sections: no change
  Related pages 36, Related Pages 4,   ->  50 were NOT: their wikilink lines
  Related ideas 5, Related Concepts 5      are now stripped from the index

So 50 pages changed what they index. That is correct and intended, and it is
the only part of batch 1 that can move the number I am about to measure.

Also: "Pure rename. No content added, changed or removed" was not accurate.
The diff is +192/-361; the 169 extra deletions are blank lines, because your
pattern ended \s*$ under re.M and \s matches a newline. I checked and it
breaks nothing -- parse_markdown still segments those pages and their
wikilinks still extract. No rework needed. But a claim that is wrong in a
harmless way still costs someone the check, so state it precisely next time.

7. RULES THAT DO NOT CHANGE.

- Never invent. A TL;DR is a summary of what the page already says. If the
  page does not say enough to summarise, it goes in wiki/_contract-exceptions.md
  with one line on why. A long exception list is a correct outcome. One
  invented sentence is a failure, and it is invisible in review, which is
  exactly why it is the rule.
- 50 pages per batch in Task 2. I accepted 192 for the rename because I could
  classify all of them with one command. I cannot do that with prose.
- Never touch wiki/index.md, wiki/log.md, wiki/SCHEMA.md. I verified they are
  byte-identical to main; keep them that way.
- Your six (none yet) exclusions were right. An empty section satisfies a
  counter and fails a reader.

8. WHAT TO REPORT.

After the merge: nothing but that it landed, and wait for my number.

After each Task 2 batch: pages touched, pages added to the exception list and
why, and for three TL;DRs you wrote, the sentence and the line of the page it
came from. I want to be able to check derivation, not just presence.
```
