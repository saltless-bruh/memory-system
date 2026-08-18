"""Tests for continuous auto-healer (Step 2, Technical_Blueprint_Auto_Healer_CICD.md)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scout import vault  # noqa: E402
from scout.healer import (  # noqa: E402
    ProposedHeal,
    _build_backend,
    append_heal_to_log,
    apply_heal_edit,
    apply_heals_in_place,
    propose_heals,
    verify_and_heal_vault,
)
from scout.types import Address, RagBackend  # noqa: E402
from scripts.mint import MintResult, MintStatus  # noqa: E402
from scripts.verify_addresses import VerifyReport, VerifyStatus  # noqa: E402


@pytest.mark.anyio
async def test_verify_and_heal_vault_success():
    backend = MagicMock(spec=RagBackend)

    mock_page = vault.Page(
        path=Path("wiki/test_page.md"),
        frontmatter={
            "summary": "test summary",
            "sources": [{"path": "raw/test.pdf", "hint": "drifted hint", "loc": "p.1"}],
        },
        body="content",
    )

    with patch("scout.healer.vault.load_pages", return_value=[mock_page]):
        drift_report = VerifyReport(
            path="raw/test.pdf",
            hint="drifted hint",
            status=VerifyStatus.DRIFT,
            matched_files=("raw/other.pdf",),
        )
        with patch(
            "scout.healer.verify_all", new_callable=AsyncMock
        ) as mock_verify_all:
            mock_verify_all.return_value = [drift_report]

            minted_addr = Address(path="raw/test.pdf", hint="new hint", loc="p.1")
            mint_res = MintResult(
                path="raw/test.pdf",
                address=minted_addr,
                status=MintStatus.MINTED,
                tried=(
                    ("drifted hint", VerifyStatus.DRIFT),
                    ("test summary", VerifyStatus.PASS),
                ),
            )
            with patch(
                "scout.healer.mint_address", new_callable=AsyncMock
            ) as mock_mint:
                mock_mint.return_value = mint_res

                with patch("scout.healer.propose_heals") as mock_propose:
                    mock_propose.return_value = 0
                    res = await verify_and_heal_vault(backend, dry_run=True)
                    assert res == 0

                    mock_mint.assert_called_once()
                    mock_propose.assert_called_once()
                    heals = mock_propose.call_args[0][0]
                    assert len(heals) == 1
                    assert heals[0].page == mock_page
                    assert heals[0].old_address.hint == "drifted hint"
                    assert heals[0].new_address.hint == "new hint"
                    assert heals[0].trigger == "DRIFT"


def test_apply_heal_edit(tmp_path):
    page_path = tmp_path / "test.md"
    page_path.write_text("---\nhint: old hint\n---")
    page = vault.Page(path=page_path, frontmatter={}, body="")

    old_addr = Address(path="test", hint="old hint")
    new_addr = Address(path="test", hint="new hint")

    res = apply_heal_edit(page, old_addr, new_addr)
    assert res is True
    assert 'hint: "new hint"' in page_path.read_text()


def test_append_heal_to_log(tmp_path):
    log_file = tmp_path / "log.md"
    with patch("scout.healer.LOG_FILE", log_file):
        append_heal_to_log("test_page", "old", "new", "DRIFT")
        content = log_file.read_text()
        assert "AUTO-HEAL (DRIFT): test_page" in content
        assert "hint 'old' -> 'new'" in content


def test_build_backend_defaults_to_pgvector():
    with patch.dict("os.environ", {}, clear=True):
        from scout.backends.pgvector import PgVectorRlsBackend

        backend = _build_backend()
        assert isinstance(backend, PgVectorRlsBackend)


def test_build_backend_supports_fake():
    with patch.dict("os.environ", {"HEALER_BACKEND": "fake"}):
        from scout.backends.fake import FakeRagBackend

        backend = _build_backend()
        assert isinstance(backend, FakeRagBackend)


def test_apply_heals_in_place_refuses_on_protected_branch():
    mock_page = vault.Page(path=Path("wiki/test.md"), frontmatter={}, body="")
    heal = ProposedHeal(
        page=mock_page,
        old_address=Address(path="a", hint="old"),
        new_address=Address(path="a", hint="new"),
        trigger="DRIFT",
    )
    with patch("scout.healer.current_branch", return_value="main"):
        rc = apply_heals_in_place([heal])
        assert rc == 1


def test_apply_heals_in_place_success_on_pr_branch(tmp_path):
    page_path = tmp_path / "test.md"
    page_path.write_text("---\nhint: old\n---")
    mock_page = vault.Page(path=page_path, frontmatter={}, body="")
    heal = ProposedHeal(
        page=mock_page,
        old_address=Address(path="a", hint="old"),
        new_address=Address(path="a", hint="new"),
        trigger="DRIFT",
    )
    log_file = tmp_path / "log.md"

    with (
        patch("scout.healer.current_branch", return_value="feat/my-pr"),
        patch("scout.healer.LOG_FILE", log_file),
    ):
        rc = apply_heals_in_place([heal])
        assert rc == 0
        assert 'hint: "new"' in page_path.read_text()
        assert log_file.exists()


def test_propose_heals_aborts_when_lint_fails():
    mock_page = vault.Page(path=Path("wiki/test.md"), frontmatter={}, body="")
    heal = ProposedHeal(
        page=mock_page,
        old_address=Address(path="a", hint="old"),
        new_address=Address(path="a", hint="new"),
        trigger="DRIFT",
    )

    with (
        patch("scout.healer.wiki_changes", return_value=[]),
        patch("scout.healer.current_branch", return_value="feat/test"),
        patch("subprocess.check_output", return_value="commit_hash"),
        patch("scout.healer.git") as mock_git,
        patch("scout.healer.apply_heal_edit", return_value=True),
        patch("scout.healer.append_heal_to_log"),
        patch("scout.healer.run_lint", return_value=False),
    ):
        rc = propose_heals([heal])
        assert rc == 1
        # Verify checkout cleaned up
        mock_git.assert_any_call("checkout", "--", "wiki", check=False)
