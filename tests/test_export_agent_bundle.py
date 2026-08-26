"""Tests for scripts/export_agent_bundle.py CLI and utilities."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from scripts.export_agent_bundle import (
    DEFAULT_PACKAGE_DIR,
    PackageError,
    bundle_package,
    install_to_client,
    load_manifest,
    main,
    sync_packages,
    verify_package,
)


def test_load_manifest_valid() -> None:
    """The manifest is now `plugin.json` (Agent Plugins 1.0.0)."""
    data = load_manifest(DEFAULT_PACKAGE_DIR)
    assert data["name"] == "snp-memory-agent"
    assert data["version"] == "2.0.0"


def test_load_manifest_missing(tmp_path: Path) -> None:
    """Test error on missing manifest."""
    with pytest.raises(PackageError, match="Missing plugin.json"):
        load_manifest(tmp_path)


def test_load_manifest_invalid_json(tmp_path: Path) -> None:
    """Test error on invalid JSON manifest."""
    (tmp_path / "plugin.json").write_text("invalid json", encoding="utf-8")
    with pytest.raises(PackageError, match="Invalid JSON"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_a_different_spec_version(tmp_path: Path) -> None:
    """Schema identifiers are immutable; a new release must use a new one.

    So a mismatch is a migration to make, not a version to tolerate silently.
    """
    import json as _json

    (tmp_path / "plugin.json").write_text(
        _json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json",
                "name": "x",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageError, match="this tooling implements"):
        load_manifest(tmp_path)


def test_verify_package_passes() -> None:
    """Test verification passes on DEFAULT_PACKAGE_DIR."""
    assert verify_package(DEFAULT_PACKAGE_DIR) is True


def test_bundle_package(tmp_path: Path) -> None:
    """Test bundle creation generates valid tar.gz with relative paths."""
    out_dir = tmp_path / "dist"
    bundle_path = bundle_package(DEFAULT_PACKAGE_DIR, output_dir=out_dir)
    assert bundle_path.is_file()
    assert bundle_path.suffix == ".gz"

    with tarfile.open(bundle_path, "r:gz") as tar:
        names = tar.getnames()
        assert "plugin.json" in names
        assert "package.json" in names
        assert any(n.startswith("skills/") for n in names)
        assert any(n.startswith("workflows/") for n in names)


def test_sync_packages(tmp_path: Path) -> None:
    """Test synchronizing package tree to a target directory."""
    target_dir = tmp_path / "synced"
    synced = sync_packages(DEFAULT_PACKAGE_DIR, target_dir)
    assert len(synced) > 0
    assert (target_dir / "plugin.json").is_file()
    assert (target_dir / "plugin.json").read_bytes() == (
        DEFAULT_PACKAGE_DIR / "plugin.json"
    ).read_bytes()

    # Re-running sync with no changes should return empty list
    second_sync = sync_packages(DEFAULT_PACKAGE_DIR, target_dir)
    assert len(second_sync) == 0


def test_install_to_cursor(tmp_path: Path) -> None:
    """Test installing config for Cursor."""
    created = install_to_client(DEFAULT_PACKAGE_DIR, "cursor", base_dir=tmp_path)
    assert "cursor_mcp" in created
    cursor_mcp = created["cursor_mcp"]
    assert cursor_mcp.is_file()
    data = json.loads(cursor_mcp.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    # `snp-wiki`, not `basic-memory`: the key a client config gives a server is
    # the agent's tool namespace, and every other surface already used this one.
    assert "snp-wiki" in data["mcpServers"]
    assert "scout" in data["mcpServers"]


def test_install_to_claude(tmp_path: Path) -> None:
    """Test installing config for Claude Code."""
    created = install_to_client(DEFAULT_PACKAGE_DIR, "claude", base_dir=tmp_path)
    assert "claude_mcp" in created
    claude_mcp = created["claude_mcp"]
    assert claude_mcp.is_file()
    data = json.loads(claude_mcp.read_text(encoding="utf-8"))
    assert "mcpServers" in data


def test_install_to_vscode(tmp_path: Path) -> None:
    """Test installing config for VS Code."""
    created = install_to_client(DEFAULT_PACKAGE_DIR, "vscode", base_dir=tmp_path)
    assert "vscode_mcp" in created
    vscode_mcp = created["vscode_mcp"]
    assert vscode_mcp.is_file()
    data = json.loads(vscode_mcp.read_text(encoding="utf-8"))
    assert "servers" in data


def test_install_unsupported_target(tmp_path: Path) -> None:
    """Test error on unsupported client target."""
    with pytest.raises(PackageError, match="Unsupported target client"):
        install_to_client(DEFAULT_PACKAGE_DIR, "unknown_client", base_dir=tmp_path)


def test_main_cli_flags(tmp_path: Path) -> None:
    """Test main() CLI flags."""
    assert main(["--verify"]) == 0
    assert main(["--bundle", "--dist-dir", str(tmp_path / "dist")]) == 0
    assert (tmp_path / "dist").is_dir()
    assert main(["--sync"]) == 0


# ── F-3: the fifth config surface must not have its own opinion ───────────


@pytest.mark.parametrize("client", ["cursor", "claude", "vscode"])
def test_the_bundle_installer_emits_what_the_one_generator_emits(
    client: str, tmp_path: Path
) -> None:
    """This file used to carry its own copy of every client config.

    It named the wiki server `basic-memory` and authenticated with
    `SCOUT_AUTH_TOKEN`, while the documentation, `export_mcp_config.py` and
    `install-agent.sh` all used `snp-wiki` and `SCOUT_AUTH_HEADER`. A user who
    followed the documentation got a config from this path that could not
    authenticate against Scout at all.
    """
    import json

    import scripts.export_agent_bundle as bundle
    import scripts.export_mcp_config as exporter

    bundle.install_to_client(DEFAULT_PACKAGE_DIR, client, base_dir=tmp_path)

    written = {
        "cursor": tmp_path / ".cursor" / "mcp.json",
        "claude": tmp_path / ".mcp.json",
        "vscode": tmp_path / ".vscode" / "mcp.json",
    }[client]
    produced = json.loads(written.read_text(encoding="utf-8"))
    key = "servers" if client == "vscode" else "mcpServers"

    assert produced[key] == exporter.generate_config(client)[key]
    assert set(produced[key]) == {"snp-wiki", "scout", "snpmemory"}


@pytest.mark.parametrize("client", ["cursor", "claude", "vscode"])
def test_the_bundle_installer_uses_the_documented_auth_variable(
    client: str, tmp_path: Path
) -> None:
    """`docs/CONNECT_AGENTS.md` tells the user to export SCOUT_AUTH_HEADER."""
    import scripts.export_agent_bundle as bundle

    bundle.install_to_client(DEFAULT_PACKAGE_DIR, client, base_dir=tmp_path)
    written = {
        "cursor": tmp_path / ".cursor" / "mcp.json",
        "claude": tmp_path / ".mcp.json",
        "vscode": tmp_path / ".vscode" / "mcp.json",
    }[client].read_text(encoding="utf-8")

    assert "SCOUT_AUTH_HEADER" in written
    assert "SCOUT_AUTH_TOKEN" not in written


def test_installing_preserves_servers_this_project_does_not_own(
    tmp_path: Path,
) -> None:
    """Replacing a config outright is data loss for anyone who already had one."""
    import json

    import scripts.export_agent_bundle as bundle

    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"theirs": {"url": "http://x"}}, "keep": True}),
        encoding="utf-8",
    )

    bundle.install_to_client(DEFAULT_PACKAGE_DIR, "claude", base_dir=tmp_path)

    merged = json.loads(target.read_text(encoding="utf-8"))
    assert merged["keep"] is True
    assert merged["mcpServers"]["theirs"] == {"url": "http://x"}
