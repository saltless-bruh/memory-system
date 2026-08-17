"""scout/healer.py — Continuous Address Drift Auto-Healer (Step 2), PR-first.

Two triggers, both PR-first (a human reviews before anything reaches `main`):

  • DEFAULT mode (scheduled sweep / local) — for drift in ALREADY-MERGED
    content. Opens its OWN heal branch + PR: branch off current → apply hint
    edits on the branch → log → lint → commit `wiki/` → push (opt-in) → print
    the PR command → return to the original branch. `main` is never mutated.

  • CI mode (`--ci`) — for drift found in an analyst's IN-FLIGHT PR, invoked by
    Gitea Actions. Applies the heals to the CURRENT PR branch's working tree
    (no new branch, no commit); the workflow lints, commits, and pushes to the
    PR branch, where the human reviews the bot commit before merging to `main`.
    Guarded: refuses to run on a protected branch, so it can never touch `main`.

Why not overwrite in place on `main`: the vault is PR-first + pristine by
invariant (R-6.4). Re-minting is verified (the new hint still retrieves the
addressed file), but a human still reviews — mint's top-1 path check misses a
stale `loc` or a semantically-off-but-valid match, and provenance integrity is
the whole point of the system.

INVOCATION
----------
    python scout/healer.py --ci                 # CI: apply to current PR branch
    python scout/healer.py --push               # scheduled: open a heal PR
    python scout/healer.py --dry-run            # preview, write nothing
Library (run_system.py):
    await verify_and_heal_vault(backend)                 # local heal branch
    await verify_and_heal_vault(backend, ci_mode=True)   # apply to current branch
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO_ROOT / "spikes" / "_lib"))
import vault as vault  # type: ignore[import-not-found] # noqa: E402

from scout.types import Address, RagBackend  # noqa: E402
from scripts.mint import MintStatus, mint_address  # noqa: E402
from scripts.propose_page import (  # noqa: E402
    PROTECTED_BRANCHES,
    current_branch,
    git,
    run_lint,
    wiki_changes,
)
from scripts.verify_addresses import (  # noqa: E402
    VerifyStatus,
    _collect_addresses,
    verify_all,
)

WIKI_DIR = REPO_ROOT / "wiki"
LOG_FILE = WIKI_DIR / "log.md"


@dataclass
class ProposedHeal:
    """One drifted address whose re-mint succeeded — a proposal, not a write."""

    page: vault.Page
    old_address: Address
    new_address: Address
    trigger: str  # "DRIFT" or "FAIL"


def apply_heal_edit(
    page: vault.Page, old_address: Address, new_address: Address
) -> bool:
    """Replace the old hint with the new hint in the page file (working tree)."""
    import re

    text = page.path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(hint:\s*)(['\"]?){re.escape(old_address.hint)}\2")
    if not pattern.search(text):
        return False
    safe_new = new_address.hint.replace('"', '\\"')
    page.path.write_text(
        pattern.sub(lambda m: f'{m.group(1)}"{safe_new}"', text, count=1),
        encoding="utf-8",
    )
    return True


def append_heal_to_log(
    page_slug: str, old_hint: str, new_hint: str, trigger: str
) -> None:
    """Record a proposed heal in wiki/log.md (committed as part of the change)."""
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"- [{ts}] AUTO-HEAL ({trigger}): {page_slug} — "
        f"hint {old_hint!r} -> {new_hint!r} (bot fix, pending review)\n"
    )
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text("# Auto-Healer Log\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)


def apply_heals_in_place(heals: list[ProposedHeal], *, dry_run: bool = False) -> int:
    """CI mode: apply heals to the CURRENT branch — no branch, no commit, no push.

    Guarded so it can only run on a PR branch, never on `main`. The workflow
    lints, commits, and pushes; the human reviews the bot commit in the PR.
    """
    if not heals:
        print("No drifted addresses - nothing to apply.")
        return 0

    branch = current_branch()
    if branch in PROTECTED_BRANCHES:
        print(
            f"Refusing CI heal on protected branch '{branch}'. CI mode applies to "
            f"a PR branch only (a human reviews the bot commit before merge)."
        )
        return 1

    if dry_run:
        print(f"[dry-run] Would apply to current branch '{branch}':")
        for h in heals:
            print(
                f"  - {h.page.slug} [{h.trigger}] "
                f"{h.old_address.hint!r} -> {h.new_address.hint!r}"
            )
        return 0

    applied = 0
    for h in heals:
        if apply_heal_edit(h.page, h.old_address, h.new_address):
            append_heal_to_log(
                h.page.slug, h.old_address.hint, h.new_address.hint, h.trigger
            )
            applied += 1
            print(
                f"  applied: {h.page.slug} [{h.trigger}] "
                f"{h.old_address.hint!r} -> {h.new_address.hint!r}"
            )
    print(
        f"Applied {applied} heal(s) to the working tree on '{branch}' "
        f"(uncommitted - CI will lint, commit, and push to the PR branch)."
    )
    return 0


def propose_heals(
    heals: list[ProposedHeal],
    *,
    remote: str = "origin",
    push: bool = False,
    base: str = "main",
    dry_run: bool = False,
) -> int:
    """Default mode: route heals through a NEW branch + PR. Never mutate `main`."""
    if not heals:
        print("No drifted addresses - nothing to propose.")
        return 0

    pre_existing = wiki_changes()
    if pre_existing and not dry_run:
        print("Refusing to propose - wiki/ has uncommitted changes:")
        for c in pre_existing:
            print(f"  - {c}")
        print("Commit or stash them first, then re-run the healer.")
        return 1

    import subprocess

    original_branch = current_branch()
    original_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    branch = f"heal/addresses-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"

    print(f"Proposed heals: {len(heals)}")
    for h in heals:
        print(
            f"  - {h.page.slug}: [{h.trigger}] "
            f"{h.old_address.hint!r} -> {h.new_address.hint!r}"
        )

    if dry_run:
        tail = ", push, open PR" if push else " (local only)"
        print(f"\n[dry-run] Would branch {branch}, apply, lint, commit{tail}.")
        return 0

    if original_branch in PROTECTED_BRANCHES:
        print(
            f"On protected branch '{original_branch}': branching off it (never committed here)."
        )

    git("checkout", "-b", branch)
    committed = False
    result = 1
    try:
        applied = 0
        for h in heals:
            if apply_heal_edit(h.page, h.old_address, h.new_address):
                append_heal_to_log(
                    h.page.slug, h.old_address.hint, h.new_address.hint, h.trigger
                )
                applied += 1
        if applied == 0:
            raise RuntimeError("no hint line matched any page - nothing to apply")

        print("\n[lint] python scripts/gen_index.py --check ...")
        if not run_lint():
            raise RuntimeError("lint failed - a broken heal never becomes a PR (R-6.5)")

        git("add", "--", "wiki")
        git(
            "commit",
            "-m",
            f"heal: propose {applied} address re-mint(s) - review before merge",
            "--no-verify",
        )
        committed = True
        print(f"committed {applied} heal(s) to {branch}")

        if push:
            git("push", "-u", remote, branch)
            print(f"pushed {remote}/{branch}")
            print("\nOpen the PR (do NOT auto-merge - R-6.4):")
            print(f"  gh pr create --base {base} --head {branch} --fill")
        else:
            print("\nLocal branch only. To propose:")
            print(
                f"  git push -u {remote} {branch} && "
                f"gh pr create --base {base} --head {branch} --fill"
            )
        result = 0
    except Exception as exc:  # noqa: BLE001 - any failure must leave main pristine
        if committed:
            print(
                f"\nCommit is on local branch {branch}, but a later step failed "
                f"({exc}). Push it manually if you want the PR."
            )
        else:
            print(f"\nABORTED - main left pristine: {exc}")
    finally:
        if not committed:
            git("checkout", "--", "wiki", check=False)
        if current_branch() != original_branch:
            if original_branch == "HEAD":
                git("checkout", original_commit, check=False)
            else:
                git("checkout", original_branch, check=False)
        if not committed:
            git("branch", "-D", branch, check=False)

    return result


def _build_address_to_page(pages) -> dict[Address, vault.Page]:
    mapping: dict[Address, vault.Page] = {}
    for page in pages:
        for src in page.sources:
            if isinstance(src, dict) and src.get("path") and src.get("hint"):
                loc = str(src["loc"]) if src.get("loc") else None
                addr = Address(path=str(src["path"]), hint=str(src["hint"]), loc=loc)
                mapping[addr] = page
    return mapping


async def verify_and_heal_vault(
    backend: RagBackend,
    *,
    ci_mode: bool = False,
    remote: str = "origin",
    push: bool = False,
    base: str = "main",
    dry_run: bool = False,
) -> int:
    """Verify all addresses and PROPOSE heals for DRIFT/FAIL (never auto-apply to main).

    ci_mode=False -> open a new heal branch + PR (scheduled/local).
    ci_mode=True  -> apply to the current PR branch (Gitea Actions commits + pushes).
    """
    pages = list(vault.load_pages())
    addresses = _collect_addresses(pages)
    if not addresses:
        return 0

    reports = await verify_all(backend, addresses)
    address_to_page = _build_address_to_page(pages)

    heals: list[ProposedHeal] = []
    for report in reports:
        if report.status not in (VerifyStatus.DRIFT, VerifyStatus.FAIL):
            continue
        found_addr = next(
            (a for a in addresses if a.path == report.path and a.hint == report.hint),
            None,
        )
        if not found_addr:
            continue
        page = address_to_page.get(found_addr)
        if not page:
            continue

        summary = str(page.frontmatter.get("summary", ""))
        candidates = [report.hint]
        if summary:
            candidates.append(summary[:50])
            candidates.append(summary)

        mint_result = await mint_address(
            backend, report.path, candidates, loc=found_addr.loc
        )
        if mint_result.status == MintStatus.MINTED and mint_result.address:
            heals.append(
                ProposedHeal(
                    page=page,
                    old_address=found_addr,
                    new_address=mint_result.address,
                    trigger=report.status.name,
                )
            )

    if ci_mode:
        return apply_heals_in_place(heals, dry_run=dry_run)
    return propose_heals(heals, remote=remote, push=push, base=base, dry_run=dry_run)


def _build_backend() -> RagBackend:
    """Construct the RAG backend for standalone/CI runs.

    Defaults to V2 PostgreSQL + pgvector (PgVectorRlsBackend).
    """
    import os

    kind = os.environ.get("HEALER_BACKEND", "pgvector").lower()
    if kind == "pgvector":
        from scout.backends.pgvector import PgVectorRlsBackend

        return PgVectorRlsBackend()
    if kind == "rag_anything_http":
        from scout.backends.rag_anything_http import RagAnythingHttpBackend

        return RagAnythingHttpBackend(
            base_url=os.environ.get("RAG_HTTP_URL", "http://localhost:8000")
        )
    if kind == "fake":
        from scout.backends.fake import FakeRagBackend

        return FakeRagBackend(chunks=[])
    raise SystemExit(f"unknown HEALER_BACKEND={kind!r}")


if __name__ == "__main__":
    import argparse
    import asyncio

    ap = argparse.ArgumentParser(description="Address drift healer (PR-first).")
    ap.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: apply heals to the current PR branch (no new branch/commit; "
        "the workflow commits + pushes, a human reviews).",
    )
    ap.add_argument(
        "--push",
        action="store_true",
        help="Default mode only: push the heal branch and print the PR command.",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Show proposed heals; write nothing."
    )
    ap.add_argument("--base", default="main", help="PR base branch (default mode).")
    args = ap.parse_args()

    backend = _build_backend()
    raise SystemExit(
        asyncio.run(
            verify_and_heal_vault(
                backend,
                ci_mode=args.ci,
                push=args.push,
                base=args.base,
                dry_run=args.dry_run,
            )
        )
    )
