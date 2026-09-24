"""Tests for `scout/cli/commands/mcp.py` — `mcp-config`.

The command's job is small; its failure modes are not. Two of them are what
these tests mostly pin:

**A user's config is merged, never replaced.** It holds servers this project
knows nothing about.

**A merged document is written but never echoed.** The generated half carries
only a `${SCOUT_AUTH_HEADER}` reference, but the half read from disk may hold a
real token for an unrelated server.

The third property this file used to pin — that `snpmemory mcp` served the
checkout given to `--root` rather than whichever directory a client launched it
from — is gone with that command, deleted in leaf-4.3 along with the stdio
server behind it. What is left of the subject is measured below: the command is
absent, and nothing it wrote survives in the emitted config.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    return {"vscode": "servers", "opencode": "mcp"}.get(client, "mcpServers")


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


@pytest.mark.parametrize("client", ["claude", "opencode"])
def test_out_writes_a_new_file(tmp_path: Path, client: str) -> None:
    target = tmp_path / "nested" / "mcp.json"

    result = mcp_config(client=client, out=str(target), config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["written_to"] == str(target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == exporter.generate_config(client)


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


@pytest.mark.parametrize("client", ["claude", "opencode"])
def test_a_merged_document_is_written_but_never_echoed(
    tmp_path: Path, client: str
) -> None:
    """A neighbouring server's real token must not come back out of the tool."""
    secret = "Bearer a-real-token-for-another-server"
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps(
            {_server_key(client): {"other": {"headers": {"Authorization": secret}}}}
        ),
        encoding="utf-8",
    )

    result = mcp_config(client=client, out=str(target), confirm=True, config=_config())

    assert secret in target.read_text(encoding="utf-8")
    assert "config" not in result.data
    assert secret not in json.dumps(result.data)
    assert secret not in (result.summary or "")
    assert secret not in json.dumps(result.messages)


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


@pytest.mark.parametrize("client", exporter.SUPPORTED_CLIENTS)
def test_the_generated_document_never_contains_a_resolved_secret(
    monkeypatch: pytest.MonkeyPatch,
    client: str,
) -> None:
    """The exporter emits a reference; this command must not resolve it."""
    monkeypatch.setenv("SCOUT_AUTH_HEADER", "Bearer resolved-value")

    result = mcp_config(client=client, config=_config())

    rendered = json.dumps(result.data)
    assert "resolved-value" not in rendered
    assert "SCOUT_AUTH_HEADER" in rendered


def test_mcp_config_is_declared_and_has_an_exposure_decision() -> None:
    from scout.cli.declarations import DECLARED
    from scout.cli.mcp_policy import Exposure, policy_for

    assert any(spec.name == "mcp-config" for spec in DECLARED)
    policy = policy_for("mcp-config")
    assert policy is not None and policy.exposure is Exposure.HIDDEN


def test_opencode_prints_native_config_and_names_its_project_path() -> None:
    result = mcp_config(client="opencode", config=_config())
    assert result.data["client"] == "opencode"
    assert result.data["default_path"] == "opencode.json"
    assert result.data["servers"] == ["snpmemory"]
    printed = json.loads(result.summary)
    assert set(printed) == {"$schema", "mcp"}
    assert printed["mcp"]["snpmemory"]["headers"] == {
        "Authorization": "{env:SCOUT_AUTH_HEADER}"
    }


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("json_exists", [False, True])
def test_opencode_jsonc_is_a_conflict_even_with_confirm(
    tmp_path: Path, confirm: bool, json_exists: bool
) -> None:
    jsonc = tmp_path / "opencode.jsonc"
    jsonc.write_text("// Bearer secret-jsonc-value\n{}", encoding="utf-8")
    target = tmp_path / "opencode.json"
    if json_exists:
        target.write_text('{"model":"keep"}', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(CliError) as caught:
        mcp_config(
            client="opencode", out=str(target), confirm=confirm, config=_config()
        )

    result = caught.value.to_result()
    assert result.exit_code == ExitCode.CONFLICT
    assert "opencode.jsonc" in caught.value.message
    assert "reconcile" in (caught.value.hint or "")
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert "secret-jsonc-value" not in str(result)


def test_opencode_custom_export_artifact_can_coexist_with_jsonc(tmp_path: Path) -> None:
    jsonc = tmp_path / "opencode.jsonc"
    original = b"// existing project configuration\n{}\n"
    jsonc.write_bytes(original)
    target = tmp_path / "scout-export.json"

    result = mcp_config(client="opencode", out=str(target), config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert jsonc.read_bytes() == original
    assert (
        json.loads(target.read_text(encoding="utf-8"))["mcp"]["snpmemory"]["type"]
        == "remote"
    )


@pytest.mark.parametrize("original", [b"", b'{"mcp": null}', b'{"mcp": []}'])
def test_malformed_opencode_target_is_a_conflict_without_writes(
    tmp_path: Path, original: bytes
) -> None:
    target = tmp_path / "opencode.json"
    target.write_bytes(original)

    with pytest.raises(CliError) as caught:
        mcp_config(client="opencode", out=str(target), confirm=True, config=_config())

    assert caught.value.to_result().exit_code == ExitCode.CONFLICT
    assert target.read_bytes() == original


def test_opencode_existing_file_needs_confirm_then_merges(tmp_path: Path) -> None:
    target = tmp_path / "opencode.json"
    original = {
        "model": "custom/model",
        "instructions": ["custom.md"],
        "mcp": {
            "other": {"enabled": False},
            "snpmemory": {"type": "local", "command": ["snpmemory", "mcp"]},
        },
    }
    target.write_text(json.dumps(original), encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(CliError) as caught:
        mcp_config(client="opencode", out=str(target), config=_config())
    assert caught.value.to_result().exit_code == ExitCode.CONFIRMATION_REQUIRED
    assert target.read_bytes() == before

    result = mcp_config(
        client="opencode", out=str(target), confirm=True, config=_config()
    )
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["model"] == original["model"]
    assert written["instructions"] == original["instructions"]
    assert written["mcp"]["other"] == original["mcp"]["other"]
    assert set(written["mcp"]) == {"other", "snpmemory"}
    assert written["$schema"] == "https://opencode.ai/config.json"
    assert result.data["servers"] == ["snpmemory"]
    assert "config" not in result.data


# ── the deleted `snpmemory mcp` command (leaf-4.3) ────────────────────────


def test_the_mcp_command_is_gone() -> None:
    """It launched a second MCP server over stdio and is deleted, not hidden.

    The module that held its tools is gone too, so a surviving entry point
    would fail at import rather than at policy — and `mcp-config`, which lives
    in the same module, must keep working.
    """
    import scout.cli.commands.mcp as module

    assert not hasattr(module, "mcp")
    assert callable(module.mcp_config)


def test_no_emitted_entry_launches_the_deleted_command() -> None:
    """Measured over every client, not asserted against one name."""
    for client in exporter.SUPPORTED_CLIENTS:
        servers = mcp_config(client=client, config=_config()).data["config"][
            _server_key(client)
        ]
        for name, entry in servers.items():
            argv = [entry.get("command", ""), *entry.get("args", [])]
            assert "mcp" not in argv[1:], f"{client}/{name} still launches a server"


def test_mcp_config_still_requires_a_checkout() -> None:
    """`root` stopped pinning anything; requiring a repository did not.

    The command writes this project's configuration, so running it from outside
    a checkout is a user error rather than a default.
    """
    with pytest.raises(CliError) as caught:
        mcp_config(
            client="claude",
            config=Config(prerequisite=Prerequisite.LOCAL, repo_root=None),
        )

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION
