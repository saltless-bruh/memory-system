"""Shared vault helpers and data models for SNP Knowledge Vault (wiki/*.md).

Deliberately dependency-light: only PyYAML. Everything here treats the
Markdown files in wiki/ as the source of truth (R-1.1, R-2.5) — nothing
in this module writes to an engine index.
"""

from __future__ import annotations

import datetime
import os
import re
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

VALID_TYPES = frozenset(["technique", "entity", "playbook", "concept"])
VALID_DEPARTMENTS = frozenset(["redteam", "blueteam", "ai_eng", "infra"])

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
_SENTENCE_TERMINATOR_RE = re.compile(r"[.!?](?=\s|$)")
REQUIRED_HEADINGS = (
    "TL;DR",
    "Technical Specifications",
    "Provenance",
    "Cross-References",
)


def write_transcript(path: Path, obj: dict[str, Any]) -> bool:
    """Persist a transcript as JSON. Returns True on success."""
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
    def title(self) -> str:
        return str(self.frontmatter.get("title", self.slug))

    @property
    def summary(self) -> str:
        return str(self.frontmatter.get("summary", ""))

    @property
    def entities(self) -> list[str]:
        raw = self.frontmatter.get("entities", [])
        return [str(e) for e in raw] if isinstance(raw, list) else []

    @property
    def department(self) -> str:
        return str(self.frontmatter.get("department", ""))

    @property
    def sources(self) -> list[dict[str, Any]]:
        raw = self.frontmatter.get("sources", [])
        return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []

    @property
    def last_compiled(self) -> str:
        return str(self.frontmatter.get("last_compiled", ""))

    @property
    def wikilinks(self) -> list[str]:
        return _WIKILINK_RE.findall(self.body)


def parse_page(path: Path) -> Page:
    """Parse one Markdown file into a `Page`."""
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        return Page(path=path, frontmatter={}, body=text)
    raw_fm, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(raw_fm) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return Page(path=path, frontmatter=fm, body=body)


def load_pages(wiki_dir: Path = WIKI_DIR) -> list[Page]:
    """Load every non-generated `.md` page in deterministic order."""
    if wiki_dir.is_symlink():
        raise ValueError(f"wiki root must not be a symlink: {wiki_dir}")
    if not wiki_dir.exists():
        return []
    root = wiki_dir.resolve(strict=True)
    pages: list[Page] = []
    paths: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in directory_names:
            child = current / name
            if child.is_symlink():
                raise ValueError(f"wiki directory must not be a symlink: {child}")
        for name in file_names:
            if not name.endswith(".md"):
                continue
            child = current / name
            if child.is_symlink():
                raise ValueError(f"wiki page must not be a symlink: {child}")
            try:
                child.resolve(strict=True).relative_to(root)
            except (OSError, ValueError) as exc:
                raise ValueError(f"wiki page escapes its root: {child}") from exc
            paths.append(child)

    generated_index = root / "index.md"
    for page_path in sorted(paths):
        if page_path == generated_index:
            continue
        pages.append(parse_page(page_path))
    return pages


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def check_tree(wiki_dir: Path = WIKI_DIR) -> LintResult:
    """Verify that every entry in `REQUIRED_TREE` exists."""
    res = LintResult()
    for entry in REQUIRED_TREE:
        target = wiki_dir / entry
        if not target.exists():
            res.errors.append(f"missing required tree entry: wiki/{entry}")
    return res


def lint_page(
    page: Page, *, raw_dir: Path | None = None, known_slugs: set[str] | None = None
) -> LintResult:
    """Comprehensive structural and canonical contract lint for one page.

    Checks:
      - 7 required frontmatter fields
      - Valid type in {technique, entity, playbook, concept}
      - Valid department in {redteam, blueteam, ai_eng, infra}
      - Exactly 1 single-line summary sentence
      - Valid ISO date format (YYYY-MM-DD)
      - sources[] element shape and disk presence
      - Ordered body section headings
      - Wikilink resolution
    """
    res = LintResult()
    fm = page.frontmatter
    source_root = (raw_dir or RAW_DIR).resolve(strict=False)

    # 1. Frontmatter presence
    for fld in REQUIRED_FRONTMATTER:
        if fld not in fm:
            res.errors.append(f"{page.rel}: missing frontmatter field '{fld}'")

    # R-1.5: no `related:` field
    if "related" in fm:
        res.errors.append(
            f"{page.rel}: forbidden 'related:' field — use [[wikilink]] (R-1.5)"
        )

    # 2. Canonical Type
    page_type = fm.get("type")
    if not isinstance(page_type, str) or page_type not in VALID_TYPES:
        res.errors.append(
            f"{page.rel}: invalid type '{page_type}'. Must be one of: {sorted(VALID_TYPES)}"
        )

    title = fm.get("title")
    if not isinstance(title, str) or not title.strip():
        res.errors.append(f"{page.rel}: title must be a nonempty string")

    # 3. Canonical Department
    dept = fm.get("department")
    if not isinstance(dept, str) or dept not in VALID_DEPARTMENTS:
        res.errors.append(
            f"{page.rel}: invalid department '{dept}'. Must be one of: {sorted(VALID_DEPARTMENTS)}"
        )

    entities = fm.get("entities")
    if (
        not isinstance(entities, list)
        or not entities
        or any(not isinstance(entity, str) or not entity.strip() for entity in entities)
    ):
        res.errors.append(
            f"{page.rel}: entities must be a nonempty list of nonempty strings"
        )

    # 4. Single-Sentence Summary
    raw_summary = fm.get("summary")
    if not isinstance(raw_summary, str) or not raw_summary.strip():
        res.errors.append(f"{page.rel}: summary must be a nonempty string")
    else:
        summary = raw_summary.strip()
        if "\n" in summary or "\r" in summary:
            res.errors.append(f"{page.rel}: summary must be a single line")
        if (
            not summary.endswith((".", "?", "!"))
            or len(_SENTENCE_TERMINATOR_RE.findall(summary)) != 1
        ):
            res.errors.append(f"{page.rel}: summary must contain exactly one sentence")

    # 5. Date validation
    raw_last_compiled = fm.get("last_compiled")
    if isinstance(raw_last_compiled, datetime.datetime):
        valid_date = False
        last_compiled = str(raw_last_compiled)
    elif isinstance(raw_last_compiled, datetime.date):
        valid_date = True
        last_compiled = raw_last_compiled.isoformat()
    elif isinstance(raw_last_compiled, str):
        last_compiled = raw_last_compiled.strip()
        valid_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_compiled))
        try:
            datetime.date.fromisoformat(last_compiled)
        except ValueError:
            valid_date = False
    else:
        valid_date = False
        last_compiled = str(raw_last_compiled)
    if not valid_date:
        res.errors.append(
            f"{page.rel}: invalid last_compiled date '{last_compiled}' "
            "(expected YYYY-MM-DD)"
        )

    # 6. Sources validation
    raw_sources = fm.get("sources")
    if not isinstance(raw_sources, list):
        res.errors.append(f"{page.rel}: sources must be a list")
        raw_sources = []
    for i, src in enumerate(raw_sources):
        if not isinstance(src, dict):
            res.errors.append(f"{page.rel}: sources[{i}] is not a mapping")
            continue
        for key in ("path", "loc", "hint"):
            if key not in src:
                res.errors.append(f"{page.rel}: sources[{i}] missing '{key}' (R-1.4)")
            elif not isinstance(src[key], str) or not src[key].strip():
                res.errors.append(
                    f"{page.rel}: sources[{i}].{key} must be a nonempty string"
                )

        raw_path = src.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        source_path = Path(raw_path)
        if source_path.is_absolute() or not source_path.parts or source_path.parts[0] != "raw":
            res.errors.append(
                f"{page.rel}: sources[{i}].path must be relative beneath raw/: {raw_path}"
            )
            continue
        disk = source_root.joinpath(*source_path.parts[1:]).resolve(strict=False)
        try:
            disk.relative_to(source_root)
        except ValueError:
            res.errors.append(
                f"{page.rel}: sources[{i}].path escapes raw/: {raw_path}"
            )
            continue
        if not disk.is_file():
            res.errors.append(
                f"{page.rel}: sources[{i}].path not on disk: {raw_path}"
            )

    # 7. Ordered Body Headings (for standard notes)
    actual_headings = tuple(
        match.group(1).strip()
        for match in re.finditer(r"^##[ \t]+(.+?)[ \t]*$", page.body, re.MULTILINE)
    )
    if actual_headings != REQUIRED_HEADINGS:
        res.errors.append(
            f"{page.rel}: section headings must appear exactly once in order: "
            + " -> ".join(REQUIRED_HEADINGS)
        )

    # 8. Wikilinks validation
    if known_slugs is not None:
        for link in page.wikilinks:
            target = link.split("|")[0].split("#")[0].strip()
            if target and target not in known_slugs:
                res.warnings.append(
                    f"{page.rel}: wikilink [[{target}]] resolves to no page"
                )

    return res
