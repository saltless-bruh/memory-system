"""Tests for `scout/cli/commands/mcp.py` — `mcp-config` and `mcp --root`.

The commands' jobs are small; their failure modes are not. Three of them are
what these tests mostly pin:

**A user's config is merged, never replaced.** It holds servers this project
knows nothing about.

**A merged document is written but never echoed.** The generated half carries
only a `${SCOUT_AUTH_HEADER}` reference, but the half read from disk may hold a
real token for an unrelated server.

**The served checkout is pinned, not inherited.** A client launches this server
from its own directory, and every tool call resolves configuration and relative
paths against the process cwd.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import scripts.export_mcp_config as exporter  # noqa: E402
from scout.cli.commands.mcp import mcp_config  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402


def _config() -> Config:
    """A config resolved inside this checkout, which is what the command needs."""
    return Config(prerequisite=Prerequisite.LOCAL, repo_root=REPO_ROOT)


def _server_key(client: str) -> str:
    return "servers" if client == "vscode" else "mcpServers"


@pytest.mark.parametrize("client", exporter.SUPPORTED_CLIENTS)
def test_every_supported_client_emits_parseable_json(client: str) -> None:
    result = mcp_config(client=client, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    # `summary` is what text mode writes to stdout, so it is the config a user
    # pastes. It has to parse.
    printed = json.loads(result.summary)
    assert printed == result.data["config"]
    assert sorted(printed[_server_key(client)]) == result.data["servers"]


@pytest.mark.parametrize("client", exporter.SUPPORTED_CLIENTS)
def test_the_emitted_server_set_matches_the_exporter(client: str) -> None:
    result = mcp_config(client=client, config=_config())
    expected = exporter.generate_config(client)[_server_key(client)]
    assert result.data["servers"] == sorted(expected)


def test_an_unknown_client_is_input_validation() -> None:
    with pytest.raises(CliError) as caught:
        mcp_config(client="emacs", config=_config())

    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INPUT_VALIDATION
    assert "cursor" in (caught.value.hint or "")


def test_printing_names_the_conventional_location_without_writing_it() -> None:
    """Where a client reads its config is information, not an instruction."""
    result = mcp_config(client="cursor", config=_config())
    assert result.data["default_path"] == exporter.CLIENT_CONFIG_PATHS["cursor"]
    assert "written_to" not in result.data


def test_out_writes_a_new_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "mcp.json"

    result = mcp_config(client="claude", out=str(target), config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["written_to"] == str(target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["mcpServers"] == exporter.generate_config("claude")["mcpServers"]


def test_an_existing_file_is_untouched_without_confirm(tmp_path: Path) -> None:
    target = tmp_path / "mcp.json"
    original = b'{"mcpServers": {"someone-elses": {"url": "http://x"}}}\n'
    target.write_bytes(original)

    with pytest.raises(CliError) as caught:
        mcp_config(client="claude", out=str(target), config=_config())

    assert caught.value.to_result().exit_code == ExitCode.CONFIRMATION_REQUIRED
    assert target.read_bytes() == original


def test_confirm_merges_rather_than_replacing(tmp_path: Path) -> None:
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {"someone-elses": {"url": "http://x"}},
                "unrelatedSetting": {"keep": True},
            }
        ),
        encoding="utf-8",
    )

    mcp_config(client="claude", out=str(target), confirm=True, config=_config())

    merged = json.loads(target.read_text(encoding="utf-8"))
    assert merged["unrelatedSetting"] == {"keep": True}
    assert merged["mcpServers"]["someone-elses"] == {"url": "http://x"}
    for name in exporter.generate_config("claude")["mcpServers"]:
        assert name in merged["mcpServers"]


def test_a_merged_document_is_written_but_never_echoed(tmp_path: Path) -> None:
    """A neighbouring server's real token must not come back out of the tool."""
    secret = "Bearer a-real-token-for-another-server"
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"headers": {"Authorization": secret}}}}),
        encoding="utf-8",
    )

    result = mcp_config(
        client="claude", out=str(target), confirm=True, config=_config()
    )

    assert secret in target.read_text(encoding="utf-8")
    assert "config" not in result.data
    assert secret not in json.dumps(result.data)
    assert secret not in (result.summary or "")


def test_an_unparseable_target_is_a_conflict_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    target = tmp_path / "mcp.json"
    original = b"{not json at all"
    target.write_bytes(original)

    with pytest.raises(CliError) as caught:
        mcp_config(client="claude", out=str(target), confirm=True, config=_config())

    result = caught.value.to_result()
    assert result.exit_code == ExitCode.CONFLICT
    assert target.read_bytes() == original
    # The file being complained about is the one that may hold a token, so the
    # complaint carries a class name and not its contents.
    assert result.error is not None
    assert result.error.details["cause"] == "JSONDecodeError"


def test_the_generated_document_never_contains_a_resolved_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exporter emits a reference; this command must not resolve it."""
    monkeypatch.setenv("SCOUT_AUTH_HEADER", "Bearer resolved-value")

    result = mcp_config(client="cursor", config=_config())

    rendered = json.dumps(result.data)
    assert "resolved-value" not in rendered
    assert "SCOUT_AUTH_HEADER" in rendered


def test_mcp_config_is_declared_and_has_an_exposure_decision() -> None:
    from scout.cli.declarations import DECLARED
    from scout.cli.mcp_policy import Exposure, policy_for

    assert any(spec.name == "mcp-config" for spec in DECLARED)
    policy = policy_for("mcp-config")
    assert policy is not None and policy.exposure is Exposure.HIDDEN


# ── `snpmemory mcp --root` — the server half of the pin ────────────────────


def _mcp() -> Any:
    from scout.cli.commands.mcp import mcp

    return mcp


def test_root_pins_the_working_directory_so_a_client_may_launch_anywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A client starts this server from its own directory, not from the repo.

    Every tool call resolves its configuration, its `.env`, and any relative
    plan path against the process cwd, so an unpinned launch serves a different
    checkout — or, from outside one, no checkout at all.
    """
    monkeypatch.chdir(tmp_path)
    outside = Config(prerequisite=Prerequisite.LOCAL, repo_root=None)

    result = _mcp()(root=str(REPO_ROOT), list_tools=True, config=outside)

    assert result.exit_code == ExitCode.SUCCESS
    assert Path.cwd() == REPO_ROOT
    assert {tool["name"] for tool in result.data["tools"]} == {
        "verify",
        "plan_articles",
        "compile_plan",
        "compile_status",
    }


def test_root_accepts_a_subdirectory_and_pins_the_checkout_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    outside = Config(prerequisite=Prerequisite.LOCAL, repo_root=None)

    _mcp()(root=str(REPO_ROOT / "scout"), list_tools=True, config=outside)

    assert Path.cwd() == REPO_ROOT


def test_root_outside_any_checkout_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    empty = tmp_path / "not-a-checkout"
    empty.mkdir()

    with pytest.raises(CliError) as caught:
        _mcp()(
            root=str(empty),
            list_tools=True,
            config=Config(prerequisite=Prerequisite.LOCAL, repo_root=None),
        )

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION
    assert Path.cwd() == tmp_path


def test_a_root_that_does_not_exist_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CliError) as caught:
        _mcp()(
            root=str(tmp_path / "absent"),
            list_tools=True,
            config=Config(prerequisite=Prerequisite.LOCAL, repo_root=None),
        )

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_without_root_a_checkout_is_still_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CliError) as caught:
        _mcp()(
            list_tools=True,
            config=Config(prerequisite=Prerequisite.LOCAL, repo_root=None),
        )

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_the_exported_root_is_the_checkout_mcp_config_was_run_in() -> None:
    """What `mcp-config` writes and what `mcp --root` accepts are one value."""
    result = mcp_config(client="claude", config=_config())
    local = result.data["config"]["mcpServers"]["snpmemory"]
    assert local["args"] == ["mcp", "--root", str(REPO_ROOT)]
