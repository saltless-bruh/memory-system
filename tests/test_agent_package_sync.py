"""Tests for agent package synchronization, manifest validity, and skill schema compliance."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "packages" / "snp-agent"
AGENT_DIR = REPO_ROOT / ".agent"
CLAUDE_DIR = REPO_ROOT / ".claude"

# `.agent/` is the authoritative agent contract. `.claude/` mirrors these four
# subtrees byte-for-byte so Claude Code reads exactly what every other agent
# reads; `manifest.json` / `package.json` stay out of `.claude/` because they
# are distribution metadata for the portable bundle, not client config.
MIRRORED_CONTRACT_DIRS = ("instructions", "rules", "skills", "workflows")

# Root-level files whose divergence README.md and docs/ARCHITECTURE_STATUS.md
# explicitly promise cannot happen.
REQUIRED_SHARED_ROOT_FILES = ("manifest.json", "package.json")

# Empty agent-config directories that must never reappear in the repo root.
# `~` is what an unquoted/unexpanded tilde in a shell command leaves behind.
FORBIDDEN_ROOT_DIRS = ("~", ".agents", ".codex")


def _relative_files(root: Path) -> set[Path]:
    """Return every real file under root, relative to it, ignoring build junk."""
    if not root.is_dir():
        return set()
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }


def test_package_manifest_validity() -> None:
    """Validate manifest.json in packages/snp-agent and .agent."""
    for root in (PACKAGE_DIR, AGENT_DIR):
        manifest_path = root / "manifest.json"
        assert manifest_path.is_file(), f"Missing manifest.json at {manifest_path}"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["name"] == "@snp/memory-agent"
        assert data["version"] == "2.0.0"
        assert "mcpServers" in data
        assert "basic-memory" in data["mcpServers"]
        assert "scout" in data["mcpServers"]
        assert "entrypoints" in data
        assert "compatibility" in data


def test_package_json_validity() -> None:
    """Validate package.json in packages/snp-agent and .agent."""
    for root in (PACKAGE_DIR, AGENT_DIR):
        pkg_path = root / "package.json"
        assert pkg_path.is_file(), f"Missing package.json at {pkg_path}"
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
        assert data["name"] == "@snp/memory-agent"
        assert data["version"] == "2.0.0"
        assert data["type"] == "module"


def test_packages_to_agent_parity() -> None:
    """Every file in packages/snp-agent must exist byte-identical in .agent.

    README.md and docs/ARCHITECTURE_STATUS.md promise that the mirrored files
    stay equivalent. `.agent/` is a superset (it also carries the superpowers-*
    skills and workflows, which are not distributed), so the shared set is
    exactly the packages/snp-agent file list.
    """
    assert PACKAGE_DIR.is_dir(), f"Package dir missing: {PACKAGE_DIR}"
    assert AGENT_DIR.is_dir(), f"Agent dir missing: {AGENT_DIR}"

    package_files = _relative_files(PACKAGE_DIR)
    agent_files = _relative_files(AGENT_DIR)

    # Guard against a vacuous pass if either tree is emptied or relocated.
    assert len(package_files) >= 20, (
        f"packages/snp-agent looks truncated: only {len(package_files)} file(s)"
    )

    missing = sorted(str(rel) for rel in package_files - agent_files)
    assert not missing, f"Present in packages/snp-agent but absent from .agent/: {missing}"

    mismatched = sorted(
        str(rel)
        for rel in package_files
        if (PACKAGE_DIR / rel).read_bytes() != (AGENT_DIR / rel).read_bytes()
    )
    assert not mismatched, (
        "Byte mismatch between packages/snp-agent/ and .agent/ for: "
        f"{mismatched}. Re-run scripts/export_agent_bundle.py --sync."
    )


def test_shared_root_metadata_is_byte_identical() -> None:
    """manifest.json and package.json must be shared and identical in both trees.

    These sit outside skills/ and workflows/, so the per-component mirror tests
    never reach them; they are the files that actually drifted.
    """
    shared = _relative_files(PACKAGE_DIR) & _relative_files(AGENT_DIR)
    for name in REQUIRED_SHARED_ROOT_FILES:
        rel = Path(name)
        assert rel in shared, f"{name} must exist in BOTH .agent/ and packages/snp-agent/"
        assert (PACKAGE_DIR / rel).read_bytes() == (AGENT_DIR / rel).read_bytes(), (
            f"{name} differs between packages/snp-agent/ and .agent/"
        )


def test_agent_snp_components_mirrored_in_package() -> None:
    """Every file of every snp-* skill and workflow in .agent must be in the package.

    This is the reverse direction of `test_packages_to_agent_parity`: it catches
    a supporting file (reference, script, template) added under an `.agent/`
    snp-* skill and never shipped in the portable bundle.
    """
    agent_skills = AGENT_DIR / "skills"
    assert agent_skills.is_dir(), f"Missing {agent_skills}"
    mirrored_skills = 0
    for skill_dir in sorted(agent_skills.iterdir()):
        if not (skill_dir.is_dir() and skill_dir.name.startswith("snp-")):
            continue
        mirrored_skills += 1
        pkg_skill_dir = PACKAGE_DIR / "skills" / skill_dir.name
        assert pkg_skill_dir.is_dir(), (
            f"Skill {skill_dir.name} in .agent/skills missing from packages/snp-agent/skills"
        )
        skill_files = _relative_files(skill_dir)
        assert Path("SKILL.md") in skill_files, f"{skill_dir.name} has no SKILL.md"
        for rel in sorted(skill_files):
            pkg_file = pkg_skill_dir / rel
            assert pkg_file.is_file(), (
                f".agent/skills/{skill_dir.name}/{rel} missing from packages/snp-agent/"
            )
            assert (skill_dir / rel).read_bytes() == pkg_file.read_bytes(), (
                f"Content mismatch for skills/{skill_dir.name}/{rel}"
            )
    assert mirrored_skills == 8, f"Expected 8 snp-* skills, found {mirrored_skills}"

    agent_workflows = AGENT_DIR / "workflows"
    assert agent_workflows.is_dir(), f"Missing {agent_workflows}"
    mirrored_workflows = 0
    for wf_file in sorted(agent_workflows.iterdir()):
        if not (wf_file.is_file() and wf_file.name.startswith("snp-")):
            continue
        if wf_file.suffix != ".md":
            continue
        mirrored_workflows += 1
        pkg_wf = PACKAGE_DIR / "workflows" / wf_file.name
        assert pkg_wf.is_file(), (
            f"Workflow {wf_file.name} in .agent/workflows missing from "
            "packages/snp-agent/workflows"
        )
        assert wf_file.read_bytes() == pkg_wf.read_bytes()
    assert mirrored_workflows == 6, (
        f"Expected 6 snp-* workflows, found {mirrored_workflows}"
    )


def test_claude_mirrors_agent_contract() -> None:
    """`.claude/` must be a byte-for-byte mirror of the `.agent/` contract.

    Claude Code reads `.claude/`; every other agent client reads `.agent/`. If
    they diverge, a Claude agent silently operates under a different contract.
    `.agent/` is authoritative — resync `.claude/` from it, never the reverse.
    """
    assert AGENT_DIR.is_dir(), f"Agent dir missing: {AGENT_DIR}"
    assert CLAUDE_DIR.is_dir(), (
        f"{CLAUDE_DIR} is missing; it must be a tracked mirror of .agent/"
    )

    problems: list[str] = []
    total_mirrored = 0
    for subdir in MIRRORED_CONTRACT_DIRS:
        agent_sub = AGENT_DIR / subdir
        claude_sub = CLAUDE_DIR / subdir
        assert agent_sub.is_dir(), f"Missing .agent/{subdir}/"
        assert claude_sub.is_dir(), f"Missing .claude/{subdir}/"

        agent_files = _relative_files(agent_sub)
        claude_files = _relative_files(claude_sub)
        total_mirrored += len(agent_files)

        problems += [
            f".agent/{subdir}/{rel} is not mirrored in .claude/"
            for rel in sorted(agent_files - claude_files)
        ]
        problems += [
            f".claude/{subdir}/{rel} has no .agent/ counterpart"
            for rel in sorted(claude_files - agent_files)
        ]
        problems += [
            f"{subdir}/{rel} differs between .agent/ and .claude/"
            for rel in sorted(agent_files & claude_files)
            if (agent_sub / rel).read_bytes() != (claude_sub / rel).read_bytes()
        ]

    assert total_mirrored >= 20, (
        f"Contract mirror looks truncated: only {total_mirrored} file(s)"
    )
    assert not problems, (
        "`.claude/` has drifted from the authoritative `.agent/` contract:\n  "
        + "\n  ".join(problems)
        + "\nResync with: for d in "
        + " ".join(MIRRORED_CONTRACT_DIRS)
        + '; do rsync -a --delete ".agent/$d/" ".claude/$d/"; done'
    )


def test_no_stray_root_agent_directories() -> None:
    """The repo root must stay free of the empty agent-config directories.

    `~` in particular is the residue of a shell command whose tilde was quoted
    or otherwise left unexpanded; it is never intentional.
    """
    stray = [name for name in FORBIDDEN_ROOT_DIRS if (REPO_ROOT / name).exists()]
    assert not stray, (
        f"Stray directories in the repo root: {stray}. Remove them "
        "(`rmdir` them if empty) and quote/expand paths correctly in whatever "
        "created them."
    )


def test_skill_frontmatter_schema() -> None:
    """Ensure all SKILL.md files in packages/snp-agent have valid YAML frontmatter."""
    skills_dir = PACKAGE_DIR / "skills"
    assert skills_dir.is_dir()
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        assert skill_file.is_file(), f"SKILL.md missing in {skill_dir}"
        content = skill_file.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match is not None, f"SKILL.md in {skill_dir.name} missing YAML frontmatter delimiters"
        frontmatter = yaml.safe_load(match.group(1))
        assert isinstance(frontmatter, dict)
        assert "name" in frontmatter, f"SKILL.md in {skill_dir.name} missing 'name' in frontmatter"
        assert frontmatter["name"] == skill_dir.name, (
            f"Frontmatter name '{frontmatter['name']}' does not match directory '{skill_dir.name}'"
        )
        assert "description" in frontmatter, f"SKILL.md in {skill_dir.name} missing 'description'"
        assert len(frontmatter["description"].strip()) > 10


def test_workflows_frontmatter_schema() -> None:
    """Ensure all workflow .md files in packages/snp-agent have valid description frontmatter."""
    workflows_dir = PACKAGE_DIR / "workflows"
    assert workflows_dir.is_dir()
    for wf_file in workflows_dir.glob("*.md"):
        content = wf_file.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match is not None, f"Workflow {wf_file.name} missing YAML frontmatter delimiters"
        frontmatter = yaml.safe_load(match.group(1))
        assert isinstance(frontmatter, dict)
        assert "description" in frontmatter, f"Workflow {wf_file.name} missing 'description'"
