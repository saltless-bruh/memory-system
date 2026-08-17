#!/usr/bin/env python3
"""propose_page.py — PR-first write path for the wiki (T-1.5 → R-7.3, R-6.4).

basic-memory's `write_note` (and a human editing in an IDE) only writes
Markdown files into the working tree. It has no git awareness. This script
is the bridge that enforces the project's **PR-first** rule: an
agent/automated change must land on a **branch + Pull Request**, never
directly on the main branch, and must pass lint before it is proposed.

Flow:
  1. Refuse to proceed if there are no wiki changes, or if we would be
     committing onto the protected main branch.
  2. Lint the vault (scripts/gen_index.py --check) — a broken page never
     becomes a PR (R-6.5).
  3. Create a branch `wiki/<slug>-<UTC>`, commit only `wiki/` changes.
  4. Push and print the PR-create command (Gitea API or `gh`). We STOP
     there — a human reviews and merges (no auto-merge, R-6.4).

Humans have two other paths (no script needed): technical members edit via
Git directly (R-7.1); non-Git members edit via the Gitea Web UI, which
creates a commit (R-7.2). This script is specifically the agent/automation
path.

Usage:
    python scripts/propose_page.py --title "Kerberoasting"      # real run
    python scripts/propose_page.py --dry-run                    # show plan
    python scripts/propose_page.py --title X --remote origin --push
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_BRANCHES = {"main", "master"}


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check
    )


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def wiki_changes() -> list[str]:
    """Paths under wiki/ that are modified/added/untracked."""
    out = git("status", "--porcelain", "--", "wiki").stdout.splitlines()
    return [ln[3:].strip() for ln in out if ln.strip()]


def slugify(title: str) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in title.lower())
    return "-".join(filter(None, keep.split("-")))[:40] or "page"


def run_lint() -> bool:
    """Gate the PR on the vault linter (T-1.2)."""
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_index.py"), "--check"],
        cwd=REPO_ROOT,
    )
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="", help="page title (names the branch)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument(
        "--push",
        action="store_true",
        help="push the branch (default: local branch only)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base", default="main", help="PR target branch")
    args = ap.parse_args()

    changes = wiki_changes()
    if not changes:
        print(
            "No wiki/ changes to propose. Edit a page first "
            "(write_note or an IDE edit), then re-run."
        )
        return 1

    branch = (
        f"wiki/{slugify(args.title or Path(changes[0]).stem)}-"
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    )
    base_now = current_branch()

    print(f"Base branch:  {base_now}")
    print(f"New branch:   {branch}")
    print(f"Wiki changes: {len(changes)}")
    for c in changes:
        print(f"  - {c}")

    # Safety: never commit the change directly onto a protected branch —
    # that is the whole point of PR-first.
    if base_now in PROTECTED_BRANCHES:
        print(
            f"\nOn protected branch '{base_now}': will branch off it "
            f"(never commit the change here)."
        )

    print("\n[1/3] Lint (gen_index.py --check) ...")
    if args.dry_run:
        print("      (dry-run: skipped)")
    elif not run_lint():
        print("LINT FAILED — not proposing. Fix the page and re-run (R-6.5).")
        return 1

    if args.dry_run:
        print("\n[dry-run] Would run:")
        print(f"  git checkout -b {branch}")
        print("  git add wiki/")
        print(f"  git commit -m 'wiki: propose {args.title or branch}'")
        if args.push:
            print(f"  git push -u {args.remote} {branch}")
        print("  → then open a PR (STOP; human reviews & merges)")
        return 0

    print("\n[2/3] Branch + commit ...")
    git("checkout", "-b", branch)
    git("add", "--", "wiki")
    git("commit", "-m", f"wiki: propose {args.title or branch}", "--no-verify")
    print(f"      committed to {branch}")

    print("\n[3/3] PR ...")
    if args.push:
        git("push", "-u", args.remote, branch)
        print(f"      pushed to {args.remote}/{branch}")
        print("\nOpen the PR (do NOT auto-merge — R-6.4):")
        print(f"  gh pr create --base {args.base} --head {branch} --fill")
        print(
            f"  # or Gitea: POST /api/v1/repos/<owner>/<repo>/pulls "
            f"(head={branch}, base={args.base})"
        )
    else:
        print("      local branch only. To propose:")
        print(
            f"        git push -u {args.remote} {branch}  &&  gh pr create "
            f"--base {args.base} --head {branch} --fill"
        )
    print(
        "\nDONE. A human reviews and merges. On merge, run gen_index.py + "
        "verify_addresses.py (R-6.5)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
