#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6", "jsonschema>=4.23"]
# ///
"""Check one draft page against the vault's page contract, and say what it checked.

Prints a receipt, never a verdict alone:

    {"schemaVersion": 1, "ok": false, "command": "verify-page",
     "input": {...}, "checks": [{"name": ..., "ok": ..., "details": ...}],
     "notChecked": [...]}

Every check is named, the count of checks is part of the verdict, and the
things this script *cannot* decide are listed rather than omitted. A page that
passes here is not a certified page; it is a page that passed these checks.
That distinction is the whole point -- `7 pages · 0 errors · PASS` is what this
shape exists to replace.

Exit status: 0 when every check passed, 1 when any failed, 2 when the page
could not be read at all (which is never a pass).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

import yaml

SCHEMA_NAME = "note-schema.json"

#: The authored body frame, restated from AGENTS.md section 4. This script ships
#: to clients that do not have this repository, so it cannot import
#: `scout.vault`; it keeps its own copy, and
#: `tests/test_verify_page_script.py` fails if the two ever disagree. An
#: earlier copy required TL;DR and forbade the H1, the inverse of AGENTS.md, so
#: a page written to the operating contract failed here.
#:
#: Required on every page, exactly once, in this order.
REQUIRED_HEADINGS = ("Cross-References",)
#: "Recommended, not mandatory": at most once, and ahead of the mandated ones.
RECOMMENDED_HEADINGS = ("TL;DR",)
#: "Required when sources are declared": once, before Cross-References.
SOURCED_HEADINGS = ("Provenance",)

#: What this script deliberately does not decide, reported on every run.
NOT_CHECKED = (
    "groundedness: whether each claim is supported by the cited source",
    "address resolution: whether each sources[] hint still retrieves its file",
    "index freshness: whether the served index reflects this page",
    "prose quality: contextual first sentences, one subject per page",
    # The shipped linter reports an unlinked page as a WARNING, not an error,
    # and `snpmemory verify-vault` passes a vault full of them. Failing a page
    # here for it would invent a contract the vault does not hold its own pages
    # to, which is a worse defect than missing one.
    "link density: whether enough other pages link here (a warning upstream)",
)


def _receipt(
    ok: bool, path: pathlib.Path, checks: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    degraded = extra.pop("degraded", [])
    return {
        "schemaVersion": 1,
        "ok": ok,
        "command": "verify-page",
        "input": {"path": str(path), **extra},
        "checks": checks,
        "checkCount": len(checks),
        # Checks that could not run here. A caller reading `ok: true` alongside
        # a non-empty `degraded` knows the page passed what was checkable, not
        # that everything was checked.
        "degraded": list(degraded),
        "notChecked": list(NOT_CHECKED),
    }


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if match is None:
        return None, text
    return match.group(1), match.group(2)


def _load_schema(page: pathlib.Path, explicit: str | None) -> tuple[Any, str]:
    """The vault's own schema, found by walking up from the page."""
    if explicit:
        candidate = pathlib.Path(explicit)
        return json.loads(candidate.read_text(encoding="utf-8")), str(candidate)
    for parent in [page.parent, *page.parent.parents]:
        candidate = parent / SCHEMA_NAME
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8")), str(candidate)
    return None, ""


def verify(page: pathlib.Path, schema_arg: str | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, details: str) -> bool:
        checks.append({"name": name, "ok": ok, "details": details})
        return ok

    text = page.read_text(encoding="utf-8")
    raw_frontmatter, body = _split_frontmatter(text)

    degraded: list[str] = []
    _check_filename(page, record, degraded)

    if raw_frontmatter is None:
        record("frontmatter-present", False, "no `---` delimited block at the top")
        return _receipt(False, page, checks, degraded=degraded)
    record("frontmatter-present", True, "found")

    try:
        frontmatter = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        record("frontmatter-parses", False, f"{type(exc).__name__}: {exc}")
        return _receipt(False, page, checks, degraded=degraded)
    if not isinstance(frontmatter, dict):
        record("frontmatter-parses", False, "frontmatter is not a mapping")
        return _receipt(False, page, checks, degraded=degraded)
    record("frontmatter-parses", True, f"{len(frontmatter)} key(s)")

    schema, schema_source = _load_schema(page, schema_arg)
    if schema is None:
        # Not a defect in the page. The field contract simply could not be
        # read, and saying so is the difference between "checked and clean" and
        # "not checked". `degraded` carries it into the verdict's own shape.
        degraded.append(
            f"field contract: no {SCHEMA_NAME} found above {page.parent}, so "
            "frontmatter was not validated against the vault's schema"
        )
    else:
        record("schema-found", True, schema_source)
        import jsonschema

        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(frontmatter), key=lambda e: list(e.path))
        record(
            "frontmatter-matches-schema",
            not errors,
            "valid"
            if not errors
            else "; ".join(
                f"{list(e.absolute_path) or '<root>'}: {e.message}" for e in errors[:5]
            ),
        )

    prose = _outside_code_fences(body)
    declares_sources = sources_are_declared(frontmatter)
    headings = tuple(re.findall(r"^##[ \t]+(.+?)[ \t]*$", prose, re.M))
    mandated = required_headings(schema, sources_declared=declares_sources)
    record(
        "headings-follow-the-frame",
        headings_are_valid(headings, schema, sources_declared=declares_sources),
        f"found {list(headings)}; required once each, in order: {list(mandated)}"
        + (" (Provenance because sources are declared)" if declares_sources else "")
        + f"; recommended, at most once and first: {list(RECOMMENDED_HEADINGS)}",
    )

    # AGENTS.md: "An H1 and `## Cross-References` are required." A `#` line in
    # a fenced block is a shell comment, not a title.
    h1 = re.findall(r"^#[ \t]+(\S.*)$", prose, re.M)
    record(
        "h1-present",
        bool(h1),
        f"found {h1}" if h1 else "no `# Title` line outside code fences",
    )

    # `[[Page#Section|alias]]` targets `Page`, and `[[#Section]]` this page; the
    # vault linter strips both the same way.
    wikilinks = [
        link.split("|")[0].split("#")[0].strip()
        for link in re.findall(r"\[\[([^\]]+)\]\]", body)
    ]
    wikilinks = [link for link in wikilinks if link]
    known = _page_stems(page)
    if known is None:
        degraded.append(
            "wikilink targets: the vault root could not be located, so link "
            "resolution was not checked"
        )
    else:
        dangling = sorted({link for link in wikilinks if link not in known})
        record(
            "wikilinks-resolve",
            not dangling,
            f"{len(wikilinks)} link(s), all resolve"
            if not dangling
            else f"resolve to no page: {dangling}",
        )

    relationship_keys = sorted(
        k for k in frontmatter if k in {"related", "links", "see_also", "backlinks"}
    )
    record(
        "relationships-live-in-the-body",
        not relationship_keys,
        "none in frontmatter"
        if not relationship_keys
        else f"{relationship_keys} belong in body [[wikilinks]]",
    )

    ok = all(check["ok"] for check in checks)
    return _receipt(
        ok, page, checks, declaresSources=declares_sources, degraded=degraded
    )


def _page_stems(page: pathlib.Path) -> set[str] | None:
    """Every page name in the vault this page belongs to, for link resolution.

    The vault root is the nearest ancestor holding `index.md`, which every
    vault has and no subdirectory does.
    """
    for parent in [page.parent, *page.parent.parents]:
        if (parent / "index.md").is_file():
            return {p.stem for p in parent.rglob("*.md")}
    return None


def required_headings(
    schema: Any, *, sources_declared: bool = False
) -> tuple[str, ...]:
    """Headings this page must carry, in order.

    The vault's own schema (`x-body.requiredHeadings`) wins over AGENTS.md's
    list when it declares one. Either way a page that declares sources must
    also carry Provenance, ahead of `Cross-References` when that is mandated.
    Mirrors `scout.vault.authored_required_headings`.
    """
    required: list[str] = list(REQUIRED_HEADINGS)
    if isinstance(schema, dict):
        body = schema.get("x-body")
        if isinstance(body, dict):
            declared = body.get("requiredHeadings")
            if isinstance(declared, list) and declared:
                required = [str(h) for h in declared]
    if sources_declared:
        for heading in SOURCED_HEADINGS:
            if heading in required:
                continue
            if "Cross-References" in required:
                required.insert(required.index("Cross-References"), heading)
            else:
                required.append(heading)
    return tuple(required)


def headings_are_valid(
    actual: tuple[str, ...], schema: Any, *, sources_declared: bool = False
) -> bool:
    """The authored frame, checked for order and duplication, not presence.

    Mandated headings appear once each and in order; a recommended heading
    appears at most once and ahead of them. Mirrors `scout.vault.
    headings_are_valid` under `HeadingFrame.AUTHORED`.
    """
    mandated = required_headings(schema, sources_declared=sources_declared)
    for heading in mandated:
        if actual.count(heading) != 1:
            return False
    present = [h for h in RECOMMENDED_HEADINGS if h in actual and h not in mandated]
    for heading in present:
        if actual.count(heading) != 1:
            return False
    positions = [actual.index(heading) for heading in (*present, *mandated)]
    return positions == sorted(positions)


def sources_are_declared(frontmatter: dict[str, Any]) -> bool:
    """Whether any nonempty `sources:` entry is declared.

    URL strings and address mappings both count. Mirrors
    `scout.vault.sources_are_declared`.
    """
    declared = frontmatter.get("sources")
    if isinstance(declared, str):
        return bool(declared.strip())
    if isinstance(declared, list):
        return any(
            (isinstance(entry, str) and entry.strip()) or isinstance(entry, dict)
            for entry in declared
        )
    return False


def _outside_code_fences(body: str) -> str:
    """The body with fenced code blocks blanked, so their lines are not headings."""
    kept: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            kept.append("")
            continue
        kept.append("" if fenced else line)
    return "\n".join(kept)


def _tracked_by_git(page: pathlib.Path) -> bool | None:
    """Whether git already tracks `page`; None when git cannot answer."""
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=page.parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", page.name],
            cwd=page.parent,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return tracked.returncode == 0


def _check_filename(page: pathlib.Path, record: Any, degraded: list[str]) -> None:
    """AGENTS.md: *new* pages use lowercase hyphenated filenames.

    An existing page such as `AFFiNE.md` keeps its name -- renaming it would
    break every `[[AFFiNE]]` pointing at it, and the rule never covered it.
    Whether the page is new is asked of git; without a git answer the rule is
    reported as not checked rather than passed or failed.
    """
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*\.md", page.name):
        record("filename-is-lowercase-hyphen", True, page.name)
        return
    tracked = _tracked_by_git(page)
    if tracked is None:
        degraded.append(
            f"filename: {page.name} is not lowercase-hyphen, and without git it "
            "is unknown whether this is a new page (where the rule applies) or "
            "an existing one (where it does not)"
        )
    elif tracked:
        record(
            "filename-is-lowercase-hyphen",
            True,
            f"{page.name} is an existing page; the rule governs new pages",
        )
    else:
        record(
            "filename-is-lowercase-hyphen",
            False,
            f"{page.name}: a new page uses a lowercase hyphenated filename",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", help="the draft page to check")
    parser.add_argument(
        "--schema",
        default=None,
        help=f"path to {SCHEMA_NAME}; found by walking up from the page otherwise",
    )
    args = parser.parse_args(argv)

    page = pathlib.Path(args.page)
    if not page.is_file():
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "command": "verify-page",
                    "input": {"path": str(page)},
                    "checks": [],
                    "checkCount": 0,
                    "error": "the page could not be read",
                    "notChecked": list(NOT_CHECKED),
                },
                indent=2,
            )
        )
        return 2

    receipt = verify(page, args.schema)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
