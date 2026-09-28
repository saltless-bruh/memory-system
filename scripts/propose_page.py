#!/usr/bin/env python3
"""Create a PR-first commit containing one named page and generated companions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_BRANCHES = {"main", "master"}
GENERATED_COMPANIONS = ("wiki/index.md", "wiki/log.md")

# `origin` is public GitHub and carries the system plus a twelve-page sample;
# the real vault lives only on the private remote. A proposal is a vault page,
# so its natural destination is the private remote, and it is the default.
PRIVATE_REMOTE = "gitea"
PUBLIC_SAMPLE_WIKI_FILES = 12


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _normalize_page(raw_page: str) -> str:
    supplied = Path(raw_page)
    if supplied.is_absolute() or not supplied.parts or supplied.parts[0] != "wiki":
        raise ValueError("--page must be a repository-relative path beneath wiki/")
    root = REPO_ROOT.resolve(strict=False)
    wiki = (root / "wiki").resolve(strict=False)
    resolved = (root / supplied).resolve(strict=False)
    try:
        resolved.relative_to(wiki)
    except ValueError as exc:
        raise ValueError("--page must resolve beneath wiki/") from exc
    if resolved.suffix.lower() != ".md" or resolved.name in {"index.md", "log.md"}:
        raise ValueError("--page must name a content Markdown page")
    if not resolved.is_file():
        raise ValueError("--page does not exist")
    return resolved.relative_to(root).as_posix()


def _porcelain_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def wiki_changes(paths: Sequence[str] | None = None) -> list[str]:
    """Return changed wiki paths, optionally restricted to an exact path set."""
    pathspec = tuple(paths) if paths is not None else ("wiki",)
    output = git(
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *pathspec,
    ).stdout
    return _porcelain_paths(output)


def _staged_paths() -> set[str]:
    output = git("diff", "--cached", "--name-only").stdout
    return {line.strip() for line in output.splitlines() if line.strip()}


def slugify(title: str) -> str:
    keep = "".join(
        character if character.isalnum() else "-" for character in title.lower()
    )
    return "-".join(filter(None, keep.split("-")))[:40] or "page"


def run_lint() -> bool:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_index.py"), "--check"],
        cwd=REPO_ROOT,
        check=False,
        timeout=60,
    )
    return result.returncode == 0


def run_verify() -> bool:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_addresses.py")],
        cwd=REPO_ROOT,
        check=False,
        timeout=300,
    )
    return result.returncode == 0


def _push_url(remote: str) -> str | None:
    """The URL a push to the named remote reaches, or None if it is not one."""
    resolved = git("remote", "get-url", "--push", remote, check=False)
    if resolved.returncode != 0:
        return None
    return resolved.stdout.strip().rstrip("/") or None


def public_push_refusal(remote: str, ref: str) -> str | None:
    """Why pushing `ref` to `remote` could publish the vault, or None if it cannot.

    A push is safe when it reaches the private remote, whatever it carries.
    Anywhere else, `ref`'s wiki/ tree must be byte-identical to what that
    remote's `main` already holds, so the push adds nothing it has not already
    published. When that remote's `main` has never been fetched there is
    nothing to compare against, and the only safe bound is the size of the
    public sample. Every failure to answer refuses: an unknown tree is not a
    known-public one. The remote-tracking ref may be stale; that errs toward
    refusing, since a stale public tree differs from anything newly proposed.
    """
    # Compared by URL, not name, so a second name for the private remote is
    # still private and a bare URL (git push accepts one) is judged by itself.
    private_url = _push_url(PRIVATE_REMOTE)
    target_url = _push_url(remote) or remote.rstrip("/")
    if private_url is not None and target_url == private_url:
        return None

    tree = git("rev-parse", "--verify", "--quiet", f"{ref}:wiki", check=False)
    if tree.returncode != 0:
        return f"cannot read the wiki/ tree of {ref}; refusing to push to {remote}"
    published = git(
        "rev-parse", "--verify", "--quiet", f"{remote}/main:wiki", check=False
    )
    if published.returncode == 0:
        if published.stdout.strip() != tree.stdout.strip():
            return (
                f"{ref}'s wiki/ tree differs from {remote}/main:wiki; pushing it "
                f"to {remote} would publish vault pages. Push to "
                f"{PRIVATE_REMOTE} instead."
            )
        return None
    listed = git("ls-tree", "-r", "--name-only", ref, "--", "wiki/", check=False)
    if listed.returncode != 0:
        return f"cannot list wiki/ in {ref}; refusing to push to {remote}"
    count = len([line for line in listed.stdout.splitlines() if line.strip()])
    if count > PUBLIC_SAMPLE_WIKI_FILES:
        return (
            f"{ref} carries {count} wiki/ files, more than the "
            f"{PUBLIC_SAMPLE_WIKI_FILES}-file public sample, and {remote}/main is "
            f"unknown here; push to {PRIVATE_REMOTE} instead."
        )
    return None


def _rollback_created_branch(
    *, base_branch: str, proposal_branch: str, selected: Sequence[str]
) -> bool:
    """Restore selected changes to the original branch after a local failure."""
    reset = git("reset", "--mixed", "HEAD", "--", *selected, check=False)
    if reset.returncode != 0:
        return False
    switched = git("switch", base_branch, check=False)
    if switched.returncode != 0:
        return False
    deleted = git("branch", "-D", proposal_branch, check=False)
    return deleted.returncode == 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True, help="exact wiki page to propose")
    parser.add_argument("--title", default="", help="page title used in branch/commit")
    parser.add_argument("--remote", default=PRIVATE_REMOTE)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--base", default="main", help="PR target branch")
    args = parser.parse_args(argv)

    try:
        page = _normalize_page(args.page)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    allowed = (page, *GENERATED_COMPANIONS)
    changes = wiki_changes(allowed)
    if page not in changes:
        print(f"Named page has no working-tree change: {page}")
        return 1
    selected = tuple(path for path in allowed if path in changes)
    staged = _staged_paths()
    if staged:
        print(
            "Refusing to propose while staged paths exist: " + ", ".join(sorted(staged))
        )
        return 1

    # A base that already carries the vault can never go to a public remote, so
    # refuse before cutting anything and leave the caller where they stood.
    if args.push:
        refusal = public_push_refusal(args.remote, args.base)
        if refusal:
            print(f"PUSH REFUSED — {refusal} Nothing was branched or pushed.")
            return 1

    base_now = current_branch()
    branch = (
        f"wiki/{slugify(args.title or Path(page).stem)}-"
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    )
    print(f"Base branch:  {args.base}")
    print(f"New branch:   {branch}")
    print("Proposal paths:")
    for changed in selected:
        print(f"  - {changed}")

    if args.dry_run:
        print(
            "[dry-run] Would lint, verify, create a branch, and commit only these paths."
        )
        return 0

    if not run_lint():
        print("LINT FAILED — working tree and branch are unchanged.")
        return 1
    if not run_verify():
        print("ADDRESS VERIFICATION FAILED — working tree and branch are unchanged.")
        return 1

    branch_created = False
    try:
        # Cut from the PR target, not from wherever the caller stands: a branch
        # cut from HEAD carries every unrelated commit on it into the review,
        # and on a vault-bearing branch that is the whole private vault.
        git("checkout", "-b", branch, args.base)
        branch_created = True
        git("add", "--", *selected)
        git(
            "commit",
            "--only",
            "-m",
            f"wiki: propose {args.title or Path(page).stem}",
            "--no-verify",
            "--",
            *selected,
        )
    except (OSError, subprocess.SubprocessError):
        if branch_created:
            restored = _rollback_created_branch(
                base_branch=base_now,
                proposal_branch=branch,
                selected=selected,
            )
            if restored:
                print(
                    "PROPOSAL FAILED — restored the original branch and unstaged changes."
                )
            else:
                print(
                    "PROPOSAL FAILED — automatic recovery was incomplete; "
                    f"inspect local branch {branch}."
                )
        else:
            print("PROPOSAL FAILED — branch creation did not complete.")
        return 1
    print(f"Committed exact proposal scope to {branch}")

    # The commit itself changed wiki/, so the branch is judged again as it now
    # stands. A refusal keeps the verified commit for a push to the right place.
    refusal = public_push_refusal(args.remote, branch)
    if args.push and refusal:
        print(
            f"PUSH REFUSED — {refusal} Nothing was pushed; the verified local "
            f"commit is kept on {branch}."
        )
        return 1
    if args.push:
        try:
            git("push", "-u", args.remote, branch)
        except (OSError, subprocess.SubprocessError):
            print(
                "PUSH FAILED OR WAS AMBIGUOUS — preserved the verified local "
                f"commit on {branch}; inspect the remote, then retry explicitly."
            )
            return 1
        print("Open a PR for human review; do not auto-merge:")
        print(f"  gh pr create --base {args.base} --head {branch} --fill")
    elif refusal:
        print(f"Do not push this branch to {args.remote}: {refusal}")
        print(f"Push with: git push -u {PRIVATE_REMOTE} {branch}")
        print(f"Then open a PR against {args.base}; a human reviews and merges.")
    else:
        print(f"Push with: git push -u {args.remote} {branch}")
        print(f"Then open a PR against {args.base}; a human reviews and merges.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
