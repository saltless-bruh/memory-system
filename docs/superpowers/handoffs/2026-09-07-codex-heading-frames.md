# Prompt for Codex — route two call sites to the authored heading frame

```text
Two corrections and one small task. Both fixes you shipped are verified good;
this is not rework.

1. YOUR ingest-wiki COUNTER FIX IS CORRECT. VERIFIED.

  $ snpmemory ingest-wiki --dir wiki --dry-run -o json
    would_index: 6  indexed: 0  purged: 0  unchanged: 0  skipped: 0
    actual statuses: Counter({'dry_run_ok': 6})

`would_index` is right, `skipped` is no longer a remainder that absorbs every
status nobody enumerated. That was the defect and it is closed.

Ignore any note you were given about `summary` being None on the -o json path.
I checked it against another command: `verify-secrets -o json` has no summary
key either. JSON emits `data`, human mode emits `summary`. That is the
dispatcher's design, not your bug. I raised it in error.

2. I REFORMATTED tests/test_cli_wiki.py UNDER YOU. SORRY.

I ran `ruff format .` across the whole tree while you had uncommitted work in
that file. The change is formatting only -- I checked, and its 21 tests pass --
but it is in your lane and I should not have touched it. If you see a diff
there you did not write, that is why. Do not read it as drift.

3. THE TASK: TWO CALL SITES, ONE ARGUMENT EACH.

`scout/vault.py` now has two heading frames instead of one rule:

  HeadingFrame.COMPILED   the existing rule, unchanged: exactly the required
                          sequence with optional headings at their anchors.
  HeadingFrame.AUTHORED   required headings present, in order, exactly once;
                          any other section permitted.

Why: one predicate was judging two document classes. compile_note.py:552-570
emits a fixed five-heading frame and checking it exactly is how drift in that
generator gets caught -- that check is correct and stays. But the reference
vault is hand-authored, and measured on 2026-09-07 **0 of its 430 content
pages** satisfy that frame, with **1006 distinct non-contract `##` headings**
in use (`## Trade-offs` on 47 pages, `## Liên quan` on 45, `## Why it matters`
on 29). A check that fails every input separates nothing. Under the AUTHORED
frame the same vault passes 8 pages today, which makes it a target rather than
a wall.

`lint_page` defaults to COMPILED, so nothing changed behaviourally when I
landed this. Two call sites should now ask for AUTHORED, because their subject
is the authored vault:

  scripts/gen_index.py:69            vault.lint_page(page, known_slugs=slugs)
  scout/cli/commands/verify.py:33    collect_lint(pages)   ← follows gen_index

Add `frame=vault.HeadingFrame.AUTHORED` at the gen_index call site. Check
whether `collect_lint` needs to thread it through or simply inherits it; read
the code rather than assuming.

DO NOT touch scripts/compile_note.py:786. It generates the compiled frame and
the default is already right for it. Changing it would silently stop checking
the one document class the strict rule describes correctly -- which is the
whole reason there are two frames instead of one relaxed one.

DONE MEANS:
  - `snpmemory verify-vault` (and gen_index) judge authored pages by the
    authored frame, and report a number that is not 0.
  - compile_note still rejects a candidate whose frame drifts. Prove it, do
    not assert it: construct a page with an extra `## Notes` section and show
    compile_note's lint refuses it.
  - The offline suite is green. It is 1342 passed / 29 deselected as of my
    last run; the engine lane is landing commits in parallel so a higher
    number is not a discrepancy. State what you measured.
  - ruff, ruff format and mypy clean.

VERIFY BEFORE YOU REPORT:
  rm -rf .agents .codex
  env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY \
    uv run pytest -m 'not integration' --disable-socket -q
  uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts

Run the suite whole, never from a fail-fast run. Report the command, its exit
status, and the figure it produced. Still no push, no merge, no branch.
```
