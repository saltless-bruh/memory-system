#!/usr/bin/env python3
"""Materialize bounded wiki Markdown from an untrusted PR Git checkout.

This module is executed only from an immutable trusted-base checkout. The PR
checkout is treated as an object database: no Python, shell, workflow, build
metadata, hooks, or other files from it are executed or imported.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_CHANGED_FILES = 100
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40,64}")


class MaterializationError(RuntimeError):
    """A redacted, fail-closed PR materialization error."""


@dataclass(frozen=True, slots=True)
class _WikiOperation:
    path: str
    content: bytes | None


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise MaterializationError("untrusted PR Git data could not be validated")
    return result.stdout


def _validate_sha(value: str) -> str:
    if _SHA_PATTERN.fullmatch(value) is None:
        raise MaterializationError("PR commit identity is malformed")
    return value.lower()


def _validate_wiki_path(raw_path: str) -> str:
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or path.parts[0] != "wiki"
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".md"
    ):
        raise MaterializationError("PR changes must contain only wiki Markdown")
    return path.as_posix()


def _changed_paths(repo: Path, base_sha: str, head_sha: str) -> tuple[str, ...]:
    for commit in (base_sha, head_sha):
        _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, head_sha],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise MaterializationError("PR head must contain the immutable base commit")

    raw = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_sha,
        head_sha,
        "--",
    )
    try:
        decoded = [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise MaterializationError("PR paths must be UTF-8") from exc
    if len(decoded) > MAX_CHANGED_FILES:
        raise MaterializationError("PR changes too many files")
    return tuple(_validate_wiki_path(path) for path in decoded)


def _head_blob(repo: Path, head_sha: str, path: str) -> bytes | None:
    raw_entry = _git(repo, "ls-tree", "-z", head_sha, "--", path)
    if not raw_entry:
        return None
    entries = [entry for entry in raw_entry.split(b"\0") if entry]
    if len(entries) != 1:
        raise MaterializationError("PR tree entry is ambiguous")
    metadata, separator, raw_returned_path = entries[0].partition(b"\t")
    fields = metadata.split()
    try:
        returned_path = raw_returned_path.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterializationError("PR paths must be UTF-8") from exc
    if (
        not separator
        or len(fields) != 3
        or fields[0] != b"100644"
        or fields[1] != b"blob"
        or returned_path != path
    ):
        raise MaterializationError("wiki Markdown must be a non-executable regular blob")
    oid = fields[2].decode("ascii")
    try:
        blob_size = int(_git(repo, "cat-file", "-s", oid).strip())
    except (UnicodeError, ValueError) as exc:
        raise MaterializationError("wiki Markdown size could not be validated") from exc
    if blob_size > MAX_MARKDOWN_BYTES:
        raise MaterializationError("wiki Markdown exceeds the size limit")
    content = _git(repo, "cat-file", "blob", oid)
    if len(content) != blob_size:
        raise MaterializationError("wiki Markdown blob size changed during validation")
    if len(content) > MAX_MARKDOWN_BYTES:
        raise MaterializationError("wiki Markdown exceeds the size limit")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterializationError("wiki Markdown must be UTF-8") from exc
    if b"\0" in content:
        raise MaterializationError("wiki Markdown must be text")
    return content


def _destination_path(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    if not candidate.resolve(strict=False).is_relative_to(root.resolve()):
        raise MaterializationError("wiki destination escapes the trusted checkout")
    return candidate


def materialize_pr_wiki(
    source_repo: Path,
    destination_repo: Path,
    *,
    base_sha: str,
    head_sha: str,
) -> tuple[str, ...]:
    """Apply a wiki-only PR diff to a trusted base checkout after full validation."""
    source = source_repo.resolve()
    destination = destination_repo.resolve()
    base = _validate_sha(base_sha)
    head = _validate_sha(head_sha)
    changed = _changed_paths(source, base, head)

    operations = tuple(
        _WikiOperation(path, _head_blob(source, head, path)) for path in changed
    )
    if sum(len(operation.content or b"") for operation in operations) > MAX_TOTAL_BYTES:
        raise MaterializationError("PR wiki payload exceeds the total size limit")

    # No destination byte changes occur until every path, mode, and blob passes.
    for operation in operations:
        target = _destination_path(destination, operation.path)
        if operation.content is None:
            if target.exists() or target.is_symlink():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        target.write_bytes(operation.content)
    return changed


def _wiki_files(repo: Path) -> dict[str, bytes]:
    wiki = repo / "wiki"
    if not wiki.is_dir() or wiki.is_symlink():
        raise MaterializationError("trusted wiki directory is unavailable")
    files: dict[str, bytes] = {}
    total = 0
    for path in wiki.rglob("*"):
        if path.is_symlink():
            raise MaterializationError("wiki trees must not contain symlinks")
        if path.is_dir() or path.suffix != ".md":
            continue
        if not path.is_file():
            raise MaterializationError("wiki Markdown must be a regular file")
        content = path.read_bytes()
        if len(content) > MAX_MARKDOWN_BYTES:
            raise MaterializationError("wiki Markdown exceeds the size limit")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise MaterializationError("wiki payload exceeds the total size limit")
        relative = path.relative_to(repo).as_posix()
        files[_validate_wiki_path(relative)] = content
    return files


def export_wiki_markdown(source_repo: Path, destination_repo: Path) -> None:
    """Mirror trusted wiki Markdown into a PR checkout without touching code."""
    source = source_repo.resolve()
    destination = destination_repo.resolve()
    files = _wiki_files(source)
    destination_wiki = destination / "wiki"
    destination_wiki.mkdir(parents=True, exist_ok=True)

    for path in destination_wiki.rglob("*"):
        if path.is_symlink():
            raise MaterializationError("PR wiki tree contains a symlink")
    existing_markdown = {
        path.relative_to(destination).as_posix(): path
        for path in destination_wiki.rglob("*.md")
        if path.is_file()
    }
    for relative, path in existing_markdown.items():
        if relative not in files:
            path.unlink()
    for relative, content in files.items():
        target = _destination_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    import_parser = commands.add_parser("import")
    import_parser.add_argument("--source", type=Path, required=True)
    import_parser.add_argument("--destination", type=Path, required=True)
    import_parser.add_argument("--base-sha", required=True)
    import_parser.add_argument("--head-sha", required=True)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--source", type=Path, required=True)
    export_parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "import":
            changed = materialize_pr_wiki(
                args.source,
                args.destination,
                base_sha=args.base_sha,
                head_sha=args.head_sha,
            )
            print(f"Validated and materialized {len(changed)} wiki Markdown file(s).")
        else:
            export_wiki_markdown(args.source, args.destination)
            print("Exported trusted wiki Markdown to the PR checkout.")
    except (MaterializationError, OSError) as exc:
        print(f"PR wiki materialization failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
