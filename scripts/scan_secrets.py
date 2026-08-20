#!/usr/bin/env python3
"""Scan working bytes, staged blobs, untracked files, and all-ref Git history.

Diagnostics deliberately contain no fragment of a matched value. Ignored files are
outside the current-state scan because they are intended for local secret storage;
reachable Git objects are always scanned regardless of current ignore rules.

Placeholder policy: exemptions are granted by VALUE, never by path. A match is
skipped only when the entire matched value is one of the documented placeholders in
``PLACEHOLDER_VALUES``; that exemption then applies at every path and in every scan
mode, history-only blobs included. Everything else fails the scan, including any near
miss of a placeholder pattern. See the comment above ``PLACEHOLDER_VALUES``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024
CAT_FILE_BATCH_SIZE = 128

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Anthropic API token", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI / LiteLLM token", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b")),
    ("GitHub personal access token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("GitLab personal access token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Slack API token", re.compile(r"\bxox[baprs]-[A-Za-z0-9_-]{20,}\b")),
)

# Placeholder policy, chosen 2026-08-19 (audit finding M6). An exemption needs a
# visibly fake value and nothing else; the path is irrelevant. The previous rule also
# demanded an approved path (".env.example" / "docker-compose.yml"), which left this
# gate permanently red: the project's own "sk-local-dev-" placeholder is committed in
# an artifact and in test fixtures, and further copies survive only inside reachable
# history blobs that cannot be edited away without rewriting published history --
# which this project does not do.
#
# Narrowness is what stops a value-scoped exemption from hollowing out the gate:
#   * matching is fullmatch, so a value that merely starts with a placeholder prefix
#     and then carries token-shaped material is still reported;
#   * no issuer mints credentials under these prefixes;
#   * the suffix alphabet admits neither the upper-case letters nor the underscores
#     that real OpenAI / Anthropic / LiteLLM credentials contain.
# Accepted limit: someone who deliberately reshapes a live credential into one of
# these exact forms defeats the rule, exactly as encoding it would defeat any regex
# scanner. This gate exists to stop accidental commits, and every other token shape
# remains a hard failure. Keep these patterns in step with `.gitleaks.toml`.
PLACEHOLDER_VALUES = (
    re.compile(r"^sk-local-dev-[a-z0-9-]+$"),
    re.compile(r"^sk-placeholder-[a-z0-9-]+$"),
)


@dataclass(frozen=True, slots=True)
class Finding:
    """A redaction-safe secret finding."""

    source: str
    path: str
    label: str
    line: int | None
    object_id: str | None = None

    def format(self) -> str:
        object_text = f" object={self.object_id[:12]}" if self.object_id else ""
        location = f"{self.path}:{self.line}" if self.line is not None else self.path
        return (
            f"[{self.source}] {location}{object_text}: "
            f"{self.label} [REDACTED]"
        )


def _incomplete(
    *, source: str, path: str, reason: str, object_id: str | None = None
) -> Finding:
    """Return a static, redaction-safe diagnostic for incomplete coverage."""
    return Finding(
        source=source,
        path=path,
        label=f"scan incomplete: {reason}",
        line=None,
        object_id=object_id,
    )


def _is_placeholder(value: str) -> bool:
    """Return True when the whole matched value is a documented placeholder."""
    return any(pattern.fullmatch(value) for pattern in PLACEHOLDER_VALUES)


def find_secrets(
    content: str,
    path: str,
    *,
    source: str = "CONTENT",
    object_id: str | None = None,
) -> list[Finding]:
    """Return redaction-safe findings for text content."""
    findings: list[Finding] = []
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            if _is_placeholder(match.group(0)):
                continue
            findings.append(
                Finding(
                    source=source,
                    path=path,
                    label=label,
                    line=content.count("\n", 0, match.start()) + 1,
                    object_id=object_id,
                )
            )
    return findings


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _listed_paths(repo: Path, *args: str) -> list[str]:
    output = _git(repo, "ls-files", "-z", *args)
    return [item.decode("utf-8", errors="surrogateescape") for item in output.split(b"\0") if item]


def _scan_bytes(
    data: bytes,
    *,
    source: str,
    path: str,
    object_id: str | None = None,
) -> list[Finding]:
    if len(data) > MAX_FILE_BYTES:
        return [
            _incomplete(
                source=source,
                path=path,
                reason="target exceeds the configured size limit",
                object_id=object_id,
            )
        ]
    return find_secrets(
        data.decode("utf-8", errors="ignore"),
        path,
        source=source,
        object_id=object_id,
    )


def _safe_worktree_path(repo: Path, relative: str) -> Path | None:
    root = repo.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _scan_worktree_file(repo: Path, relative: str, *, source: str) -> list[Finding]:
    unresolved = repo / relative
    try:
        # A tracked path can be intentionally deleted in the current worktree.
        # There are no current bytes to inspect; the staged blob is covered by
        # ``scan_index``. Existing symlinks still go through containment checks.
        unresolved.lstat()
    except FileNotFoundError:
        return []
    except OSError:
        return [
            _incomplete(
                source=source,
                path=relative,
                reason="target is unreadable",
            )
        ]
    try:
        path = _safe_worktree_path(repo, relative)
    except (OSError, RuntimeError):
        path = None
    if path is None:
        return [
            _incomplete(
                source=source,
                path=relative,
                reason="target is unaddressable",
            )
        ]
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return [
                _incomplete(
                    source=source,
                    path=relative,
                    reason="target exceeds the configured size limit",
                )
            ]
        data = path.read_bytes()
    except OSError:
        return [
            _incomplete(
                source=source,
                path=relative,
                reason="target is unreadable",
            )
        ]
    return _scan_bytes(data, source=source, path=relative)


def scan_worktree(repo: Path = REPO_ROOT) -> list[Finding]:
    """Scan tracked files using their current filesystem bytes."""
    findings: list[Finding] = []
    for relative in _listed_paths(repo, "--cached"):
        findings.extend(_scan_worktree_file(repo, relative, source="WORKTREE"))
    return findings


def scan_index(repo: Path = REPO_ROOT) -> list[Finding]:
    """Scan staged index blobs independently from working-tree bytes."""
    findings: list[Finding] = []
    for relative in _listed_paths(repo, "--cached"):
        size_result = subprocess.run(
            ["git", "cat-file", "-s", f":{relative}"],
            cwd=repo,
            capture_output=True,
            check=False,
            text=True,
        )
        if size_result.returncode != 0:
            findings.append(
                _incomplete(
                    source="INDEX",
                    path=relative,
                    reason="index blob is unaddressable",
                )
            )
            continue
        try:
            if int(size_result.stdout.strip()) > MAX_FILE_BYTES:
                findings.append(
                    _incomplete(
                        source="INDEX",
                        path=relative,
                        reason="target exceeds the configured size limit",
                    )
                )
                continue
        except ValueError:
            findings.append(
                _incomplete(
                    source="INDEX",
                    path=relative,
                    reason="index blob size is unreadable",
                )
            )
            continue
        result = subprocess.run(
            ["git", "cat-file", "blob", f":{relative}"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            findings.extend(
                _scan_bytes(result.stdout, source="INDEX", path=relative)
            )
        else:
            findings.append(
                _incomplete(
                    source="INDEX",
                    path=relative,
                    reason="index blob is unreadable",
                )
            )
    return findings


def scan_untracked(repo: Path = REPO_ROOT) -> list[Finding]:
    """Scan non-ignored untracked files."""
    findings: list[Finding] = []
    for relative in _listed_paths(repo, "--others", "--exclude-standard"):
        findings.extend(_scan_worktree_file(repo, relative, source="UNTRACKED"))
    return findings


def scan_all_current(repo: Path = REPO_ROOT) -> list[Finding]:
    """Scan tracked worktree bytes, staged index blobs, and untracked files."""
    return scan_worktree(repo) + scan_index(repo) + scan_untracked(repo)


def _reachable_objects(repo: Path) -> tuple[list[str], dict[str, set[str]]]:
    output = _git(repo, "rev-list", "--objects", "--all")
    object_ids: list[str] = []
    paths: dict[str, set[str]] = {}
    for raw_line in output.splitlines():
        raw_oid, separator, raw_path = raw_line.partition(b" ")
        oid = raw_oid.decode("ascii")
        object_ids.append(oid)
        if separator:
            paths.setdefault(oid, set()).add(
                raw_path.decode("utf-8", errors="surrogateescape")
            )

    # `rev-list --objects` names each unique object only once, under a single
    # arbitrary path. Enumerate every reachable tree so that a flagged blob is
    # reported at every path it has ever occupied, which is what makes a history
    # finding actionable.
    commit_ids = _git(repo, "rev-list", "--all").splitlines()
    for raw_commit in commit_ids:
        tree = _git(repo, "ls-tree", "-r", "-z", "--full-tree", raw_commit.decode("ascii"))
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, separator, raw_path = entry.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3 or fields[1] != b"blob":
                continue
            oid = fields[2].decode("ascii")
            paths.setdefault(oid, set()).add(
                raw_path.decode("utf-8", errors="surrogateescape")
            )
    return list(dict.fromkeys(object_ids)), paths


def _eligible_blob_ids(repo: Path, object_ids: list[str]) -> tuple[list[str], list[str]]:
    if not object_ids:
        return [], []
    output = _git(
        repo,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_bytes=("\n".join(object_ids) + "\n").encode("ascii"),
    )
    eligible: list[str] = []
    oversized: list[str] = []
    for line in output.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise RuntimeError("malformed git cat-file metadata response")
        oid, kind, raw_size = fields
        if kind != "blob":
            continue
        if int(raw_size) <= MAX_FILE_BYTES:
            eligible.append(oid)
        else:
            oversized.append(oid)
    return eligible, oversized


def _batched_blobs(repo: Path, object_ids: list[str]) -> list[tuple[str, bytes]]:
    blobs: list[tuple[str, bytes]] = []
    for offset in range(0, len(object_ids), CAT_FILE_BATCH_SIZE):
        batch = object_ids[offset : offset + CAT_FILE_BATCH_SIZE]
        output = _git(
            repo,
            "cat-file",
            "--batch",
            input_bytes=("\n".join(batch) + "\n").encode("ascii"),
        )
        cursor = 0
        while cursor < len(output):
            header_end = output.find(b"\n", cursor)
            if header_end < 0:
                raise RuntimeError("malformed git cat-file batch response")
            header = output[cursor:header_end].decode("ascii")
            oid, kind, raw_size = header.split()
            if kind != "blob":
                raise RuntimeError("git cat-file returned a non-blob object")
            size = int(raw_size)
            content_start = header_end + 1
            content_end = content_start + size
            blobs.append((oid, output[content_start:content_end]))
            cursor = content_end + 1
    return blobs


def scan_git_history(repo: Path = REPO_ROOT) -> list[Finding]:
    """Scan each unique blob reachable from every local branch and tag."""
    object_ids, paths = _reachable_objects(repo)
    blob_ids, oversized_blob_ids = _eligible_blob_ids(repo, object_ids)
    findings: list[Finding] = []
    for oid in oversized_blob_ids:
        for path in sorted(paths.get(oid) or {"<path-unavailable>"}):
            findings.append(
                _incomplete(
                    source="HISTORY",
                    path=path,
                    reason="target exceeds the configured size limit",
                    object_id=oid,
                )
            )
    for oid, content in _batched_blobs(repo, blob_ids):
        object_paths = paths.get(oid) or {"<path-unavailable>"}
        for path in sorted(object_paths):
            findings.extend(
                _scan_bytes(
                    content,
                    source="HISTORY",
                    path=path,
                    object_id=oid,
                )
            )
    return findings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--worktree", action="store_true")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--untracked", action="store_true")
    parser.add_argument("--all-current", action="store_true")
    parser.add_argument("--history", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo = args.repo.resolve()
    findings: list[Finding] = []
    current_mode_selected = args.worktree or args.index or args.untracked

    try:
        if args.all_current or not current_mode_selected:
            findings.extend(scan_all_current(repo))
        else:
            if args.worktree:
                findings.extend(scan_worktree(repo))
            if args.index:
                findings.extend(scan_index(repo))
            if args.untracked:
                findings.extend(scan_untracked(repo))
        if args.history:
            findings.extend(scan_git_history(repo))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, UnicodeError):
        print(
            "Secret scan failed; repository coverage was incomplete [REDACTED].",
            file=sys.stderr,
        )
        return 1

    if findings:
        print("Secret scan failed; matched values are fully redacted.", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding.format()}", file=sys.stderr)
        return 1

    print("Secret scan passed: no prohibited values found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
