"""tests/test_agent_package.py — Verification suite for portable agent distribution package."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
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
    rule_text = rule_file.read_text(encoding="utf-8")
    assert rule_text.index("wiki_search") < rule_text.index("wiki_read")
    assert "R-8.5" in rule_text

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
    """Verify all seven domain skills have valid SKILL.md frontmatter."""
    expected_skills = [
        "snp-bootstrap-system",
        "snp-compile-wiki",
        "snp-export-mcp",
        "snp-ingest-raw-data",
        "snp-query-wiki",
        "snp-read-wiki-page",
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
    assert (agent_dir / "skills" / "snp-read-wiki-page" / "SKILL.md").is_file()
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
    """Verify all seven domain skills match .agent/skills/."""
    root_skills = sorted(
        p.name for p in (REPO_ROOT / ".agent" / "skills").glob("snp-*")
    )
    pkg_skills = sorted(p.name for p in PACKAGE_DIR.glob("skills/snp-*"))
    assert root_skills == pkg_skills
    assert len(pkg_skills) == 7


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

    Both active servers are built in-process here, keeping this check hermetic.
    """
    import asyncio
    import json
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from scout.auth import AuthConfig, AuthMode, CallerIdentity
    from scout.mcp.local_server import build_server
    from scout.mcp_server import build_server as build_scout
    from scout.types import RagChunk, Scope

    declared = json.loads((PACKAGE_DIR / "plugin.json").read_text(encoding="utf-8"))[
        "extensions"
    ]["io.snp.memory"]["requiredTools"]

    served_local = {tool.name for tool in asyncio.run(build_server().list_tools())}
    assert set(declared["snpmemory"]) == served_local

    class _NullBackend:
        async def retrieve(
            self,
            hint: str,
            *,
            path: str | None = None,
            scope: Scope | None = None,
            k: int = 10,
        ) -> Sequence[RagChunk]:
            del hint, path, scope, k
            return ()

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
        for tool in asyncio.run(
            build_scout(_NullBackend(), auth_config=scout_config).list_tools()
        )
    }
    assert set(declared["scout"]) == served_scout


def test_the_local_server_is_declared_stdio_and_carries_no_url() -> None:
    """It has the authority of whoever launches it; a URL would publish that."""
    local = _manifest_servers(PACKAGE_DIR)["snpmemory"]
    # Agent Plugins types the transport rather than leaving it free text.
    assert local["type"] == "stdio"
    assert "url" not in local
    assert local["command"] == "snpmemory"


def test_the_installer_scaffolds_exactly_the_v3_servers(tmp_path: Path) -> None:
    """The surface an installed agent actually reads."""
    import json

    subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)], check=True, capture_output=True
    )
    scaffolded = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))

    assert set(scaffolded["mcpServers"]) == {"scout", "snpmemory"}
    local = scaffolded["mcpServers"]["snpmemory"]
    assert local["command"] == "snpmemory"
    assert local["args"] == ["mcp", "--root", str(REPO_ROOT)]


def test_installer_removes_retired_components_but_preserves_custom_files(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".agent"
    retired_skill = agent_dir / "skills" / "snp-auto-heal-vault"
    retired_skill.mkdir(parents=True)
    (retired_skill / "SKILL.md").write_text("stale", encoding="utf-8")
    retired_workflow = agent_dir / "workflows" / "snp-heal.md"
    retired_workflow.parent.mkdir(parents=True)
    retired_workflow.write_text("stale", encoding="utf-8")
    # A rename is additive over an existing tree: left in place, the old
    # directories give the agent four retrieval skills, two of them naming a
    # retired tool and describing the pre-envelope response shape.
    renamed = [
        agent_dir / "skills" / "snp-rag-fetch",
        agent_dir / "skills" / "snp-search-wiki",
    ]
    for directory in renamed:
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("stale", encoding="utf-8")
    custom = agent_dir / "skills" / "custom-team" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom", encoding="utf-8")

    subprocess.run(
        [str(INSTALLER_SCRIPT), str(tmp_path)], check=True, capture_output=True
    )

    assert not retired_skill.exists()
    assert not retired_workflow.exists()
    assert [d for d in renamed if d.exists()] == []
    installed = sorted(d.name for d in (agent_dir / "skills").glob("snp-*"))
    assert installed == [
        "snp-bootstrap-system",
        "snp-compile-wiki",
        "snp-export-mcp",
        "snp-ingest-raw-data",
        "snp-query-wiki",
        "snp-read-wiki-page",
        "snp-verify-vault",
    ]
    assert custom.read_text(encoding="utf-8") == "custom"


def test_manifest_repository_matches_the_real_remote() -> None:
    """A distributed manifest points people at a repository that exists."""
    import json

    manifest = json.loads((PACKAGE_DIR / "plugin.json").read_text())
    declared = manifest["repository"].rstrip("/").removesuffix(".git")

    remote = (
        subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        .stdout.strip()
        .rstrip("/")
        .removesuffix(".git")
    )
    if not remote:
        import pytest

        pytest.skip("no origin remote configured in this checkout")

    assert declared == remote, (
        f"plugin.json points at {declared} but origin is {remote}"
    )
