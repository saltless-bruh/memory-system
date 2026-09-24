"""Authenticated MCP client exporter safety and CLI behavior tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

import scripts.export_mcp_config as exporter

LEGACY_CLIENTS = ("cursor", "vscode", "claude", "gemini")


@pytest.mark.parametrize("client", LEGACY_CLIENTS)
def test_generated_config_exposes_only_v3_servers_and_authenticates_scout(
    client: str,
) -> None:
    config = exporter.generate_config(client)
    server_key = "servers" if client == "vscode" else "mcpServers"
    servers = config[server_key]
    assert set(servers) == {"snpmemory"}
    scout = servers["snpmemory"]

    if client == "vscode":
        assert scout["type"] == "stdio"

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
    scout = exporter.generate_config(client)[server_key]["snpmemory"]
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
        json.dumps(
            {
                "mcpServers": {
                    "unrelated": {"url": "http://other"},
                    "snp-wiki": {"url": "http://localhost:8765/mcp"},
                }
            }
        ),
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
        server_key = {"vscode": "servers", "opencode": "mcp"}.get(client, "mcpServers")
        assert "snp-wiki" not in written[server_key]
        assert (
            written[server_key]["snpmemory"]
            == exporter.generate_config(client)[server_key]["snpmemory"]
        )
    assert "unrelated" in json.loads(cursor_path.read_text())["mcpServers"]
    assert "snp-wiki" not in json.loads(cursor_path.read_text())["mcpServers"]
    assert "unrelated" in json.loads(vscode_path.read_text())["servers"]


def test_vscode_uses_workspace_config_path_and_native_schema() -> None:
    assert exporter.CLIENT_CONFIG_PATHS["vscode"] == ".vscode/mcp.json"
    config = exporter.generate_config("vscode")
    assert set(config) == {"servers"}
    assert "mcpServers" not in config


def test_claude_uses_portable_claude_code_project_config() -> None:
    assert exporter.CLIENT_CONFIG_PATHS["claude"] == ".mcp.json"


@pytest.mark.parametrize("fail_at", [2, len(exporter.SUPPORTED_CLIENTS)])
def test_all_client_write_failure_rolls_back_every_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_at: int
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

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("synthetic destination failure")
        real_replace(source, destination)

    monkeypatch.setattr("scripts.export_mcp_config.os.replace", fail_replace)

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


def test_opencode_uses_native_authenticated_scout_only(tmp_path: Path) -> None:
    assert exporter.CLIENT_CONFIG_PATHS["opencode"] == "opencode.json"
    assert exporter.generate_config("opencode", root=tmp_path) == {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "snpmemory": {
                "type": "remote",
                "url": "http://127.0.0.1:8080/mcp",
                "oauth": False,
                "timeout": 15000,
                "headers": {"Authorization": "{env:SCOUT_AUTH_HEADER}"},
            }
        },
    }


def test_opencode_merge_preserves_settings_and_removes_managed_local_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "opencode.json"
    secret = "Bearer secret-for-someone-else"
    unrelated = {
        "type": "remote",
        "url": "http://other.example/mcp",
        "headers": {"Authorization": secret},
    }
    target.write_text(
        json.dumps(
            {
                "$schema": "https://example.com/custom-schema.json",
                "model": "custom/model",
                "instructions": ["custom-rules.md"],
                "permission": {"bash": "ask"},
                "mcp": {
                    "other": unrelated,
                    "disabled": {"enabled": False},
                    # Every name this project has ever used, each planted as
                    # the shape it really had: the live remote server under its
                    # old key, the deleted stdio server, and basic-memory. A
                    # merge has to end with exactly one of them.
                    "scout": {"type": "remote", "url": "http://127.0.0.1:8080/mcp"},
                    "snpmemory": {"type": "local", "command": ["snpmemory", "mcp"]},
                    "snp-wiki": {"type": "remote", "url": "http://retired.example"},
                },
            }
        ),
        encoding="utf-8",
    )

    assert exporter.main(["--client", "opencode"]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["$schema"] == "https://example.com/custom-schema.json"
    assert written["model"] == "custom/model"
    assert written["instructions"] == ["custom-rules.md"]
    assert written["permission"] == {"bash": "ask"}
    assert set(written["mcp"]) == {"snpmemory", "other", "disabled"}
    assert written["mcp"]["other"] == unrelated
    assert written["mcp"]["disabled"] == {"enabled": False}
    assert written["mcp"]["snpmemory"]["type"] == "remote"
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    # Repeated export does not accumulate or reorder managed entries.
    before = target.read_bytes()
    assert exporter.main(["--client", "opencode"]) == 0
    assert target.read_bytes() == before
    output = capsys.readouterr()
    assert secret not in output.out + output.err


@pytest.mark.parametrize(
    "original",
    [
        b"",
        b" \n",
        b"\v{}",
        b"[]",
        b"null",
        b'{"mcp": null}',
        b'{"mcp": []}',
        b'{"mcp": false}',
        b'{"mcp": "Bearer sensitive-value"}',
        b'{"mcp": {}, "mcp": {}}',
        b'{"temperature": NaN}',
        b'{"temperature": Infinity}',
        b'{"temperature": 1e9999}',
        b'{"setting": "Bearer sensitive-value", invalid}',
    ],
)
def test_malformed_opencode_aborts_all_exports_before_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    original: bytes,
) -> None:
    paths = {
        client: str(tmp_path / f"{client}.json")
        for client in exporter.SUPPORTED_CLIENTS
    }
    monkeypatch.setattr(exporter, "CLIENT_CONFIG_PATHS", paths)
    originals = {}
    for client, target in paths.items():
        content = original if client == "opencode" else b'{"setting": "keep"}\n'
        Path(target).write_bytes(content)
        originals[target] = content

    assert exporter.main(["--all"]) == 1
    assert {target: Path(target).read_bytes() for target in paths.values()} == originals
    assert not list(tmp_path.glob("*.tmp"))
    output = capsys.readouterr()
    assert "sensitive-value" not in output.out + output.err


@pytest.mark.parametrize("json_exists", [False, True])
def test_existing_jsonc_blocks_all_exports_without_being_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    json_exists: bool,
) -> None:
    paths = {
        client: str(tmp_path / f"{client}.json")
        for client in exporter.SUPPORTED_CLIENTS
    }
    monkeypatch.setattr(exporter, "CLIENT_CONFIG_PATHS", paths)
    jsonc = tmp_path / "opencode.jsonc"
    jsonc.write_text("// Bearer private-jsonc-value\n{}\n", encoding="utf-8")
    target = Path(paths["opencode"])
    if json_exists:
        target.write_text('{"model":"keep"}', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    real_read_text = Path.read_text

    def refuse_jsonc_read(path: Path, *args: Any, **kwargs: Any) -> str:
        assert path != jsonc, "JSONC conflict detection must not read its contents"
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse_jsonc_read)
    assert exporter.main(["--all"]) == 1
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    output = capsys.readouterr()
    assert "opencode.jsonc" in output.err
    assert "private-jsonc-value" not in output.out + output.err


def test_dangling_jsonc_symlink_is_also_a_conflict(tmp_path: Path) -> None:
    sibling = tmp_path / "opencode.jsonc"
    try:
        sibling.symlink_to(tmp_path / "missing.jsonc")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(exporter.ConfigTargetConflict):
        exporter.validate_config_target("opencode", tmp_path / "opencode.json")
    assert sibling.is_symlink()


def test_print_opencode_does_not_inspect_existing_configs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "opencode.json").write_text("not JSON", encoding="utf-8")
    (tmp_path / "opencode.jsonc").write_text("// keep\n{}", encoding="utf-8")
    assert exporter.main(["--client", "opencode", "--print"]) == 0
    assert json.loads(capsys.readouterr().out)["mcp"]["snpmemory"]["oauth"] is False


def test_export_can_write_without_posix_only_fchmod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(os, "fchmod", raising=False)
    assert exporter.main(["--client", "opencode"]) == 0
    target = tmp_path / "opencode.json"
    assert (
        json.loads(target.read_text(encoding="utf-8"))["mcp"]["snpmemory"]["type"]
        == "remote"
    )
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600


# ── one server, and the retired one removed (leaf-4.3) ────────────────────


@pytest.mark.parametrize("client", LEGACY_CLIENTS)
def test_every_client_gets_exactly_one_server(client: str) -> None:
    """The exported surface is the authenticated Scout connection and nothing else.

    Until leaf-4.3 this also wrote a `snpmemory` stdio entry that launched a
    second MCP server from the user's own shell. That server is deleted, so an
    entry naming it would advertise tools that cannot start.
    """
    server_key = "servers" if client == "vscode" else "mcpServers"
    servers = exporter.generate_config(client)[server_key]
    assert set(servers) == {"snpmemory"}


def test_opencode_gets_exactly_one_server() -> None:
    assert set(exporter.generate_config("opencode")["mcp"]) == {"snpmemory"}


@pytest.mark.parametrize("client", LEGACY_CLIENTS)
def test_the_root_argument_no_longer_pins_a_checkout(
    client: str, tmp_path: Path
) -> None:
    """`root` survives as an accepted argument and changes nothing.

    `snpmemory mcp-config` still passes it. It pinned the deleted server's
    checkout; a generated config that varied by caller now would mean some
    entry still carries a path.
    """
    server_key = "servers" if client == "vscode" else "mcpServers"
    assert (
        exporter.generate_config(client, root=tmp_path)[server_key]
        == exporter.generate_config(client)[server_key]
    )


def test_no_generated_entry_launches_a_local_process_from_a_checkout() -> None:
    """Measured over every client, not asserted over a list of names."""
    for client in exporter.SUPPORTED_CLIENTS:
        config = exporter.generate_config(client)
        key = (
            "mcp"
            if client == "opencode"
            else ("servers" if client == "vscode" else "mcpServers")
        )
        for name, entry in config[key].items():
            argv = [entry.get("command", ""), *entry.get("args", [])]
            assert "mcp" not in argv[1:], f"{client}/{name} still launches a server"
            assert str(exporter.REPO_ROOT) not in " ".join(argv), (
                f"{client}/{name} still pins a checkout"
            )


@pytest.mark.parametrize("client", LEGACY_CLIENTS)
def test_an_upgrade_removes_the_deleted_stdio_entry(client: str) -> None:
    """A config written before leaf-4.3 must be corrected, not shadowed.

    The stale entry names a real installed command (`snpmemory`) with a
    subcommand that no longer exists, so leaving it registered gives the client
    a server whose launch fails at use time rather than at install time.
    """
    server_key = "servers" if client == "vscode" else "mcpServers"
    existing = {
        server_key: {
            "snpmemory": {"command": "snpmemory", "args": ["mcp", "--root", "/old"]},
            "unrelated": {"command": "somebody-elses-server"},
        }
    }
    merged = exporter.merge_configs(existing, exporter.generate_config(client))
    assert set(merged[server_key]) == {"snpmemory", "unrelated"}


def test_an_opencode_upgrade_removes_the_deleted_stdio_entry() -> None:
    existing = {
        "mcp": {
            "snpmemory": {"command": "snpmemory", "args": ["mcp"]},
            "unrelated": {"type": "remote", "url": "http://example.invalid/mcp"},
        }
    }
    merged = exporter.merge_configs(existing, exporter.generate_config("opencode"))
    assert set(merged["mcp"]) == {"snpmemory", "unrelated"}


def test_the_exporter_and_the_server_agree_on_the_server_name() -> None:
    """Two files naming the same server must not be allowed to disagree.

    `scripts/export_mcp_config.py` repeats the name rather than importing it:
    that module has to stay runnable on a machine with nothing installed, and
    `scout.mcp_server` pulls in fastmcp. The repetition is deliberate, so the
    agreement has to be enforced somewhere, and this is the only place it is.

    A disagreement would not raise anywhere. The exporter would write one key,
    the server would answer to another, and every generated config would point
    a client at a server name the server never claims.
    """
    from scout.mcp_server import SERVER_NAME

    assert exporter.SERVER_NAME == SERVER_NAME


def test_the_supersession_set_contains_the_name_in_use() -> None:
    """Removal is derived as `_OWN_SERVER_NAMES - generated`, which only works
    if the generated name is a member. Drop it and the set becomes a list of
    dead names again -- the shape that let `scout` be orphaned by the rename.
    """
    assert exporter.SERVER_NAME in exporter._OWN_SERVER_NAMES
    for client in exporter.SUPPORTED_CLIENTS:
        key = {"opencode": "mcp", "vscode": "servers"}.get(client, "mcpServers")
        generated = set(exporter.generate_config(client)[key])
        assert generated <= exporter._OWN_SERVER_NAMES, generated
