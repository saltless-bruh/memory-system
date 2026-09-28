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


def git(
    *args: str, check: bool = True, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        input=stdin,
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


def _url(raw: str) -> str:
    return raw.strip().rstrip("/")


def _push_urls(remote: str) -> set[str]:
    """Every URL a push to the named remote reaches; empty if it is not one.

    A remote may carry several push URLs and `git push` sends to all of them,
    so judging only the first would let a second, public one ride along.
    """
    listed = git("remote", "get-url", "--push", "--all", remote, check=False)
    if listed.returncode != 0:
        return set()
    return {_url(line) for line in listed.stdout.splitlines() if line.strip()}


def _private_urls() -> set[str]:
    """The URLs known to reach the private remote's repository.

    Its fetch URL is where the vault is read from, so it is private by
    construction. A single push URL is the same repository by another route
    (ssh beside https, say). Several push URLs mean the name fans out, and
    nothing here can tell which of them is private, so none is trusted.
    """
    fetched = git("remote", "get-url", PRIVATE_REMOTE, check=False)
    if fetched.returncode != 0:
        return set()
    pushed = _push_urls(PRIVATE_REMOTE)
    return {_url(fetched.stdout)} | (pushed if len(pushed) == 1 else set())


def _ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def _owning_remote(target: str) -> str | None:
    """The configured remote whose refs record what `target` already holds.

    `git push` takes a URL as readily as a name, and origin's own URL is still
    origin: what it has published is recorded under `origin/*`, and that record
    is what the guard must compare against. A URL that no configured remote
    fetches from or pushes to has no such record, and None says so.
    """
    names = git("remote", check=False).stdout.split()
    if target in names:
        return target
    url = _url(target)
    owners = [
        name
        for name in names
        if url in _push_urls(name)
        or url == _url(git("remote", "get-url", name, check=False).stdout)
    ]
    # Only an owner whose `main` has been fetched can say what is published,
    # so it is preferred; otherwise any owner is as uninformative as another.
    for name in owners:
        if _ref_exists(f"refs/remotes/{name}/main"):
            return name
    return owners[0] if owners else None


def _wiki_trees(commits: Sequence[str]) -> list[str | None]:
    """Each commit's wiki/ tree id, in order, or None where it has no wiki/."""
    answered = git(
        "cat-file",
        "--batch-check=%(objectname)",
        stdin="".join(f"{commit}:wiki\n" for commit in commits),
    )
    return [
        None if line.endswith(" missing") else line.strip()
        for line in answered.stdout.splitlines()
    ]


def public_push_refusal(remote: str, ref: str) -> str | None:
    """Why pushing `ref` to `remote` could publish the vault, or None if it cannot.

    A push is safe when it reaches only the private remote, whatever it
    carries. Anywhere else the question is not what `ref`'s tip holds but what
    the push sends, and `git push` sends every commit reachable from `ref` that
    the remote lacks. A branch that once added the vault and later deleted it
    has a clean tip and the whole vault in its history.

    So when the remote's `main` is known here, every commit reachable from
    `ref` and absent from that remote's tracked refs must carry exactly the
    wiki/ tree its `main` already publishes; a commit that differs would add
    pages the remote does not have. When nothing is known about the remote,
    nothing counts as published, and the only safe bound is that the whole
    reachable history never names more wiki/ files than the public sample.
    Every failure to answer refuses: an unknown tree is not a known-public one.
    Remote-tracking refs may be stale; that errs toward refusing, since a
    commit the remote gained later is judged as not yet sent.
    """
    # Compared by URL, not name, so a second name for the private remote is
    # still private. A URL is pushed to exactly as given, so it is judged by
    # itself, never by the push URLs of whichever remote happens to own it.
    private_urls = _private_urls()
    target_urls = _push_urls(remote) or {_url(remote)}
    if private_urls and target_urls <= private_urls:
        return None

    if not _ref_exists(f"{ref}^{{commit}}"):
        return f"cannot read {ref}; refusing to push to {remote}"
    # A remote's tracking refs record one repository. When the push fans out
    # to several URLs, they say nothing about the others, so none is trusted.
    owner = _owning_remote(remote) if len(target_urls) == 1 else None
    if owner is not None and _ref_exists(f"refs/remotes/{owner}/main"):
        unsent = git("rev-list", ref, "--not", f"--remotes={owner}", check=False)
        if unsent.returncode != 0:
            return f"cannot list what {ref} would send; refusing to push to {remote}"
        commits = unsent.stdout.split()
        published, *sent = _wiki_trees([f"refs/remotes/{owner}/main", *commits])
        differing = sum(1 for tree in sent if tree != published)
        if len(sent) != len(commits) or differing:
            return (
                f"{differing or 'some'} commit(s) reachable from {ref} and not yet "
                f"on {owner} carry a wiki/ tree that differs from "
                f"{owner}/main:wiki; pushing them to {remote} would publish vault "
                f"pages. Push to {PRIVATE_REMOTE} instead."
            )
        return None

    # Renames count as a delete and an add, and merges are diffed against
    # every parent, so no file that ever existed on the way escapes the count.
    history = git(
        "log",
        ref,
        "-m",
        "--no-renames",
        "--format=",
        "--name-only",
        "--",
        "wiki/",
        check=False,
    )
    if history.returncode != 0:
        return f"cannot read the history of {ref}; refusing to push to {remote}"
    named = {line for line in history.stdout.splitlines() if line.strip()}
    if len(named) > PUBLIC_SAMPLE_WIKI_FILES:
        return (
            f"the history reachable from {ref} names {len(named)} wiki/ files, "
            f"more than the {PUBLIC_SAMPLE_WIKI_FILES}-file public sample, and "
            f"nothing on {remote} is known here to be published already; push "
            f"to {PRIVATE_REMOTE} instead."
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
