"""A parser behaviour change cannot claim compatibility, or hide in a skip.

`PARSER_REVISION` is a hand-maintained constant, so nothing forces a bump when
parser behaviour changes — the exact failure the capability fingerprint exists
to prevent, one level up. This hashes what the parser actually produced.

**Why the skip is conditional.** The obvious design skips when the running
environment has no recorded golden. Measured on 2026-08-26, this suite already
reported **21 skipped** — a 22nd is invisible, and a guard that is satisfied by
not running is not a guard. So: an unknown environment skips in local
development, where a contributor may legitimately have different extractor
versions, and **fails** where `SNP_CANONICAL_ENV=1`, which `.gitea/workflows/
checks.yaml` sets.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.capabilities import capability_fingerprint  # noqa: E402
from scout.parser_golden import (  # noqa: E402
    GOLDEN_DIR,
    golden_key,
    load_golden,
    parse_digest,
)
from scout.parsers import parse_pdf  # noqa: E402

SOURCE_URI = "raw/papers/computers-12-00091.pdf"

RE_RECORD = "uv run python -m scout.parser_golden_record"


def _is_canonical() -> bool:
    return os.environ.get("SNP_CANONICAL_ENV", "").strip() == "1"


def test_at_least_one_golden_is_recorded() -> None:
    """Guard against the suite passing because the fixture directory is empty."""
    assert list(GOLDEN_DIR.glob("*.json")), f"no goldens under {GOLDEN_DIR}"


def test_the_parse_matches_the_golden_for_this_environment() -> None:
    source = REPO_ROOT / SOURCE_URI
    if not source.exists():  # pragma: no cover - corpus is committed
        pytest.skip(f"corpus document absent: {SOURCE_URI}")

    key = golden_key()
    golden = load_golden(key)
    if golden is None:
        message = (
            f"no parser golden recorded for this environment.\n"
            f"  key         {key}\n"
            f"  fingerprint {capability_fingerprint()}\n"
            f"  recorded    {sorted(p.stem for p in GOLDEN_DIR.glob('*.json'))}\n"
            f"Review what changed, then record one: {RE_RECORD}"
        )
        if _is_canonical():
            # The canonical environment is the one whose parse defines the
            # corpus. An unrecognised one there is a finding, not a skip.
            pytest.fail(
                "SNP_CANONICAL_ENV=1 and the parsing environment is unknown, so "
                "the corpus this build would produce is unverified.\n" + message
            )
        pytest.skip(message)

    digest = parse_digest(parse_pdf(source, SOURCE_URI))
    assert digest == golden["sha256"], (
        "the parser produces different output than when the golden was "
        f"recorded ({golden['recorded']}).\n"
        f"  key      {key}\n"
        f"  expected {golden['sha256']}\n"
        f"  actual   {digest}\n"
        "If this change is deliberate, bump PARSER_REVISION in "
        f"scout/capabilities.py and re-record: {RE_RECORD}\n"
        "If it is not, the corpus would silently change on the next ingest."
    )


def test_the_golden_key_ignores_the_python_version() -> None:
    """Measured, not assumed.

    On 2026-08-26 this document parsed to the identical digest under CPython
    3.12 and 3.14 with the same extractor versions, so keying on the Python
    minor would demand a second golden carrying no information. If that ever
    stops being true, this exclusion is the thing to revisit.
    """
    base = capability_fingerprint()
    other = {**base, "python": "9.9"}
    assert golden_key(base) == golden_key(other)


def test_the_golden_key_separates_capability_environments() -> None:
    """The host has pdfplumber and pillow; the deployed image does not."""
    host = capability_fingerprint()
    container_like = {
        **host,
        "extractors": {
            name: {"available": False, "version": None} for name in host["extractors"]
        },
    }
    assert golden_key(host) != golden_key(container_like)


def test_an_extractor_version_bump_asks_for_a_new_golden() -> None:
    """It must not present as "the parser changed and nobody bumped the revision".

    A pdfplumber release can change reconstructed table text with no change in
    this repository. Blaming `PARSER_REVISION` for that would send the reader
    to the wrong file, so a version change keys to a different golden and
    surfaces as "record one after reviewing the diff".
    """
    base = capability_fingerprint()
    bumped = {
        **base,
        "extractors": {
            **base["extractors"],
            "tables": {"available": True, "version": "99.0.0"},
        },
    }
    assert golden_key(base) != golden_key(bumped)
    assert load_golden(golden_key(bumped)) is None
