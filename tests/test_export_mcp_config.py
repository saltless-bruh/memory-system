"""Authenticated MCP client exporter safety and CLI behavior tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import scripts.export_mcp_config as exporter


@pytest.mark.parametrize("client", exporter.SUPPORTED_CLIENTS)
def test_generated_config_preserves_basic_memory_and_authenticates_scout(
    client: str,
) -> None:
    config = exporter.generate_config(client)
    server_key = "servers" if client == "vscode" else "mcpServers"
    servers = config[server_key]
    basic = servers["snp-wiki"]
    scout = servers["scout"]

    if client == "cursor":
        assert basic == {"type": "sse", "url": "http://localhost:8765/mcp"}
    elif client == "vscode":
        assert basic == {"type": "http", "url": "http://localhost:8765/mcp"}
        assert scout["type"] == "stdio"
    elif client == "gemini":
        assert basic == {"httpUrl": "http://localhost:8765/mcp"}
    else:
        assert basic == {
            "command": "npx",
            "args": ["-y", "mcp-remote", "http://localhost:8765/mcp"],
        }

    assert scout["command"] == "npx"
    assert scout["args"] == [
        "-y",
        "mcp-remote",
        "http://localhost:8080/mcp",
        "--allow-http",
        "--header",
        "Authorization:${SCOUT_AUTH_HEADER}",
    ]


@pytest.mark.parametrize(
    ("client", "expected_env"),
    [
        ("cursor", {"SCOUT_AUTH_HEADER": "${env:SCOUT_AUTH_HEADER}"}),
        ("vscode", {"SCOUT_AUTH_HEADER": "${env:SCOUT_AUTH_HEADER}"}),
        ("gemini", {"SCOUT_AUTH_HEADER": "$SCOUT_AUTH_HEADER"}),
        ("claude", None),
    ],
)
def test_each_client_uses_supported_environment_reference(
    client: str, expected_env: dict[str, str] | None
) -> None:
    server_key = "servers" if client == "vscode" else "mcpServers"
    scout = exporter.generate_config(client)[server_key]["scout"]
    if expected_env is None:
        assert "env" not in scout
    else:
        assert scout["env"] == expected_env


def test_export_never_reads_or_serializes_bearer_value(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "Bearer actual-sensitive-value"
    monkeypatch.setenv("SCOUT_AUTH_HEADER", secret)
    assert exporter.main(["--all", "--print"]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert "actual-sensitive-value" not in output
    assert "SCOUT_AUTH_HEADER" in output


def test_client_and_all_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as error:
        exporter.main(["--client", "cursor", "--all"])
    assert error.value.code == 2


@pytest.mark.parametrize("argv", [[], ["--print"]])
def test_non_tty_without_target_exits_2(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        exporter.main(argv)
    assert error.value.code == 2


def test_tty_without_target_may_prompt_and_print(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "gemini")
    assert exporter.main(["--print"]) == 0
    output = capsys.readouterr().out
    printed = json.loads(output[output.index("{") :])
    assert printed == exporter.generate_config("gemini")


def test_print_one_client_is_plain_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert exporter.main(["--client", "cursor", "--print"]) == 0
    assert json.loads(capsys.readouterr().out) == exporter.generate_config("cursor")


def test_print_all_is_keyed_by_every_client(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert exporter.main(["--all", "--print"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert list(output) == exporter.SUPPORTED_CLIENTS
    assert output == {
        client: exporter.generate_config(client)
        for client in exporter.SUPPORTED_CLIENTS
    }


def test_all_writes_every_client_and_preserves_unrelated_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = {
        client: str(tmp_path / f"{client}.json")
        for client in exporter.SUPPORTED_CLIENTS
    }
    monkeypatch.setattr(exporter, "CLIENT_CONFIG_PATHS", paths)
    cursor_path = Path(paths["cursor"])
    cursor_path.write_text(
        json.dumps({"mcpServers": {"unrelated": {"url": "http://other"}}}),
        encoding="utf-8",
    )
    vscode_path = Path(paths["vscode"])
    vscode_path.write_text(
        json.dumps({"servers": {"unrelated": {"url": "http://other"}}}),
        encoding="utf-8",
    )

    assert exporter.main(["--all"]) == 0
    for client, path_string in paths.items():
        written = json.loads(Path(path_string).read_text(encoding="utf-8"))
        server_key = "servers" if client == "vscode" else "mcpServers"
        assert written[server_key]["snp-wiki"] == exporter.generate_config(client)[
            server_key
        ]["snp-wiki"]
        assert written[server_key]["scout"] == exporter.generate_config(client)[
            server_key
        ]["scout"]
    assert "unrelated" in json.loads(cursor_path.read_text())["mcpServers"]
    assert "unrelated" in json.loads(vscode_path.read_text())["servers"]


def test_vscode_uses_workspace_config_path_and_native_schema() -> None:
    assert exporter.CLIENT_CONFIG_PATHS["vscode"] == ".vscode/mcp.json"
    config = exporter.generate_config("vscode")
    assert set(config) == {"servers"}
    assert "mcpServers" not in config


def test_claude_uses_portable_claude_code_project_config() -> None:
    assert exporter.CLIENT_CONFIG_PATHS["claude"] == ".mcp.json"


def test_all_client_write_failure_rolls_back_every_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = {
        client: str(tmp_path / f"{client}.json")
        for client in exporter.SUPPORTED_CLIENTS
    }
    monkeypatch.setattr(exporter, "CLIENT_CONFIG_PATHS", paths)
    originals: dict[str, bytes] = {}
    for client, target in paths.items():
        content = json.dumps({"original": client}).encode()
        Path(target).write_bytes(content)
        originals[target] = content

    real_replace = os.replace
    calls = 0

    def fail_second_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic destination failure")
        real_replace(source, destination)

    monkeypatch.setattr("scripts.export_mcp_config.os.replace", fail_second_replace)

    assert exporter.main(["--all"]) == 1
    assert {target: Path(target).read_bytes() for target in paths.values()} == originals


def test_invalid_existing_json_fails_without_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "cursor.json"
    target.write_bytes(b"not-json")
    monkeypatch.setitem(exporter.CLIENT_CONFIG_PATHS, "cursor", str(target))
    assert exporter.main(["--client", "cursor"]) == 1
    assert target.read_bytes() == b"not-json"


def test_unknown_client_rejected() -> None:
    with pytest.raises(ValueError):
        exporter.generate_config("unknown")
