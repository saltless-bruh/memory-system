"""The grounding half of the address gate must read Vietnamese.

`_TOKEN_RE = [a-z0-9]+` split casefolded text at every accented letter, so a
Vietnamese hint shrank to whatever ASCII fragments of three or more letters
survived. "Kiến trúc bộ nhớ đa tầng cho tác tử" became {"cho"} and was
"grounded" by any text containing that one preposition; "Bảo mật mạng nội bộ"
became {} and could never ground even against itself. Migration 007 treats
Vietnamese content as first class, so the check was vacuous or impossible for
exactly the content it most needed to judge.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_addresses import (  # noqa: E402
    content_tokens,
    grounding_coverage,
    hint_is_grounded,
)

MEMORY_HINT = "Kiến trúc bộ nhớ đa tầng cho tác tử"
SECURITY_HINT = "Bảo mật mạng nội bộ"


def test_accented_words_are_tokens_not_separators() -> None:
    tokens = content_tokens(MEMORY_HINT)
    assert {"kiến", "trúc", "nhớ", "tầng", "tác"} <= tokens
    assert "ki" not in tokens and "n" not in tokens


def test_one_shared_preposition_no_longer_grounds_a_vietnamese_hint() -> None:
    assert grounding_coverage(MEMORY_HINT, ["cho bạn"]) < 0.5
    assert not hint_is_grounded(MEMORY_HINT, ["cho bạn"])


def test_a_vietnamese_hint_grounds_against_its_own_text() -> None:
    assert content_tokens(SECURITY_HINT), "the hint must carry retrieval signal"
    assert grounding_coverage(SECURITY_HINT, [SECURITY_HINT]) == 1.0
    assert hint_is_grounded(SECURITY_HINT, [f"Chương 2. {SECURITY_HINT} là gì"])


def test_decomposed_and_composed_forms_are_the_same_word() -> None:
    """Extracted PDF text is often NFD; a hint typed by a person is NFC."""
    decomposed = unicodedata.normalize("NFD", SECURITY_HINT)
    assert grounding_coverage(SECURITY_HINT, [decomposed]) == 1.0


def test_ascii_behaviour_is_unchanged() -> None:
    assert content_tokens("PagedAttention KV-Cache") == {"pagedattention", "cache"}
    assert content_tokens("snake_case_name") == {"snake", "case", "name"}
    assert grounding_coverage("the of 3", ["anything"]) == 0.0
