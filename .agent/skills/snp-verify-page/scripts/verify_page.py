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
import sys
from typing import Any

import yaml

SCHEMA_NAME = "note-schema.json"

#: The frame a V3 page is authored in. Only what a page can honestly supply
#: from its own material is required: `TL;DR` summarises the page's own text and
#: `Cross-References` gathers links the body already holds. `Provenance` is
#: required only when the page declares `sources`, because a page with no
#: sources has nothing to be provenant about.
REQUIRED_HEADINGS = ("TL;DR", "Cross-References")

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

    record(
        "filename-is-lowercase-hyphen",
        bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*\.md", page.name)),
        f"{page.name}",
    )

    if raw_frontmatter is None:
        record("frontmatter-present", False, "no `---` delimited block at the top")
        return _receipt(False, page, checks)
    record("frontmatter-present", True, "found")

    try:
        frontmatter = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        record("frontmatter-parses", False, f"{type(exc).__name__}: {exc}")
        return _receipt(False, page, checks)
    if not isinstance(frontmatter, dict):
        record("frontmatter-parses", False, "frontmatter is not a mapping")
        return _receipt(False, page, checks)
    record("frontmatter-parses", True, f"{len(frontmatter)} key(s)")

    schema, schema_source = _load_schema(page, schema_arg)
    degraded: list[str] = []
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

    headings = re.findall(r"^##\s+(.+?)\s*$", body, re.M)
    missing = [h for h in _required_headings(schema) if h not in headings]
    record(
        "required-headings-present",
        not missing,
        f"found {headings}" if not missing else f"missing {missing}",
    )

    h1 = re.findall(r"^#\s+(?!#)(.+)$", body, re.M)
    record(
        "no-h1-in-body",
        not h1,
        "none" if not h1 else f"the title lives in frontmatter, but found {h1}",
    )

    wikilinks = [
        link.split("|")[0].strip() for link in re.findall(r"\[\[([^\]]+)\]\]", body)
    ]
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

    declares_sources = bool(frontmatter.get("sources"))
    if declares_sources:
        record(
            "provenance-when-sources-declared",
            "Provenance" in headings,
            "present"
            if "Provenance" in headings
            else "sources[] declared without a ## Provenance section",
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


def _required_headings(schema: Any) -> tuple[str, ...]:
    """Headings the vault's own schema requires, falling back to the V3 frame."""
    if isinstance(schema, dict):
        body = schema.get("x-body")
        if isinstance(body, dict):
            required = body.get("requiredHeadings")
            if isinstance(required, list) and required:
                return tuple(str(h) for h in required)
    return REQUIRED_HEADINGS


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
