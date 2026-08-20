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
    parser.add_argument("--remote", default="origin")
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
            "Refusing to propose while staged paths exist: "
            + ", ".join(sorted(staged))
        )
        return 1

    base_now = current_branch()
    branch = (
        f"wiki/{slugify(args.title or Path(page).stem)}-"
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    )
    print(f"Base branch:  {base_now}")
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
        git("checkout", "-b", branch)
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
                print("PROPOSAL FAILED — restored the original branch and unstaged changes.")
            else:
                print(
                    "PROPOSAL FAILED — automatic recovery was incomplete; "
                    f"inspect local branch {branch}."
                )
        else:
            print("PROPOSAL FAILED — branch creation did not complete.")
        return 1
    print(f"Committed exact proposal scope to {branch}")

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
    else:
        print(f"Push with: git push -u {args.remote} {branch}")
        print(f"Then open a PR against {args.base}; a human reviews and merges.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
