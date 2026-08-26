"""What the parser could actually do, recorded with what it produced.

Two runs of the same parser over the same bytes can produce different corpora,
because "the same parser" is not the same thing in two environments. The host
here has `pdfplumber`; the deployed image deliberately does not (T5.1), so a
host ingest emits table sections `sync-job` cannot produce and will drop
unannounced on its next pass. Reproducible-pipeline practice names this exactly:
capturing preprocessing parameters and environment revealed that "identical"
datasets had gone through different pipelines.

The fingerprint records the **environment**, not the output, and it records what
was **available**, never what was configured. That distinction is not
theoretical here: `figures_status: "ok"` for a parser that could not look was a
real bug in this repository (T5.1), and a fingerprint asserting `tables: enabled`
from a config flag would reproduce it one layer up.

`PARSER_REVISION` is deliberately not the package version. This repository
changed parser behaviour twice in one week — lifting the reference list out of
the retrievable text, and making a missing Pillow raise instead of being
swallowed — with no release bump either time. A fingerprint keyed on `0.1.0`
would have claimed equivalence across both.
"""

from __future__ import annotations

import sys
from importlib import metadata
from typing import Any

#: Bump on any change to what the parser *produces* from the same bytes.
#: Not the package version — see the module docstring.
#:
#: 1: chunked page text, tables via pdfplumber, figures via pypdf+Pillow.
#: 2: reference list lifted into metadata (2026-08-25); a missing Pillow raises
#:    `PdfStructureError` instead of being swallowed per page.
PARSER_REVISION = 2

#: Schema of the fingerprint record itself, so it can migrate independently of
#: what it describes.
FINGERPRINT_SCHEMA_VERSION = 1

#: Optional extractors whose presence changes the corpus. Probed, never assumed.
OPTIONAL_EXTRACTORS: tuple[tuple[str, str], ...] = (
    ("tables", "pdfplumber"),
    ("figures", "pillow"),
)


def _probe(distribution: str) -> dict[str, Any]:
    """Import-probe one optional extractor.

    Importing is the only honest test: a distribution can be present and
    unimportable, and `importlib.metadata` alone would call that available.
    """
    module = {"pillow": "PIL"}.get(distribution, distribution)
    try:
        __import__(module)
    except Exception:  # noqa: BLE001 - any import failure means unavailable
        return {"available": False, "version": None}
    try:
        version = metadata.version(distribution)
    except metadata.PackageNotFoundError:  # pragma: no cover - importable, unpackaged
        version = None
    return {"available": True, "version": version}


def capability_fingerprint() -> dict[str, Any]:
    """Describe the parsing environment this process would ingest with."""
    return {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "parser_revision": PARSER_REVISION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "extractors": {
            name: _probe(distribution) for name, distribution in OPTIONAL_EXTRACTORS
        },
    }


def describe_fingerprint_difference(
    recorded: dict[str, Any], current: dict[str, Any]
) -> str:
    """Name what differs between two fingerprints, or "" when they agree.

    Compares only what changes the corpus: the parser revision and which
    extractors were available. An extractor's *version* is recorded for
    diagnosis but does not by itself constitute a mismatch — otherwise every
    patch release would block ingestion.
    """
    differences: list[str] = []

    recorded_revision = recorded.get("parser_revision")
    current_revision = current.get("parser_revision")
    if recorded_revision != current_revision:
        differences.append(f"parser revision {recorded_revision} -> {current_revision}")

    recorded_extractors = recorded.get("extractors") or {}
    current_extractors = current.get("extractors") or {}
    for name in sorted(set(recorded_extractors) | set(current_extractors)):
        was = bool((recorded_extractors.get(name) or {}).get("available"))
        now = bool((current_extractors.get(name) or {}).get("available"))
        if was != now:
            differences.append(
                f"{name} {'available' if was else 'unavailable'} -> "
                f"{'available' if now else 'unavailable'}"
            )

    return "; ".join(differences)
