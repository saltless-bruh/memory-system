"""tests/test_agent_package.py — Verification suite for portable agent distribution package."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "packages" / "snp-agent"
INSTALLER_SCRIPT = REPO_ROOT / "scripts" / "install-agent.sh"


def test_package_structure_exists() -> None:
    """Verify core package directories exist."""
    assert (PACKAGE_DIR / "rules").is_dir()
    assert (PACKAGE_DIR / "instructions").is_dir()
    assert (PACKAGE_DIR / "workflows").is_dir()
    assert (PACKAGE_DIR / "skills").is_dir()


def test_package_rules_and_instructions() -> None:
    """Verify rule and instruction files exist and are non-empty."""
    rule_file = PACKAGE_DIR / "rules" / "snp-memory.md"
    assert rule_file.is_file()
    assert "Rule R-5" in rule_file.read_text(encoding="utf-8")

    expected_instructions = [
        "agent_guide.instructions.md",
        "frontmatter_schema.instructions.md",
        "query_protocol.instructions.md",
    ]
    for instr_name in expected_instructions:
        instr_file = PACKAGE_DIR / "instructions" / instr_name
        assert instr_file.is_file()
        assert len(instr_file.read_text(encoding="utf-8").strip()) > 50


def test_package_workflows_frontmatter() -> None:
    """Verify all workflows in packages/snp-agent/workflows have valid YAML frontmatter."""
    expected_workflows = [
        "snp-query.md",
        "snp-compile.md",
        "snp-ingest.md",
        "snp-verify.md",
        "snp-heal.md",
        "snp-reload.md",
    ]
    for wf_name in expected_workflows:
        wf_path = PACKAGE_DIR / "workflows" / wf_name
        assert wf_path.is_file(), f"Missing workflow: {wf_name}"

        content = wf_path.read_text(encoding="utf-8")
        assert content.startswith("---"), f"{wf_name} missing frontmatter header"
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{wf_name} malformed frontmatter"

        meta = yaml.safe_load(parts[1])
        assert isinstance(meta, dict), f"{wf_name} metadata is not a dict"
        assert "description" in meta, f"{wf_name} missing 'description' field"
        assert len(meta["description"].strip()) > 10


def test_package_skills_frontmatter() -> None:
    """Verify all 8 domain skills in packages/snp-agent/skills have valid SKILL.md frontmatter."""
    expected_skills = [
        "snp-auto-heal-vault",
        "snp-bootstrap-system",
        "snp-compile-wiki",
        "snp-export-mcp",
        "snp-ingest-raw-data",
        "snp-rag-fetch",
        "snp-search-wiki",
        "snp-verify-vault",
    ]
    for skill_name in expected_skills:
        skill_file = PACKAGE_DIR / "skills" / skill_name / "SKILL.md"
        assert skill_file.is_file(), f"Missing SKILL.md for {skill_name}"

        content = skill_file.read_text(encoding="utf-8")
        assert content.startswith("---"), f"{skill_name} missing frontmatter header"
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{skill_name} malformed frontmatter"

        meta = yaml.safe_load(parts[1])
        assert isinstance(meta, dict)
        assert meta.get("name") == skill_name
        assert "description" in meta


def test_installer_fresh_installation(tmp_path: Path) -> None:
    """Test install-agent.sh in a clean, empty directory."""
    res = subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Successfully Installed" in res.stdout

    # Verify directory structure
    agent_dir = tmp_path / ".agent"
    assert (agent_dir / "rules" / "snp-memory.md").is_file()
    assert (agent_dir / "instructions" / "agent_guide.instructions.md").is_file()
    assert (agent_dir / "workflows" / "snp-query.md").is_file()
    assert (agent_dir / "workflows" / "snp-reload.md").is_file()
    assert (agent_dir / "skills" / "snp-search-wiki" / "SKILL.md").is_file()
    assert (tmp_path / ".mcp.json").is_file()


def test_installer_non_destructive_merge(tmp_path: Path) -> None:
    """Test install-agent.sh merges into an existing .agent without overwriting custom rules."""
    agent_dir = tmp_path / ".agent"
    (agent_dir / "rules").mkdir(parents=True)
    custom_rule = agent_dir / "rules" / "custom-team-rules.md"
    custom_rule.write_text("# My Custom Team Rules", encoding="utf-8")

    # Run installer
    subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    # Verify custom rule was preserved
    assert custom_rule.is_file()
    assert custom_rule.read_text(encoding="utf-8") == "# My Custom Team Rules"

    # Verify SNP rules were added
    assert (agent_dir / "rules" / "snp-memory.md").is_file()
    assert (agent_dir / "workflows" / "snp-query.md").is_file()


def test_installer_idempotent(tmp_path: Path) -> None:
    """Test that running install-agent.sh multiple times is idempotent."""
    subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)], check=True, capture_output=True
    )
    res2 = subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Successfully Installed" in res2.stdout


def test_installer_nested_path(tmp_path: Path) -> None:
    """Test installer against deeply nested directory that does not exist yet."""
    deep_path = tmp_path / "deeply" / "nested" / "target" / "workspace"
    subprocess.run(
        [str(INSTALLER_SCRIPT), str(deep_path)], check=True, capture_output=True
    )
    assert (deep_path / ".agent" / "rules" / "snp-memory.md").is_file()
    assert (deep_path / ".mcp.json").is_file()


def test_package_and_root_skills_synchronized() -> None:
    """Verify all 8 domain skills in packages/snp-agent/skills match .agent/skills/."""
    root_skills = sorted(
        p.name for p in (REPO_ROOT / ".agent" / "skills").glob("snp-*")
    )
    pkg_skills = sorted(p.name for p in PACKAGE_DIR.glob("skills/snp-*"))
    assert root_skills == pkg_skills
    assert len(pkg_skills) == 8


# ── the three config surfaces must not drift (T2.1) ───────────────────────


def _manifest_servers(root: Path) -> dict[str, dict[str, object]]:
    """MCP servers now live in `mcp.json`, never inline in the manifest."""
    import json

    data = json.loads((root / "mcp.json").read_text(encoding="utf-8"))
    servers: dict[str, dict[str, object]] = data["mcpServers"]
    return servers


def test_the_manifest_and_the_exporter_describe_the_same_servers() -> None:
    """Two files describing the same thing must not be allowed to disagree.

    They already had: the manifest listed the two read paths and the exporter
    listed the same two, and neither knew about the local authoring server. An
    agent installed from this package could search and fetch but not compile.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    import scripts.export_mcp_config as exporter

    exported = set(exporter.generate_config("claude")["mcpServers"])
    # Only the package declares servers: `.agent/` is this repository's working
    # contract, not a distributable plugin.
    assert set(_manifest_servers(PACKAGE_DIR)) == exported


def test_the_manifest_lists_the_local_servers_real_tools() -> None:
    """`requiredTools` has to name tools the servers actually serve.

    Both in-process servers are covered here. `snp-wiki` runs in a container and
    is covered by tests/integration/test_wiki_tool_surface_live.py, because
    reaching it needs Docker and this suite stays hermetic. The split is named
    in plugin.json's own note so a reader can check the claim rather than
    trust it -- an earlier version of that note said every server was verified
    while only `snpmemory` was, and `list_notes` sat in the manifest unserved.
    """
    import asyncio
    import json
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from scout.auth import AuthConfig, AuthMode, CallerIdentity
    from scout.mcp.local_server import build_server
    from scout.mcp_server import build_server as build_scout

    declared = json.loads((PACKAGE_DIR / "plugin.json").read_text(encoding="utf-8"))[
        "extensions"
    ]["io.snp.memory"]["requiredTools"]

    served_local = {tool.name for tool in asyncio.run(build_server().list_tools())}
    assert set(declared["snpmemory"]) == served_local

    class _NullBackend:
        async def retrieve(self, *args: object, **kwargs: object) -> list[object]:
            return []

    scout_config = AuthConfig(
        mode=AuthMode.DEVELOPMENT,
        provider=None,
        development_identity=CallerIdentity(
            subject="manifest-check",
            departments=frozenset({"ai_eng"}),
            auth_mode=AuthMode.DEVELOPMENT,
        ),
    )
    served_scout = {
        tool.name
        for tool in asyncio.run(build_scout(_NullBackend(), auth_config=scout_config).list_tools())
    }
    assert set(declared["scout"]) == served_scout


def test_the_local_server_is_declared_stdio_and_carries_no_url() -> None:
    """It has the authority of whoever launches it; a URL would publish that."""
    local = _manifest_servers(PACKAGE_DIR)["snpmemory"]
    # Agent Plugins types the transport rather than leaving it free text.
    assert local["type"] == "stdio"
    assert "url" not in local
    assert local["command"] == "snpmemory"


def test_the_installer_scaffolds_all_three_servers(tmp_path: Path) -> None:
    """The surface an installed agent actually reads."""
    import json

    subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)], check=True, capture_output=True
    )
    scaffolded = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))

    assert set(scaffolded["mcpServers"]) == {"snp-wiki", "scout", "snpmemory"}
    local = scaffolded["mcpServers"]["snpmemory"]
    assert local["command"] == "snpmemory"
    assert local["args"] == ["mcp", "--root", str(REPO_ROOT)]
