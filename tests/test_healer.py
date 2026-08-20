"""Scoped healer computation, application, rollback, and resource tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scout import vault
from scout.core import post_filter
from scout.healer import (
    LOG_FILE,
    ProposedHeal,
    _build_backend,
    append_heal_to_log,
    apply_heal_edit,
    apply_heals_in_place,
    compute_heals,
    verify_and_heal_vault,
)
from scout.types import Address, RagChunk, Scope, ScopedAddress
from scripts.mint import CandidateOutcome, MintResult, MintStatus
from scripts.verify_addresses import (
    VerifyReport,
    VerifyStatus,
    _collect_addresses,
    verify_all,
)


def _page(path: Path, *, department: str = "infra") -> vault.Page:
    return vault.Page(
        path=path,
        frontmatter={
            "title": "Title",
            "summary": "Useful summary.",
            "entities": ["entity"],
            "department": department,
            "sources": [{"path": "raw/a.md", "hint": "old", "loc": "p.1"}],
        },
        body="",
    )


def _scoped(page: vault.Page) -> ScopedAddress:
    return ScopedAddress(
        page_path=page.rel,
        page_slug=page.slug,
        source_index=0,
        department=page.department,
        address=Address(path="raw/a.md", hint="old", loc="p.1"),
    )


def _heal(page: vault.Page) -> ProposedHeal:
    return ProposedHeal(
        page=page,
        scoped_address=_scoped(page),
        new_address=Address(path="raw/a.md", hint="new", loc="p.1"),
        trigger="DRIFT",
    )


async def test_compute_heals_preserves_scope_and_source_identity() -> None:
    page = _page(Path("wiki/page.md"), department="redteam")
    scoped = _scoped(page)
    report = VerifyReport(scoped, VerifyStatus.DRIFT, ("raw/other.md",))
    minted = MintResult(
        path="raw/a.md",
        department="redteam",
        address=Address(path="raw/a.md", hint="new", loc="p.1"),
        status=MintStatus.MINTED,
        tried=(("Title", CandidateOutcome.PASS),),
    )
    with patch("scout.healer.mint_address", new=AsyncMock(return_value=minted)) as mint:
        heals = await compute_heals(object(), [page], [scoped], [report])  # type: ignore[arg-type]
    assert len(heals) == 1
    assert heals[0].scoped_address == scoped
    mint.assert_awaited_once()
    assert mint.await_args.kwargs == {"department": "redteam", "loc": "p.1"}


def test_apply_heal_edit_targets_declared_source_index(tmp_path: Path) -> None:
    path = tmp_path / "page.md"
    path.write_text(
        "---\ndepartment: infra\nsources:\n  - path: raw/a.md\n    hint: old\n    loc: p.1\n  - path: raw/a.md\n    hint: old\n    loc: p.2\n---\n",
        encoding="utf-8",
    )
    page = _page(path)
    scoped = ScopedAddress(
        page_path=str(path),
        page_slug="page",
        source_index=1,
        department="infra",
        address=Address(path="raw/a.md", hint="old", loc="p.2"),
    )
    assert apply_heal_edit(page, scoped, Address("raw/a.md", "new", "p.2"))
    text = path.read_text(encoding="utf-8")
    assert text.count("hint: old") == 1
    assert text.count('hint: "new"') == 1


_BODY_CODE_BLOCK_PAGE = """---
department: infra
sources:
  - path: raw/a.md
    hint: old
    loc: p.1
---

## Technical Specifications

The frontmatter contract looks like this:

```yaml
sources:
  - path: raw/decoy.md
    hint: decoy
    loc: p.99
  - path: raw/decoy2.md
    hint: decoy2
    loc: p.98
```
"""


def test_apply_heal_edit_ignores_source_yaml_inside_the_page_body(tmp_path: Path) -> None:
    """m7: a fenced YAML block in the body must not shift the sources[] index.

    AGENTS.md itself carries such a block. The old whole-file `- path:` scan
    counted the decoys, so index 0 pointed at the wrong entry and every
    legitimate heal on such a page was refused.
    """
    path = tmp_path / "page.md"
    path.write_text(_BODY_CODE_BLOCK_PAGE, encoding="utf-8")
    page = _page(path)
    assert apply_heal_edit(page, _scoped(page), Address("raw/a.md", "new", "p.1"))
    text = path.read_text(encoding="utf-8")
    assert 'hint: "new"' in text
    assert "hint: decoy\n" in text and "hint: decoy2\n" in text  # body untouched
    assert "raw/decoy" in text


def test_apply_heal_edit_handles_a_source_entry_that_leads_with_hint(
    tmp_path: Path,
) -> None:
    """m7: YAML does not require `path:` to be an entry's first key."""
    path = tmp_path / "page.md"
    path.write_text(
        "---\ndepartment: infra\nsources:\n"
        "  - hint: old\n    loc: p.1\n    path: raw/a.md\n"
        "  - loc: p.2\n    path: raw/b.md\n    hint: second\n"
        "---\n",
        encoding="utf-8",
    )
    page = _page(path)
    assert apply_heal_edit(page, _scoped(page), Address("raw/a.md", "new", "p.1"))
    text = path.read_text(encoding="utf-8")
    assert '  - hint: "new"\n' in text  # the leading dash is preserved
    assert "hint: second" in text


def test_apply_heal_edit_never_rewrites_a_sources_block_in_the_page_body(
    tmp_path: Path,
) -> None:
    """m7: the whole-file scan let a stale index land inside the body.

    Index 1 does not exist in this page's frontmatter — only in the documented
    example in its body. The old scan counted that example as a real entry,
    matched its path and hint, and rewrote the prose. Item lookup is now scoped
    to the frontmatter, so the index is simply out of range.
    """
    path = tmp_path / "page.md"
    body = (
        "---\ndepartment: infra\nsources:\n  - path: raw/a.md\n    hint: old\n    loc: p.1\n---\n"
        "\n## Technical Specifications\n\nThe contract looks like this:\n\n"
        "```yaml\nsources:\n  - path: raw/b.md\n    hint: second\n    loc: p.2\n```\n"
    )
    path.write_text(body, encoding="utf-8")
    phantom = ScopedAddress(
        page_path=str(path),
        page_slug="page",
        source_index=1,
        department="infra",
        address=Address(path="raw/b.md", hint="second", loc="p.2"),
    )
    assert not apply_heal_edit(_page(path), phantom, Address("raw/b.md", "healed", "p.2"))
    assert path.read_text(encoding="utf-8") == body


def test_apply_heal_edit_refuses_when_the_declared_entry_no_longer_matches(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page.md"
    path.write_text(
        "---\ndepartment: infra\nsources:\n  - path: raw/moved.md\n    hint: old\n    loc: p.1\n---\n",
        encoding="utf-8",
    )
    page = _page(path)
    before = path.read_bytes()
    assert not apply_heal_edit(page, _scoped(page), Address("raw/a.md", "new", "p.1"))
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter at all\n",
        "---\ndepartment: infra\nsources: []\n---\n",
        "---\ndepartment: infra\n---\n",
    ],
)
def test_apply_heal_edit_fails_closed_on_unparseable_frontmatter(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "page.md"
    path.write_text(text, encoding="utf-8")
    page = _page(path)
    assert not apply_heal_edit(page, _scoped(page), Address("raw/a.md", "new", "p.1"))
    assert path.read_text(encoding="utf-8") == text


# ── m7: a log the healer creates must survive the vault linter ─────────────
def test_append_heal_to_log_creates_a_lint_valid_page(tmp_path: Path) -> None:
    """The old creation path wrote `# Auto-Healer Log` with no frontmatter,
    which failed `gen_index.py --check` on the healer's very next run."""
    log = tmp_path / "log.md"
    with patch("scout.healer.LOG_FILE", log):
        append_heal_to_log("page", "old", "new", "DRIFT")
        append_heal_to_log("other", "stale", "fresh", "FAIL")
    result = vault.lint_page(vault.parse_page(log), raw_dir=tmp_path)
    assert result.errors == []
    text = log.read_text(encoding="utf-8")
    assert text.count("AUTO-HEAL") == 2
    assert "hint 'old' -> 'new'" in text


def test_append_heal_to_log_keeps_a_multiline_hint_on_one_line(tmp_path: Path) -> None:
    """A record must never be able to forge a `##` heading and break the lint."""
    log = tmp_path / "log.md"
    with patch("scout.healer.LOG_FILE", log):
        append_heal_to_log("page", "old", "new\n## Provenance\ninjected", "DRIFT")
    assert vault.lint_page(vault.parse_page(log), raw_dir=tmp_path).errors == []


def test_the_repository_log_page_is_the_shape_the_healer_appends_to() -> None:
    """The checked-in log is a real page, so appending to it stays lint-valid."""
    assert LOG_FILE.exists()
    assert vault.lint_page(vault.parse_page(LOG_FILE)).errors == []


def test_a_recreated_log_keeps_the_index_entry_it_replaces(tmp_path: Path) -> None:
    """`wiki/index.md` lists the log by title + summary.

    A recreated log that renamed itself would be lint-clean and still fail
    `gen_index.py --check`, because the generated index would no longer match.
    """
    existing = vault.parse_page(LOG_FILE)
    log = tmp_path / "log.md"
    with patch("scout.healer.LOG_FILE", log):
        append_heal_to_log("page", "old", "new", "DRIFT")
    recreated = vault.parse_page(log)
    assert (recreated.title, recreated.summary) == (existing.title, existing.summary)
    assert recreated.frontmatter["department"] == existing.frontmatter["department"]


def test_apply_heals_rolls_back_only_healer_owned_bytes_on_failure(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.md"
    second_path = tmp_path / "second.md"
    template = "---\nsources:\n  - path: raw/a.md\n    hint: old\n    loc: p.1\n---\n"
    first_path.write_text(template, encoding="utf-8")
    second_path.write_text(template, encoding="utf-8")
    first, second = _page(first_path), _page(second_path)
    unrelated = tmp_path / "unrelated.md"
    unrelated.write_text("user bytes", encoding="utf-8")
    log = tmp_path / "log.md"
    log.write_text("existing log\n", encoding="utf-8")
    before = (first_path.read_bytes(), second_path.read_bytes(), log.read_bytes())

    with (
        patch("scout.healer.current_branch", return_value="feat/pr"),
        patch("scout.healer.LOG_FILE", log),
        patch("scout.healer.apply_heal_edit", side_effect=[True, False]),
    ):
        assert apply_heals_in_place([_heal(first), _heal(second)]) == 1
    assert (first_path.read_bytes(), second_path.read_bytes(), log.read_bytes()) == before
    assert unrelated.read_text(encoding="utf-8") == "user bytes"


@pytest.mark.parametrize("branch", ["main", "master", "HEAD", "unknown", ""])
def test_apply_heals_rejects_protected_or_unresolved_branch(branch: str) -> None:
    with patch("scout.healer.current_branch", return_value=branch):
        assert apply_heals_in_place([]) == 1


async def test_verify_and_heal_closes_backend() -> None:
    class Backend:
        closed = False

        async def close(self) -> None:
            self.closed = True

    backend = Backend()
    with (
        patch("scout.healer.vault.load_pages", return_value=[]),
        patch("scout.healer.current_branch", return_value="feat/pr"),
    ):
        assert await verify_and_heal_vault(backend, ci_mode=True) == 0  # type: ignore[arg-type]
    assert backend.closed


def test_build_backend_rejects_fake_in_production() -> None:
    with (
        patch.dict("os.environ", {"HEALER_BACKEND": "fake"}, clear=True),
        pytest.raises(SystemExit, match="unknown"),
    ):
        _build_backend()


# ── B1 follow-on: with a real gate, drift is reachable and the healer fires ─
@dataclass
class _SeededBackend:
    """Replays a fixed ranking per hint and honours `path` pre-filtering."""

    mapping: dict[str, list[RagChunk]]

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        chunks = self.mapping.get(hint, [])
        if path is not None:
            chunks = post_filter(chunks, path)
        return chunks[:k]


async def test_injected_drift_is_detected_healed_and_verifies_again(
    tmp_path: Path,
) -> None:
    """The audit's other half of B1: injecting real drift produced 0 heals,
    because DRIFT was effectively unreachable. It is reachable now."""
    path = tmp_path / "page.md"
    path.write_text(
        "---\ntype: concept\ntitle: Title\nsummary: Useful summary.\n"
        "entities: [entity]\ndepartment: infra\nsources:\n"
        "  - path: raw/a.md\n    hint: stale phrase\n    loc: p.1\n"
        "last_compiled: 2026-08-19\n---\n",
        encoding="utf-8",
    )
    backend = _SeededBackend(
        {
            # the declared hint now pulls a different file to the top
            "stale phrase": [
                RagChunk(text="stale phrase", file_path="raw/other.md", score=1.0, loc="p.9"),
                RagChunk(text="stale phrase", file_path="raw/a.md", score=0.1, loc="p.1"),
            ],
            # the page's own title still lands squarely on the addressed file
            "Title": [
                RagChunk(
                    text="Title of the addressed source document",
                    file_path="raw/a.md",
                    score=1.0,
                    loc="p.1",
                )
            ],
        }
    )
    page = vault.parse_page(path)
    addresses = _collect_addresses([page])
    reports = await verify_all(backend, addresses)
    assert [report.status for report in reports] == [VerifyStatus.DRIFT]

    heals = await compute_heals(backend, [page], addresses, reports)
    assert len(heals) == 1
    assert heals[0].new_address.hint == "Title"

    log = tmp_path / "log.md"
    with (
        patch("scout.healer.current_branch", return_value="feat/pr"),
        patch("scout.healer.LOG_FILE", log),
    ):
        assert apply_heals_in_place(heals) == 0
    assert vault.lint_page(vault.parse_page(log), raw_dir=tmp_path).errors == []

    healed = _collect_addresses([vault.parse_page(path)])
    assert healed[0].address.hint == "Title"
    assert [r.status for r in await verify_all(backend, healed)] == [VerifyStatus.PASS]
