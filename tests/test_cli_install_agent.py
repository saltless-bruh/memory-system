"""Tests for `snpmemory install-agent`.

The property most worth protecting: this command writes instructions into a
directory somebody else owns. A target that already has an `.agent/` is somebody
else's configured project, and replacing files there without being asked is the
failure mode — so the refusal, and the fact that `--dry-run` reaches nothing, are
what these tests mostly pin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.agent import install_agent  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402


def _config() -> Config:
    """The command needs a real checkout: it runs this repo's own installer."""
    return Config(prerequisite=Prerequisite.LOCAL, repo_root=REPO_ROOT)


def _code(caught: pytest.ExceptionInfo[CliError]) -> ExitCode:
    return caught.value.to_result().exit_code


def test_a_target_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(CliError) as caught:
        install_agent(str(target), config=_config())

    assert _code(caught) == ExitCode.INPUT_VALIDATION


def test_a_missing_target_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path / "absent"), config=_config())

    assert _code(caught) == ExitCode.INPUT_VALIDATION


def test_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    result = install_agent(str(tmp_path), dry_run=True, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "dry_run"
    assert any(".agent/skills" in path for path in result.data["would_write"])
    assert list(tmp_path.iterdir()) == [], "a dry run must touch nothing"


def test_installing_into_an_empty_directory_needs_no_confirmation(
    tmp_path: Path,
) -> None:
    result = install_agent(str(tmp_path), config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "installed"
    assert (tmp_path / ".agent" / "rules" / "snp-memory.md").is_file()
    assert (tmp_path / ".mcp.json").is_file()


def test_a_target_that_already_has_an_agent_directory_is_refused(
    tmp_path: Path,
) -> None:
    """Somebody else's configured project. Replacing files there is not implicit."""
    (tmp_path / ".agent" / "rules").mkdir(parents=True)
    theirs = tmp_path / ".agent" / "rules" / "theirs.md"
    theirs.write_text("their rule\n", encoding="utf-8")

    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path), config=_config())

    assert _code(caught) == ExitCode.CONFIRMATION_REQUIRED
    assert theirs.read_text(encoding="utf-8") == "their rule\n"
    assert not (tmp_path / ".mcp.json").exists()


def test_confirm_installs_over_an_existing_agent_directory(tmp_path: Path) -> None:
    (tmp_path / ".agent" / "rules").mkdir(parents=True)
    (tmp_path / ".agent" / "rules" / "theirs.md").write_text("keep\n", encoding="utf-8")

    result = install_agent(str(tmp_path), confirm=True, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    # The installer is non-destructive: it copies alongside rather than wiping.
    assert (tmp_path / ".agent" / "rules" / "theirs.md").is_file()
    assert (tmp_path / ".agent" / "rules" / "snp-memory.md").is_file()


def test_the_installed_project_gets_all_three_servers(tmp_path: Path) -> None:
    result = install_agent(str(tmp_path), config=_config())

    assert result.data["servers"] == ["scout", "snp-wiki", "snpmemory"]
    written = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert set(written["mcpServers"]) == {"snp-wiki", "scout", "snpmemory"}


def test_installing_is_idempotent(tmp_path: Path) -> None:
    install_agent(str(tmp_path), config=_config())
    before = (tmp_path / ".mcp.json").read_bytes()

    result = install_agent(str(tmp_path), confirm=True, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert (tmp_path / ".mcp.json").read_bytes() == before


def test_the_repo_local_layer_is_not_installed(tmp_path: Path) -> None:
    """A consumer's project must not acquire this repository's own workflow."""
    install_agent(str(tmp_path), config=_config())

    installed = [p.name for p in (tmp_path / ".agent" / "skills").iterdir()]
    assert installed, "nothing was installed"
    assert not any(name.startswith("superpowers-") for name in installed)


def test_install_agent_is_declared_and_hidden_from_the_tool_surface() -> None:
    from scout.cli.declarations import DECLARED
    from scout.cli.mcp_policy import Exposure, policy_for

    assert any(spec.name == "install-agent" for spec in DECLARED)
    policy = policy_for("install-agent")
    assert policy is not None and policy.exposure is Exposure.HIDDEN
