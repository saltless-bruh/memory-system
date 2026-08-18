import json
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

import scripts.export_mcp_config as export_module
from scripts.export_mcp_config import generate_config, main, merge_configs


def test_generate_config_cursor() -> None:
    cfg = generate_config("cursor")
    assert "snp-wiki" in cfg["mcpServers"]
    assert cfg["mcpServers"]["snp-wiki"]["url"] == "http://localhost:8765/mcp"


def test_generate_config_gemini() -> None:
    cfg = generate_config("gemini")
    assert "snp-wiki" in cfg["mcpServers"]
    assert cfg["mcpServers"]["snp-wiki"]["httpUrl"] == "http://localhost:8765/mcp"


def test_generate_config_claude() -> None:
    cfg = generate_config("claude")
    assert "snp-wiki" in cfg["mcpServers"]
    assert cfg["mcpServers"]["snp-wiki"]["command"] == "npx"
    assert "mcp-remote" in cfg["mcpServers"]["snp-wiki"]["args"]


def test_generate_config_invalid() -> None:
    with pytest.raises(ValueError):
        generate_config("invalid")


def test_merge_configs() -> None:
    existing: dict[str, Any] = {"mcpServers": {"other": {"url": "http://other"}}}
    new_cfg = generate_config("cursor")
    merged = merge_configs(existing, new_cfg)
    assert "other" in merged["mcpServers"]
    assert "snp-wiki" in merged["mcpServers"]


def test_main_print(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["export_mcp_config.py", "--client", "cursor", "--print"]
    )
    main()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["mcpServers"]["snp-wiki"]["url"] == "http://localhost:8765/mcp"


def test_main_print_gemini(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["export_mcp_config.py", "--client", "gemini", "--print"]
    )
    main()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["mcpServers"]["snp-wiki"]["httpUrl"] == "http://localhost:8765/mcp"


def test_main_print_all_clients(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["export_mcp_config.py", "--print"])
    main()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "cursor" in output
    assert "vscode" in output
    assert "claude" in output
    assert "gemini" in output
    assert output["cursor"]["mcpServers"]["snp-wiki"]["url"] == "http://localhost:8765/mcp"
    assert output["gemini"]["mcpServers"]["snp-wiki"]["httpUrl"] == "http://localhost:8765/mcp"


def test_main_non_interactive_no_client(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["export_mcp_config.py"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    main()
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "cursor" in output
    assert "gemini" in output


def test_main_write_file(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    target_file = tmp_path / "settings.json"

    monkeypatch.setitem(export_module.CLIENT_CONFIG_PATHS, "gemini", str(target_file))

    monkeypatch.setattr("sys.argv", ["export_mcp_config.py", "--client", "gemini"])

    main()

    assert target_file.exists()
    content = json.loads(target_file.read_text(encoding="utf-8"))
    assert "snp-wiki" in content["mcpServers"]
    assert content["mcpServers"]["snp-wiki"]["httpUrl"] == "http://localhost:8765/mcp"


def test_main_write_file_merge(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    target_file = tmp_path / "settings.json"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        '{"mcpServers": {"existing": {"url": "http://exist"}}}', encoding="utf-8"
    )

    monkeypatch.setitem(export_module.CLIENT_CONFIG_PATHS, "gemini", str(target_file))

    monkeypatch.setattr("sys.argv", ["export_mcp_config.py", "--client", "gemini"])

    main()

    content = json.loads(target_file.read_text(encoding="utf-8"))
    assert "existing" in content["mcpServers"]
    assert "snp-wiki" in content["mcpServers"]

