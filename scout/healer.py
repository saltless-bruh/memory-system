"""Scoped address-heal computation and transactional working-tree application."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout import vault
from scout.types import Address, RagBackend, ScopedAddress
from scripts.mint import MintStatus, mint_address
from scripts.propose_page import PROTECTED_BRANCHES, current_branch
from scripts.verify_addresses import (
    VerifyReport,
    VerifyStatus,
    _collect_addresses,
    verify_all,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = REPO_ROOT / "wiki" / "log.md"
UNRESOLVED_BRANCHES = frozenset({"", "HEAD", "unknown"})


@dataclass(frozen=True, slots=True)
class ProposedHeal:
    """One page/source-specific, verified replacement proposal."""

    page: vault.Page
    scoped_address: ScopedAddress
    new_address: Address
    trigger: str

    @property
    def old_address(self) -> Address:
        return self.scoped_address.address


_FENCE_RE = re.compile(r"^---\s*$")
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*):")
_SOURCES_KEY_RE = re.compile(r"^sources:")
_ITEM_START_RE = re.compile(r"^(\s*)-(?:\s|$)")
#: One mapping key inside a `sources[]` item. The optional ``- `` is part of
#: the captured prefix so a key written as the item's first line (``- hint:``)
#: round-trips with its dash intact. YAML does not require `path:` to come
#: first, so this matches any key at any position (m7).
_ITEM_KEY_RE = re.compile(r"^(\s*(?:-\s+)?)([A-Za-z_][\w.-]*):\s*(.*?)\s*$")


def _unquote(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        return stripped[1:-1]
    return stripped


def _frontmatter_span(lines: list[str]) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of the YAML frontmatter body, or ``None``.

    Scoping every later scan to this range is what stops a fenced YAML code
    block in the page *body* from shifting the `sources[]` index (m7) —
    AGENTS.md itself contains such a block.
    """
    if not lines or not _FENCE_RE.match(lines[0].rstrip("\n")):
        return None
    for index in range(1, len(lines)):
        if _FENCE_RE.match(lines[index].rstrip("\n")):
            return (1, index)
    return None


def _sources_span(lines: list[str], frontmatter: tuple[int, int]) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of the block nested under ``sources:``."""
    start, end = frontmatter
    for index in range(start, end):
        if not _SOURCES_KEY_RE.match(lines[index]):
            continue
        for following in range(index + 1, end):
            if _TOP_LEVEL_KEY_RE.match(lines[following]):
                return (index + 1, following)
        return (index + 1, end)
    return None


def _item_spans(lines: list[str], sources: tuple[int, int]) -> list[tuple[int, int]]:
    """Line range of each `sources[]` list item, in declaration order.

    Only dashes at the list's own indentation start a new item, so a nested
    sequence inside one entry cannot be mistaken for a sibling.
    """
    start, end = sources
    item_indent: int | None = None
    starts: list[int] = []
    for index in range(start, end):
        match = _ITEM_START_RE.match(lines[index])
        if match is None:
            continue
        indent = len(match.group(1))
        if item_indent is None:
            item_indent = indent
        if indent == item_indent:
            starts.append(index)
    return [
        (line, starts[position + 1] if position + 1 < len(starts) else end)
        for position, line in enumerate(starts)
    ]


def _item_key(
    lines: list[str], span: tuple[int, int], key: str
) -> tuple[int, str, str] | None:
    """Find `key` anywhere in one item; returns ``(line, prefix, raw value)``."""
    for index in range(*span):
        match = _ITEM_KEY_RE.match(lines[index].rstrip("\n"))
        if match is not None and match.group(2) == key:
            return (index, match.group(1), match.group(3))
    return None


def apply_heal_edit(
    page: vault.Page, scoped_address: ScopedAddress, new_address: Address
) -> bool:
    """Replace only the hint in the declared `sources[]` list item.

    Fails closed — returns ``False`` without writing — whenever the file no
    longer looks the way the address says it does.
    """
    text = page.path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    frontmatter = _frontmatter_span(lines)
    if frontmatter is None:
        return False
    sources = _sources_span(lines, frontmatter)
    if sources is None:
        return False
    items = _item_spans(lines, sources)
    if scoped_address.source_index >= len(items):
        return False
    span = items[scoped_address.source_index]

    declared_path = _item_key(lines, span, "path")
    if declared_path is None or _unquote(declared_path[2]) != scoped_address.address.path:
        return False
    declared_hint = _item_key(lines, span, "hint")
    if declared_hint is None or _unquote(declared_hint[2]) != scoped_address.address.hint:
        return False

    index, prefix, _ = declared_hint
    safe_hint = new_address.hint.replace("\\", "\\\\").replace('"', '\\"')
    newline = "\n" if lines[index].endswith("\n") else ""
    lines[index] = f'{prefix}hint: "{safe_hint}"{newline}'
    page.path.write_text("".join(lines), encoding="utf-8")
    return True


#: A freshly created heal log must satisfy `scout.vault.lint_page` on its first
#: line of output, or the very next `gen_index.py --check` fails on a page the
#: healer itself wrote (m7): seven frontmatter fields, a canonical type and
#: department, a one-sentence summary, and the four ordered body headings.
#:
#: `title` and `summary` deliberately match the vault's own `wiki/log.md`,
#: because `wiki/index.md` lists this page by exactly those two values — a
#: recreated log that renamed itself would leave the generated index stale and
#: fail the same `--check` for a different reason.
_LOG_TEMPLATE = """---
type: concept
title: Change Log
summary: Nhật ký thay đổi ở mức vault; bổ sung cho lịch sử Git bằng ghi chú \
người-đọc-được về các quyết định lớn.
entities: [changelog]
department: redteam
sources: []
last_compiled: {today}
---

## TL;DR

Vault-level change log. Every automated hint replacement lands here first;
none of them is merged without a human reading it.

## Technical Specifications

One line per heal, in the order applied: UTC timestamp, the verification status
that triggered it, the page slug, and the old and new hint.

## Provenance

Recreated by `scout/healer.py` because no log page existed. No RAG source backs
this page.

## Cross-References

_(none)_

"""


def append_heal_to_log(
    page_slug: str, old_hint: str, new_hint: str, trigger: str
) -> None:
    """Append one heal record, creating a lint-valid log page if none exists."""
    now = datetime.now(UTC)
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(
            _LOG_TEMPLATE.format(today=now.date().isoformat()), encoding="utf-8"
        )
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    with LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(
            f"- [{timestamp}] AUTO-HEAL ({_one_line(trigger)}): "
            f"{_one_line(page_slug)} — hint {_one_line(old_hint)!r} -> "
            f"{_one_line(new_hint)!r} (pending review)\n"
        )


def _one_line(value: str) -> str:
    """Collapse newlines so one record can never forge a body heading."""
    return " ".join(value.split())


def _snapshot(paths: set[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def apply_heals_in_place(heals: list[ProposedHeal], *, dry_run: bool = False) -> int:
    """Apply proposals transactionally; never commit, push, or reset Git state."""
    branch = current_branch()
    if branch in PROTECTED_BRANCHES or branch in UNRESOLVED_BRANCHES:
        print("Refusing heal outside a resolved non-protected branch.")
        return 1
    if not heals:
        return 0
    if dry_run:
        return 0

    owned_paths = {heal.page.path for heal in heals} | {LOG_FILE}
    snapshot = _snapshot(owned_paths)
    try:
        for heal in heals:
            if not apply_heal_edit(
                heal.page, heal.scoped_address, heal.new_address
            ):
                raise RuntimeError("declared source entry no longer matches")
            append_heal_to_log(
                heal.page.slug,
                heal.old_address.hint,
                heal.new_address.hint,
                heal.trigger,
            )
    except Exception:  # noqa: BLE001 - any partial healer write must roll back
        _restore(snapshot)
        return 1
    return 0


async def compute_heals(
    backend: RagBackend,
    pages: list[vault.Page],
    addresses: list[ScopedAddress],
    reports: list[VerifyReport],
) -> list[ProposedHeal]:
    """Purely compute verified page/source-specific replacements."""
    page_by_identity = {(page.rel, page.slug): page for page in pages}
    reports_by_identity = {report.scoped_address: report for report in reports}
    heals: list[ProposedHeal] = []
    for scoped in addresses:
        report = reports_by_identity.get(scoped)
        if report is None or report.status is VerifyStatus.PASS:
            continue
        page = page_by_identity.get((scoped.page_path, scoped.page_slug))
        if page is None or not scoped.address.loc:
            continue
        candidates = [page.title, page.summary, *page.entities]
        candidates = [candidate for candidate in dict.fromkeys(candidates) if candidate]
        result = await mint_address(
            backend,
            scoped.address.path,
            candidates,
            department=scoped.department,
            loc=scoped.address.loc,
        )
        if result.status is MintStatus.MINTED and result.address is not None:
            heals.append(
                ProposedHeal(
                    page=page,
                    scoped_address=scoped,
                    new_address=result.address,
                    trigger=report.status.name,
                )
            )
    return heals


async def _close_backend(backend: RagBackend) -> None:
    close = getattr(backend, "close", None)
    if close is None:
        return
    result: Any = close()
    if inspect.isawaitable(result):
        await result


async def verify_and_heal_vault(
    backend: RagBackend,
    *,
    ci_mode: bool = False,
    remote: str = "origin",
    push: bool = False,
    base: str = "main",
    dry_run: bool = False,
) -> int:
    """Compute and apply only; the CI gate owns branch/commit/push operations."""
    del remote, push, base
    try:
        if ci_mode:
            branch = current_branch()
            if branch in PROTECTED_BRANCHES or branch in UNRESOLVED_BRANCHES:
                return 1
        pages = list(vault.load_pages())
        addresses = _collect_addresses(pages)
        if not addresses:
            return 0
        reports = await verify_all(backend, addresses)
        heals = await compute_heals(backend, pages, addresses, reports)
        return apply_heals_in_place(heals, dry_run=dry_run)
    finally:
        await _close_backend(backend)


def _build_backend() -> RagBackend:
    import os

    kind = os.environ.get("HEALER_BACKEND", "pgvector").lower()
    if kind != "pgvector":
        raise SystemExit(f"unknown HEALER_BACKEND={kind!r}")
    from scout.backends.pgvector import PgVectorRlsBackend

    return PgVectorRlsBackend()


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Apply scoped address heals")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        exit_code = asyncio.run(
            verify_and_heal_vault(
                _build_backend(), ci_mode=args.ci, dry_run=args.dry_run
            )
        )
    except Exception:  # noqa: BLE001 - backend failures may include credentials
        print("INFRASTRUCTURE ERROR: healer could not complete.")
        exit_code = 2
    raise SystemExit(exit_code)
