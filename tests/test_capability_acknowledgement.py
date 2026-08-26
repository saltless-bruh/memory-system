"""Overriding a capability refusal must name the difference it overrides.

`--confirm` is already required for every write in this CLI, so requiring it a
second time acknowledges nothing — it is a keystroke the caller types without
reading, and the plan revision that proposed it was wrong. An acknowledgement
that must equal the mismatch text can only be produced by having read that
specific mismatch, and — the part a boolean can never do — a *stale* one copied
from an earlier refusal about a different difference is rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.ingest import (  # noqa: E402
    CapabilityMismatchError,
    _require_acknowledgement,
)

MISMATCH = "raw/papers/computers-12-00091.pdf: tables available -> unavailable"
STALE = "raw/papers/computers-12-00091.pdf: parser revision 1 -> 2"


def test_the_flag_alone_is_refused() -> None:
    with pytest.raises(CapabilityMismatchError) as excinfo:
        _require_acknowledgement(MISMATCH, None)
    # The refusal must carry the text the caller needs to pass back, or it
    # forces a second run just to read it.
    assert MISMATCH in str(excinfo.value)


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_an_empty_acknowledgement_is_refused(empty: str) -> None:
    with pytest.raises(CapabilityMismatchError):
        _require_acknowledgement(MISMATCH, empty)


def test_a_stale_acknowledgement_is_refused() -> None:
    """The case a boolean flag cannot express.

    A caller who overrode a parser-revision change last month must not be able
    to reuse that acknowledgement for today's extractor-availability change.
    """
    with pytest.raises(CapabilityMismatchError) as excinfo:
        _require_acknowledgement(MISMATCH, STALE)
    message = str(excinfo.value)
    assert "stale" in message
    assert MISMATCH in message and STALE in message


def test_a_wrong_acknowledgement_is_refused() -> None:
    with pytest.raises(CapabilityMismatchError):
        _require_acknowledgement(MISMATCH, "yes")
    with pytest.raises(CapabilityMismatchError):
        _require_acknowledgement(MISMATCH, MISMATCH.replace("unavailable", "available"))


def test_the_exact_text_is_accepted() -> None:
    _require_acknowledgement(MISMATCH, MISMATCH)


@pytest.mark.parametrize(
    "variant",
    [
        f"  {MISMATCH}  ",
        f"'{MISMATCH}'",
        f'"{MISMATCH}"',
        MISMATCH.replace(": ", ":  ").replace(" -> ", "  ->  "),
    ],
    ids=["padded", "single-quoted", "double-quoted", "respaced"],
)
def test_shell_quoting_does_not_defeat_it(variant: str) -> None:
    """Compare on content, not on how a shell mangled the quoting.

    A caller who copies the refusal text correctly and gets rejected over a
    quote character learns that the check is noise, and reaches for the flag
    that skips it.
    """
    _require_acknowledgement(MISMATCH, variant)


def test_it_does_not_accept_a_substring() -> None:
    """ "tables" appears in the mismatch; it is not an acknowledgement of it."""
    with pytest.raises(CapabilityMismatchError):
        _require_acknowledgement(MISMATCH, "tables")
