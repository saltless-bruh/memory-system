"""Native installation must load the contract while preserving project content."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import install_opencode_agent as installer  # noqa: E402

PACKAGE = REPO_ROOT / "packages/snp-agent"
SHELL_INSTALLER = REPO_ROOT / "scripts/install-agent.sh"
EXPECTED_SKILLS = {
    "snp-bootstrap-system",
    "snp-compile-batch",
    "snp-compile-wiki",
    "snp-export-mcp",
    "snp-ingest-raw-data",
    "snp-plan-articles",
    "snp-query-wiki",
    "snp-read-wiki-page",
    "snp-verify-page",
    "snp-verify-vault",
}


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | str | None]]:
    """Capture file bytes, links, modes and empty directories without following links."""
    result = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        content = (
            os.readlink(path)
            if stat.S_ISLNK(mode)
            else path.read_bytes()
            if stat.S_ISREG(mode)
            else None
        )
        result[path.relative_to(root).as_posix()] = (mode, content)
    return result


def _shell(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SHELL_INSTALLER), "--client", "opencode", *args, "--", str(target)],
        env={**os.environ, "SNP_AGENT_PYTHON": sys.executable},
        cwd=target,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_fresh_shell_install_loads_every_skill_and_governing_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOUT_AUTH_HEADER", "Bearer do-not-write-this-secret")
    result = _shell(tmp_path)

    assert result.returncode == 0, result.stderr
    native = tmp_path / ".opencode"
    assert {path.name for path in (native / "skills").iterdir()} == EXPECTED_SKILLS
    for skill in EXPECTED_SKILLS:
        assert (native / "skills" / skill / "SKILL.md").read_bytes() == (
            PACKAGE / "skills" / skill / "SKILL.md"
        ).read_bytes()
    for folder in ("rules", "instructions", "workflows"):
        expected = {path.name for path in (PACKAGE / folder).glob("*.md")}
        assert {path.name for path in (native / "snp" / folder).iterdir()} == expected
        for name in expected:
            assert (native / "snp" / folder / name).read_bytes() == (
                PACKAGE / folder / name
            ).read_bytes()

    config = json.loads((tmp_path / "opencode.json").read_text())
    assert config["mcp"] == {
        "snpmemory": {
            "type": "remote",
            "url": "http://127.0.0.1:8080/mcp",
            "oauth": False,
            "timeout": 15000,
            "headers": {"Authorization": "{env:SCOUT_AUTH_HEADER}"},
        }
    }
    expected_instructions = {".opencode/snp/rules/snp-memory.md"} | {
        ".opencode/snp/instructions/" + path.name
        for path in (PACKAGE / "instructions").glob("*.md")
    }
    assert set(config["instructions"]) == expected_instructions
    assert len(config["instructions"]) == len(expected_instructions)
    assert set(config) == {"$schema", "mcp", "instructions"}
    assert not (native / "plugin.json").exists()
    assert not (native / "commands").exists()
    assert not (tmp_path / ".agent").exists()
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert "do-not-write-this-secret" not in (tmp_path / "opencode.json").read_text()
    assert "do-not-write-this-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("entry", ["helper", "shell"])
@pytest.mark.parametrize("existing", [False, True])
def test_dry_run_is_read_only_and_lists_exact_destinations(
    tmp_path: Path, entry: str, existing: bool
) -> None:
    if existing:
        installer.install_opencode_agent(tmp_path)
    before = _snapshot(tmp_path)

    if entry == "helper":
        result = installer.install_opencode_agent(tmp_path, dry_run=True)
        assert result["status"] == "dry_run"
        assert str(tmp_path / "opencode.json") in result["would_write"]
        assert (
            str(tmp_path / ".opencode/skills/snp-query-wiki/SKILL.md")
            in result["would_write"]
        )
    else:
        completed = _shell(tmp_path, "--dry-run")
        assert completed.returncode == 0, completed.stderr
        assert str(tmp_path / "opencode.json") in completed.stdout
    assert _snapshot(tmp_path) == before


def test_custom_only_content_config_and_legacy_contract_need_no_confirmation(
    tmp_path: Path,
) -> None:
    preserved = {
        "AGENTS.md": "# Human project instructions\n",
        ".agent/skills/my-skill/SKILL.md": "Legacy custom skill\n",
        ".agent/rules/snp-memory.md": "Legacy SNP rule\n",
        ".mcp.json": '{"mcpServers":{"legacy":{}}}\n',
        ".opencode/skills/custom/SKILL.md": "Custom native skill\n",
        ".opencode/snp/rules/custom.md": "Custom reference\n",
        ".opencode/commands/mine.md": "Existing command\n",
    }
    for relative, content in preserved.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    custom = {
        "model": "provider/existing-model",
        "permission": {"edit": "ask"},
        "plugin": ["existing-plugin"],
        "instructions": ["AGENTS.md", "custom/*.md"],
        "mcp": {
            "custom": {
                "type": "remote",
                "url": "https://example.invalid/mcp",
                "headers": {"Authorization": "Bearer preserve-custom-secret"},
            }
        },
    }
    (tmp_path / "opencode.json").write_text(json.dumps(custom))

    result = _shell(tmp_path)

    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / "opencode.json").read_text())
    for key in ("model", "permission", "plugin"):
        assert config[key] == custom[key]
    assert config["instructions"][:2] == custom["instructions"]
    assert config["mcp"]["custom"] == custom["mcp"]["custom"]
    for relative, content in preserved.items():
        assert (tmp_path / relative).read_text() == content
    assert "preserve-custom-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "owned",
    [
        "skill-directory",
        "skill-file",
        "rule",
        "instruction",
        "workflow",
        # One case per name this project has ever given a server of its own.
        # All three must be recognised as ours: the current one, and the two it
        # superseded. `scout` is the name the remote server itself answered to
        # until leaf-4.4, so an install that stopped recognising it would walk
        # past a live entry it owns.
        "current-config",
        "superseded-scout-config",
        "retired-local-config",
        "retired-wiki-config",
        "loaded-instruction",
    ],
)
def test_every_owned_collision_requires_confirmation_before_writes(
    tmp_path: Path, owned: str
) -> None:
    files = {
        "skill-file": ".opencode/skills/snp-query-wiki/SKILL.md",
        "rule": ".opencode/snp/rules/snp-memory.md",
        "instruction": ".opencode/snp/instructions/query_protocol.instructions.md",
        "workflow": ".opencode/snp/workflows/snp-query.md",
    }
    if owned == "skill-directory":
        (tmp_path / ".opencode/skills/snp-query-wiki").mkdir(parents=True)
    elif owned in files:
        destination = tmp_path / files[owned]
        destination.parent.mkdir(parents=True)
        destination.write_text("locally customized SNP content")
    else:
        # After leaf-4.4 the live server and the deleted stdio server share the
        # key `snpmemory`, so these two cases are told apart by shape rather
        # than by name: one is reached over the network, the other launched as
        # a subprocess. Both are ours and both must stop an unconfirmed write.
        planted = {
            "current-config": (
                "snpmemory",
                {"type": "remote", "url": "http://127.0.0.1:8080/mcp"},
            ),
            "superseded-scout-config": (
                "scout",
                {"type": "remote", "url": "http://127.0.0.1:8080/mcp"},
            ),
            "retired-local-config": (
                "snpmemory",
                {"type": "local", "command": ["snpmemory", "mcp"]},
            ),
            "retired-wiki-config": ("snp-wiki", {"command": "basic-memory"}),
        }.get(owned)
        config = (
            {"mcp": {planted[0]: planted[1]}}
            if planted
            else {"instructions": [".opencode/snp/rules/snp-memory.md"]}
        )
        (tmp_path / "opencode.json").write_text(json.dumps(config))
    before = _snapshot(tmp_path)

    result = _shell(tmp_path)

    assert result.returncode == 5
    assert "--confirm" in result.stderr
    assert _snapshot(tmp_path) == before


def test_confirmed_upgrade_reconciles_snp_and_is_repeatable(tmp_path: Path) -> None:
    installer.install_opencode_agent(tmp_path)
    custom = tmp_path / ".opencode/skills/snp-query-wiki/local-notes.md"
    custom.write_text("Keep my skill notes")
    config_path = tmp_path / "opencode.json"
    config = json.loads(config_path.read_text())
    config["mcp"].update(
        {"snpmemory": {}, "snp-wiki": {}, "custom": {"enabled": False}}
    )
    config["instructions"].append("custom.md")
    config_path.write_text(json.dumps(config))

    result = _shell(tmp_path, "--confirm")

    assert result.returncode == 0, result.stderr
    merged = json.loads(config_path.read_text())
    assert set(merged["mcp"]) == {"snpmemory", "custom"}
    assert merged["instructions"] == config["instructions"]
    assert custom.read_text() == "Keep my skill notes"
    before = _snapshot(tmp_path)
    result = _shell(tmp_path, "--confirm")
    assert result.returncode == 0, result.stderr
    assert _snapshot(tmp_path) == before


INVALID_CONFIGS = [
    b"{broken Bearer private-value",
    b"",
    b"  \n",
    b"[]",
    b"null",
    b'{"model":"first","model":"second"}',
    b'{"temperature":NaN}',
    b'{"temperature":Infinity}',
    b'{"temperature":1e999}',
    b'{"mcp":null}',
    b'{"mcp":[]}',
    b'{"mcp":"private-value"}',
    b'{"mcp":{"custom":false}}',
    b'{"instructions":null}',
    b'{"instructions":"private-value"}',
    b'{"instructions":{}}',
    b'{"instructions":[5]}',
    b'{"instructions":[""]}',
    b'{"instructions":[" "]}',
    b'\xff{"mcp":{}}',
]


@pytest.mark.parametrize("content", INVALID_CONFIGS)
@pytest.mark.parametrize("flags", [(), ("--confirm",), ("--dry-run",)])
def test_invalid_config_is_never_overridden_or_partially_installed(
    tmp_path: Path, content: bytes, flags: tuple[str, ...]
) -> None:
    (tmp_path / "opencode.json").write_bytes(content)
    before = _snapshot(tmp_path)

    result = _shell(tmp_path, *flags)

    assert result.returncode == 7
    assert _snapshot(tmp_path) == before
    assert "private-value" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("json_exists", [False, True])
@pytest.mark.parametrize("flags", [(), ("--confirm",), ("--dry-run",)])
def test_jsonc_sibling_requires_reconciliation_before_any_installation(
    tmp_path: Path, json_exists: bool, flags: tuple[str, ...]
) -> None:
    (tmp_path / "opencode.jsonc").write_text("// private-jsonc-value\n{}")
    if json_exists:
        (tmp_path / "opencode.json").write_text('{"model":"keep"}')
    before = _snapshot(tmp_path)

    result = _shell(tmp_path, *flags)

    assert result.returncode == 7
    assert "opencode.jsonc" in result.stderr
    assert "reconciled" in result.stderr
    assert _snapshot(tmp_path) == before
    assert "private-jsonc-value" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "opencode.json",
        "opencode.jsonc",
        ".opencode",
        ".opencode/skills",
        ".opencode/skills/snp-query-wiki",
        ".opencode/skills/snp-query-wiki/SKILL.md",
        ".opencode/snp",
        ".opencode/snp/rules",
        ".opencode/snp/rules/snp-memory.md",
        ".opencode/snp/instructions/query_protocol.instructions.md",
        ".opencode/snp/workflows/snp-query.md",
    ],
)
@pytest.mark.parametrize("dangling", [False, True])
def test_links_in_managed_destinations_are_refused_even_with_confirmation(
    tmp_path: Path, relative: str, dangling: bool
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    outside = tmp_path / "outside"
    if not dangling:
        outside.mkdir()
        (outside / "sentinel").write_text("must not change")
    link = target / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    before = _snapshot(tmp_path)

    result = _shell(target, "--confirm")

    assert result.returncode == 7
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "relative,directory",
    [
        ("opencode.json", True),
        (".opencode", False),
        (".opencode/skills", False),
        (".opencode/skills/snp-query-wiki", False),
        (".opencode/skills/snp-query-wiki/SKILL.md", True),
        (".opencode/snp", False),
        (".opencode/snp/instructions", False),
        (".opencode/snp/workflows/snp-query.md", True),
    ],
)
def test_incompatible_destination_types_fail_before_any_copy(
    tmp_path: Path, relative: str, directory: bool
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if directory:
        path.mkdir()
    else:
        path.write_text("preserve this file")
    before = _snapshot(tmp_path)

    result = _shell(tmp_path, "--confirm")

    assert result.returncode == 7
    assert _snapshot(tmp_path) == before


def test_skill_support_files_and_modes_are_copied(tmp_path: Path) -> None:
    package = tmp_path / "package"
    shutil.copytree(PACKAGE, package)
    support = package / "skills/snp-query-wiki/scripts/example.sh"
    support.parent.mkdir()
    support.write_text("#!/bin/sh\nexit 0\n")
    support.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()

    installer.install_opencode_agent(project, package=package)

    installed = project / ".opencode/skills/snp-query-wiki/scripts/example.sh"
    assert installed.read_bytes() == support.read_bytes()
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755


def test_incomplete_package_fails_before_writes(tmp_path: Path) -> None:
    package = tmp_path / "package"
    shutil.copytree(PACKAGE, package)
    (package / "skills/snp-query-wiki/SKILL.md").unlink()
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(installer.InstallError) as caught:
        installer.install_opencode_agent(project, package=package)

    assert caught.value.code == 2
    assert not list(project.iterdir())


@pytest.mark.parametrize("existing", [False, True])
def test_failed_replacement_rolls_back_new_files_config_and_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    if existing:
        installer.install_opencode_agent(tmp_path)
        rule = tmp_path / ".opencode/snp/rules/snp-memory.md"
        rule.write_text("old locally changed rule")
        rule.chmod(0o640)
    before = _snapshot(tmp_path)
    real_replace = os.replace
    count = 0

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", fail_once)

    with pytest.raises(installer.InstallError) as caught:
        installer.install_opencode_agent(tmp_path, confirm=True)

    assert caught.value.code == 2
    assert "restored" in str(caught.value)
    assert _snapshot(tmp_path) == before


def test_remote_shell_preview_does_not_fetch_or_create_a_staging_directory(
    tmp_path: Path,
) -> None:
    download = tmp_path / "download"
    download.mkdir()
    script = download / "install-agent.sh"
    shutil.copy2(SHELL_INSTALLER, script)
    project = tmp_path / "project"
    project.mkdir()
    before = _snapshot(tmp_path)

    result = subprocess.run(
        [str(script), str(project), "--client", "opencode", "--dry-run"],
        env={**os.environ, "SNP_AGENT_REPO": "/must-not-be-fetched"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "no package was fetched" in result.stdout
    assert _snapshot(tmp_path) == before
