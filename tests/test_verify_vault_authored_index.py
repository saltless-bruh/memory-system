"""An authored `index.md` is a control document, not a stale generated file.

`verify-vault` used to fail whenever `index.md` differed from `render_index()`,
which builds every description from `summary:` behind a "generated -- do not
edit" header. AGENTS.md section 4 calls `index.md` an authored control document
that must never be replaced with generated descriptions, and
`gen_index.write_mode_allowed` refuses to regenerate such a vault -- so on the
production vault the check could never pass, and the remedy it printed
(`snpmemory index`) named a command the CLI does not have.

Owner ruling: on an authored vault INDEX STALE is a warning, not a failure. A
generated index that drifted is still a failure, with a remedy that exists.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.verify import verify_vault  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402

AUTHORED_INDEX = (
    "# Vault Index\n\n"
    "Hand-kept catalogue; descriptions are written by the maintainer.\n\n"
    "- [Some page](concepts/some-page.md) — why a reader would open it\n"
)


def _cfg(repo: Path) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, values={}, repo_root=repo)


def _vault(tmp_path: Path, index: str | None = None) -> Path:
    shutil.copytree(REPO_ROOT / "wiki", tmp_path / "wiki")
    if index is not None:
        (tmp_path / "wiki" / "index.md").write_text(index, encoding="utf-8")
    return tmp_path


def test_an_authored_index_that_differs_is_a_warning_not_a_failure(
    tmp_path: Path,
) -> None:
    repo = _vault(tmp_path, AUTHORED_INDEX)

    result = verify_vault(config=_cfg(repo))

    assert result.exit_code is ExitCode.SUCCESS
    assert result.data["status"] == "pass"
    assert result.data["index_current"] is False
    assert any("index.md" in w for w in result.data["warnings"])
    assert "FAIL" not in result.summary
    joined = "\n".join(result.messages)
    assert "INDEX STALE" not in joined
    assert "regenerate" not in joined, "an authored index must never be regenerated"


def test_a_generated_index_that_drifted_still_fails(tmp_path: Path) -> None:
    repo = _vault(tmp_path)
    index = repo / "wiki" / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "\ndrifted\n", "utf-8")

    result = verify_vault(config=_cfg(repo))

    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["index_current"] is False


def test_the_stale_remedy_names_a_command_that_exists(tmp_path: Path) -> None:
    import scout.cli.declarations  # noqa: F401 - populates the registry
    from scout.cli.registry import REGISTRY

    repo = _vault(tmp_path)
    index = repo / "wiki" / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "\ndrifted\n", "utf-8")

    result = verify_vault(config=_cfg(repo))

    [stale] = [m for m in result.messages if "INDEX STALE" in m]
    assert "snpmemory index" not in stale
    assert len(REGISTRY) > 0
    assert REGISTRY.get("index") is None, "the hint may only name a real command"
    assert "python scripts/gen_index.py" in stale
    assert (REPO_ROOT / "scripts" / "gen_index.py").is_file()


def test_gen_index_check_treats_an_authored_index_as_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The script's own `--check` agrees with the CLI on the same tree."""
    from scripts import gen_index

    repo = _vault(tmp_path, AUTHORED_INDEX)
    monkeypatch.setattr(gen_index, "WIKI_DIR", repo / "wiki")
    monkeypatch.setattr(gen_index, "INDEX_PATH", repo / "wiki" / "index.md")
    monkeypatch.setattr(sys, "argv", ["gen_index.py", "--check"])

    assert gen_index.main() == 0
    out = capsys.readouterr().out
    assert "INDEX STALE" not in out
    assert "PASS" in out
