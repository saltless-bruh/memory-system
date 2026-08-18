"""Tests for automated wiki page compiler."""

from pathlib import Path
from unittest.mock import patch

import pytest

from scout.types import Address
from scripts.compile_note import compile_note, generate_mock_data
from scripts.mint import MintResult, MintStatus


@pytest.fixture
def mock_mint():
    with patch("scripts.compile_note.mint_address") as mock:
        yield mock


@pytest.fixture
def mock_gen_index():
    with patch("scripts.compile_note.subprocess.run") as mock:
        yield mock


@pytest.fixture
def mock_raw_file(tmp_path: Path):
    raw_dir = tmp_path / "raw" / "reports"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "acme.txt"
    raw_file.write_text("Acme content")

    with patch("scripts.compile_note.REPO_ROOT", tmp_path):
        yield tmp_path, "raw/reports/acme.txt"


def test_generate_mock_data(mock_raw_file) -> None:
    repo_root, rel_path = mock_raw_file
    with patch("scripts.compile_note.urllib.request.urlopen") as mock_urlopen:
        import io

        mock_resp = io.BytesIO(
            b'{"choices": [{"message": {"content": "{\\"summary\\": \\"Acme Corp is great.\\", \\"entities\\": [\\"mock-entity-1\\"], \\"hint\\": \\"Acme Corp summary\\"}"}}]}'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        data = generate_mock_data("Acme Corp", rel_path)
    assert "Acme Corp" in data["summary"]
    assert data["hint"] == "Acme Corp summary"
    assert "mock-entity-1" in data["entities"]


def test_compile_note_success(mock_mint, mock_gen_index, mock_raw_file) -> None:
    repo_root, rel_path = mock_raw_file

    mock_address = Address(
        path=rel_path, hint="Acme Corp summary", loc="Auto-generated"
    )

    async def mock_mint_address(*args, **kwargs):
        return MintResult(
            path=rel_path, address=mock_address, status=MintStatus.MINTED, tried=()
        )

    mock_mint.side_effect = mock_mint_address

    with patch("scripts.compile_note.PgVectorRlsBackend"):
        compile_note(rel_path, "Acme Corp", "concept")

    wiki_path = repo_root / "wiki" / "concepts" / "acme-corp.md"
    assert wiki_path.exists()

    content = wiki_path.read_text(encoding="utf-8")
    assert "type: concept" in content
    assert "title: Acme Corp" in content
    assert f"path: {rel_path}" in content
    assert "hint: Acme Corp summary" in content
    assert "## TL;DR" in content

    mock_gen_index.assert_called_once()
