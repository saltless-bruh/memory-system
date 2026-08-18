"""Shared vault helpers and data models for SNP Knowledge Vault (wiki/*.md).

Deliberately dependency-light: only PyYAML. Everything here treats the
Markdown files in wiki/ as the source of truth (R-1.1, R-2.5) — nothing
in this module writes to an engine index.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = REPO_ROOT / "wiki"
RAW_DIR = REPO_ROOT / "raw"

REQUIRED_FRONTMATTER = (
    "type",
    "title",
    "summary",
    "entities",
    "department",
    "sources",
    "last_compiled",
)

# R-1.2 required tree.
REQUIRED_TREE = (
    "index.md",
    "archive.md",
    "log.md",
    "techniques",
    "entities",
    "playbooks",
    "concepts",
)

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def write_transcript(path: Path, obj: dict[str, Any]) -> bool:
    """Persist a transcript as JSON. Returns True on success.

    Never raises: if the output dir is read-only (or sandboxed), warn and
    return False so the harness still exits cleanly with results already
    printed to stdout.
    """
    import json

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError as e:
        print(
            f"WARN: could not write transcript to {path} ({e}). "
            f"Results above are still valid; copy them manually."
        )
        return False


@dataclass
class Page:
    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def rel(self) -> str:
        try:
            return self.path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return self.path.as_posix()


    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def wikilinks(self) -> list[str]:
        return _WIKILINK_RE.findall(self.body)

    @property
    def sources(self) -> list[dict[str, Any]]:
        sources_val = self.frontmatter.get("sources")
        if isinstance(sources_val, list):
            return [s for s in sources_val if isinstance(s, dict)]
        return []


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body). Raises ValueError if no frontmatter."""
    m = _FM_RE.match(text)
    if not m:
        raise ValueError("no YAML frontmatter block found")
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter is not a mapping")
    return fm, m.group(2)


def load_page(path: Path) -> Page:
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return Page(path=path, frontmatter=fm, body=body)


def iter_pages(wiki_dir: Path = WIKI_DIR) -> Iterator[Path]:
    """Yield every content page. index.md is generated, so it is skipped."""
    for p in sorted(wiki_dir.rglob("*.md")):
        if p.name == "index.md":
            continue
        yield p


def load_pages(wiki_dir: Path = WIKI_DIR) -> list[Page]:
    pages = []
    for p in iter_pages(wiki_dir):
        try:
            pages.append(load_page(p))
        except ValueError as e:
            # Surface as a synthetic page-less error via exception; callers
            # that care about lint use lint_page directly.
            raise ValueError(f"{p}: {e}") from e
    return pages


def check_tree(wiki_dir: Path = WIKI_DIR) -> LintResult:
    """R-1.2: verify the required vault tree exists."""
    res = LintResult()
    if not wiki_dir.is_dir():
        res.errors.append(f"wiki dir missing: {wiki_dir}")
        return res
    for entry in REQUIRED_TREE:
        target = wiki_dir / entry
        if not target.exists():
            res.errors.append(f"missing required tree entry: wiki/{entry}")
    return res


def lint_page(
    page: Page, *, raw_dir: Path = RAW_DIR, known_slugs: set[str] | None = None
) -> LintResult:
    """Structural lint for one page (subset of gen_index.py, T-1.2).

    Checks: required frontmatter fields, sources[] element shape, and
    (when raw_dir given) that each source path exists on disk. Wikilink
    resolution is checked only when known_slugs is provided.
    """
    res = LintResult()
    fm = page.frontmatter

    for fld in REQUIRED_FRONTMATTER:
        if fld not in fm:
            res.errors.append(f"{page.rel}: missing frontmatter field '{fld}'")

    # R-1.5: no `related:` field — wikilinks are the only link source.
    if "related" in fm:
        res.errors.append(
            f"{page.rel}: forbidden 'related:' field — use [[wikilink]] (R-1.5)"
        )

    for i, src in enumerate(page.sources):
        if not isinstance(src, dict):
            res.errors.append(f"{page.rel}: sources[{i}] is not a mapping")
            continue
        for k in ("path", "loc", "hint"):
            if k not in src:
                res.errors.append(f"{page.rel}: sources[{i}] missing '{k}' (R-1.4)")
        path = src.get("path")
        if path:
            # A placeholder alongside the real file counts as "exists" for
            # local dev where the real binary isn't committed.
            disk = REPO_ROOT / path
            placeholder = REPO_ROOT / (path + ".placeholder.md")
            if not disk.exists() and not placeholder.exists():
                res.errors.append(f"{page.rel}: sources[{i}].path not on disk: {path}")

    if known_slugs is not None:
        for link in page.wikilinks:
            target = link.split("|")[0].split("#")[0].strip()
            if target and target not in known_slugs:
                res.warnings.append(
                    f"{page.rel}: wikilink [[{target}]] resolves to no page"
                )

    return res


if __name__ == "__main__":
    import sys

    tree = check_tree()
    all_ok = tree.ok
    for e in tree.errors:
        print(f"TREE ERROR: {e}")

    pages = load_pages()
    slugs = {p.slug for p in pages}
    for page in pages:
        r = lint_page(page, known_slugs=slugs)
        for e in r.errors:
            print(f"LINT ERROR: {e}")
            all_ok = False
        for w in r.warnings:
            print(f"LINT WARN:  {w}")

    print(f"\n{len(pages)} pages checked — {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)
