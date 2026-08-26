"""Hash what the parser *produced*, keyed by the environment that produced it.

`PARSER_REVISION` is a manual constant: nothing forces a bump when parser
behaviour changes, which is the failure the fingerprint exists to prevent,
reappearing one level up. This module closes that by hashing the parse itself,
so a behaviour change that forgot its revision bump fails a test.

The hash covers the **parsed structure** — section locs, section text, section
metadata, and the document's metadata — not the raw bytes. Raw bytes would only
prove the fixture had not been edited.

Two environments legitimately produce different corpora from the same file (the
host has `pdfplumber` and `pillow`; the deployed image deliberately does not),
so one golden hash would be ambiguous. Goldens are therefore keyed by capability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scout.capabilities import capability_fingerprint
from scout.parsers import ParsedDocument

GOLDEN_DIR = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "parser-golden"
)


def golden_key(fingerprint: dict[str, Any] | None = None) -> str:
    """Name the environment a golden hash belongs to.

    Includes extractor **versions**, not merely availability: a pdfplumber
    release can change reconstructed table text without any change here, and
    that must present as "no golden for this environment, record one after
    reviewing the diff" rather than as "the parser changed and nobody bumped
    the revision" — which would blame the wrong thing.

    Excludes the Python version, and that exclusion is **measured**: on
    2026-08-26 this document parsed to the identical digest
    (`cefc2e24…`) under CPython 3.12 and 3.14 with the same extractor
    versions. Including it would demand a second golden that carries no
    information.
    """
    fingerprint = fingerprint or capability_fingerprint()
    parts = [f"r{fingerprint['parser_revision']}"]
    for name, entry in sorted((fingerprint.get("extractors") or {}).items()):
        version = entry.get("version") if entry.get("available") else None
        parts.append(f"{name}-{version or 'absent'}")
    return "_".join(parts)


def parse_digest(document: ParsedDocument) -> str:
    """A stable digest of everything the parse decided."""
    payload = {
        "title": document.title,
        "metadata": document.metadata,
        "sections": [
            {"loc": section.loc, "text": section.text, "metadata": section.metadata}
            for section in document.sections
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def golden_path(key: str | None = None) -> Path:
    return GOLDEN_DIR / f"{key or golden_key()}.json"


def load_golden(key: str | None = None) -> dict[str, Any] | None:
    path = golden_path(key)
    if not path.exists():
        return None
    record: dict[str, Any] = json.loads(path.read_text())
    return record
