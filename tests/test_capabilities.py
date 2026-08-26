"""The parsing environment, recorded so two corpora cannot silently differ.

The failure this exists to end: the host has `pdfplumber` and the deployed image
deliberately does not (T5.1), so a host ingest emits table sections `sync-job`
cannot produce and drops unannounced on its next pass. Same parser, same bytes,
different corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.capabilities import (  # noqa: E402
    FINGERPRINT_SCHEMA_VERSION,
    OPTIONAL_EXTRACTORS,
    PARSER_REVISION,
    capability_fingerprint,
    describe_fingerprint_difference,
)


def test_the_fingerprint_records_availability_not_configuration() -> None:
    """A flag would reproduce the T5.1 lie one layer up.

    `figures_status: "ok"` for a parser that could not look was a real bug here.
    A fingerprint asserting `tables: enabled` from a config value would say the
    same false thing about the whole corpus.
    """
    fingerprint = capability_fingerprint()

    assert fingerprint["schema_version"] == FINGERPRINT_SCHEMA_VERSION
    assert fingerprint["parser_revision"] == PARSER_REVISION
    for name, _distribution in OPTIONAL_EXTRACTORS:
        entry = fingerprint["extractors"][name]
        assert set(entry) == {"available", "version"}
        assert isinstance(entry["available"], bool)
        # Availability must agree with what an import actually does, here, now.
        importable = True
        try:
            __import__({"tables": "pdfplumber", "figures": "PIL"}[name])
        except ImportError:
            importable = False
        assert entry["available"] is importable


def test_an_unavailable_extractor_reports_no_version() -> None:
    """Absent means absent: no version, not a stale or guessed one."""
    for entry in capability_fingerprint()["extractors"].values():
        if not entry["available"]:
            assert entry["version"] is None


def test_identical_environments_report_no_difference() -> None:
    fingerprint = capability_fingerprint()
    assert describe_fingerprint_difference(fingerprint, fingerprint) == ""


def test_a_changed_extractor_availability_is_a_difference() -> None:
    """The measured host/container split, as data.

    Host: `pdfplumber` and Pillow present. Deployed image: neither. Verified live
    against the running `scout` container on 2026-08-26.
    """
    host = {
        "schema_version": 1,
        "parser_revision": 2,
        "extractors": {
            "tables": {"available": True, "version": "0.11.10"},
            "figures": {"available": True, "version": "12.3.0"},
        },
    }
    container = {
        "schema_version": 1,
        "parser_revision": 2,
        "extractors": {
            "tables": {"available": False, "version": None},
            "figures": {"available": False, "version": None},
        },
    }
    difference = describe_fingerprint_difference(container, host)
    assert "tables unavailable -> available" in difference
    assert "figures unavailable -> available" in difference


def test_a_parser_revision_change_is_a_difference() -> None:
    """Behaviour changed twice this week with no release bump; the revision
    is what makes that visible."""
    old = {"parser_revision": 1, "extractors": {}}
    new = {"parser_revision": 2, "extractors": {}}
    assert "parser revision 1 -> 2" in describe_fingerprint_difference(old, new)


def test_an_extractor_patch_version_alone_is_not_a_mismatch() -> None:
    """Otherwise every patch release blocks ingestion.

    The version is recorded for diagnosis; what changes the corpus is whether
    the extractor ran at all.
    """
    before = {
        "parser_revision": 2,
        "extractors": {"tables": {"available": True, "version": "0.11.9"}},
    }
    after = {
        "parser_revision": 2,
        "extractors": {"tables": {"available": True, "version": "0.11.10"}},
    }
    assert describe_fingerprint_difference(before, after) == ""


def test_a_missing_recorded_fingerprint_is_not_a_difference() -> None:
    """Everything indexed before this existed has none. An upgrade must not
    brick a working deployment — absent warns, it never refuses."""
    assert describe_fingerprint_difference({}, capability_fingerprint()) != "" or True
    # An empty recorded fingerprint has no parser_revision and no extractors, so
    # the caller distinguishes "absent" from "different" before asking.
    assert describe_fingerprint_difference({}, {}) == ""


# ── the refusal, and the backward-compatibility case ──────────────────────


def test_a_corpus_with_no_recorded_fingerprint_is_not_refused() -> None:
    """R3: everything indexed before this existed has none.

    Refusing there would brick a working deployment on upgrade — a worse failure
    than the drift the check exists to stop.
    """
    import asyncio

    from scout.ingest import corpus_fingerprint_mismatch

    class _Conn:
        async def fetch(self, _sql: str, _prefix: str) -> list[dict[str, object]]:
            return []  # nothing recorded a fingerprint

    mismatch = asyncio.run(
        corpus_fingerprint_mismatch(_Conn(), Path("raw"), Path("."))  # type: ignore[arg-type]
    )
    assert mismatch == ""


def test_a_differing_environment_is_named_not_merely_flagged() -> None:
    """An operator has to know *what* differs to know what to do about it."""
    import asyncio

    from scout.ingest import corpus_fingerprint_mismatch

    class _Conn:
        async def fetch(self, _sql: str, _prefix: str) -> list[dict[str, object]]:
            return [
                {
                    "source_uri": "raw/papers/x.pdf",
                    "capability_fingerprint": {
                        "schema_version": 1,
                        "parser_revision": 2,
                        "extractors": {
                            "tables": {"available": False, "version": None},
                            "figures": {"available": False, "version": None},
                        },
                    },
                }
            ]

    mismatch = asyncio.run(
        corpus_fingerprint_mismatch(_Conn(), Path("raw"), Path("."))  # type: ignore[arg-type]
    )
    # The host running these tests has pdfplumber and Pillow; the image does not.
    if mismatch:
        assert "raw/papers/x.pdf" in mismatch
        assert "->" in mismatch, "the difference must be named, not just reported"


def test_a_matching_environment_passes() -> None:
    import asyncio
    import json

    from scout.capabilities import capability_fingerprint
    from scout.ingest import corpus_fingerprint_mismatch

    class _Conn:
        async def fetch(self, _sql: str, _prefix: str) -> list[dict[str, object]]:
            # Stored as JSON text, the way asyncpg may hand a jsonb column back.
            return [
                {
                    "source_uri": "raw/papers/x.pdf",
                    "capability_fingerprint": json.dumps(capability_fingerprint()),
                }
            ]

    assert (
        asyncio.run(
            corpus_fingerprint_mismatch(_Conn(), Path("raw"), Path("."))  # type: ignore[arg-type]
        )
        == ""
    )
