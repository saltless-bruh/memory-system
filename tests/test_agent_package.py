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
