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
# reads; distribution metadata stays out of `.claude/` because it describes the
# portable bundle, not client config.
MIRRORED_CONTRACT_DIRS = ("instructions", "rules", "skills", "workflows")


def _claude_exclusions_from_plugin() -> tuple[
    tuple[str, ...], dict[str, tuple[str, ...]]
]:
    """Read the deliberate Claude-only carve-out from authoritative metadata."""
    data = json.loads((PACKAGE_DIR / "plugin.json").read_text(encoding="utf-8"))
    repo_local = data["extensions"]["io.snp.memory"]["repoLocal"]
    prefixes = tuple(repo_local["prefixes"])
    rules = tuple(repo_local["rules"])
    return prefixes, {"rules": rules}


# The one carve-out from that mirror. The `superpowers-*` layer is the
# development discipline of the *other* agent clients (Antigravity reads
# `.agent/`); Claude Code runs the `unlazy` gate discipline instead. Carrying
# both put two conflicting completion protocols — plan-gate plus gate-ledger —
# into a single Claude session, so the layer stays in `.agent/` and is absent
# from `.claude/`. This is narrower than `plugin.json`'s `repoLocal`, which
# means "not distributed": the repo-local *instructions* are still mirrored
# into `.claude/`, because Claude Code is meant to read those.
CLAUDE_EXCLUDED_PREFIXES, CLAUDE_EXCLUDED_FILES = _claude_exclusions_from_plugin()


def _is_claude_excluded(subdir: str, rel: Path) -> bool:
    """True when `subdir/rel` deliberately does not exist under `.claude/`."""
    if any(part.startswith(CLAUDE_EXCLUDED_PREFIXES) for part in rel.parts):
        return True
    return rel.name in CLAUDE_EXCLUDED_FILES.get(subdir, ())


# Root-level files whose divergence README.md and docs/ARCHITECTURE_STATUS.md
# explicitly promise cannot happen.
# `package.json` is npm metadata and is shared. `plugin.json` and `mcp.json`
# are deliberately NOT: they describe the *distribution*, and `.agent/` is this
# repository's working contract rather than a plugin. Since the superpowers
# layer is repo-local, the two trees legitimately differ, and a manifest
# claiming otherwise in both places would be the accident this tier removed.
REQUIRED_SHARED_ROOT_FILES = ("package.json",)

#: Files that belong to the distribution only.
PACKAGE_ONLY_ROOT_FILES = ("plugin.json", "mcp.json")

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
    """Validate `plugin.json` — the Agent Plugins 1.0.0 manifest."""
    manifest_path = PACKAGE_DIR / "plugin.json"
    assert manifest_path.is_file(), f"Missing plugin.json at {manifest_path}"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["name"] == "snp-memory-agent"
    assert data["version"] == "2.0.0"
    # The specification is explicit: MCP servers are declared in `mcp.json`,
    # never inline in the manifest.
    assert "mcpServers" not in data

    mcp_path = PACKAGE_DIR / "mcp.json"
    assert mcp_path.is_file(), f"Missing mcp.json at {mcp_path}"
    servers = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"]
    # `snp-wiki`, not `basic-memory`: an agent's tool namespace comes from the
    # key a client config gives a server, and every other surface uses this one.
    assert set(servers) == {"snp-wiki", "scout", "snpmemory"}

    project = data["extensions"]["io.snp.memory"]
    assert "entrypoints" in project
    assert "ships" in project


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

    # `plugin.json` / `mcp.json` describe the distribution and exist only in the
    # package; `.agent/` is this repository's working contract, not a plugin.
    package_files = _relative_files(PACKAGE_DIR) - {
        Path(name) for name in PACKAGE_ONLY_ROOT_FILES
    }
    agent_files = _relative_files(AGENT_DIR)

    # Guard against a vacuous pass if either tree is emptied or relocated. The
    # floor counts only the shared files: `plugin.json` and `mcp.json` are
    # excluded above, and `manifest.json` no longer exists.
    assert len(package_files) >= 18, (
        f"packages/snp-agent looks truncated: only {len(package_files)} file(s)"
    )

    missing = sorted(str(rel) for rel in package_files - agent_files)
    assert not missing, (
        f"Present in packages/snp-agent but absent from .agent/: {missing}"
    )

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
    """Shared root metadata must be identical in both trees.

    These sit outside skills/ and workflows/, so the per-component mirror tests
    never reach them; they are the files that actually drifted.
    """
    shared = _relative_files(PACKAGE_DIR) & _relative_files(AGENT_DIR)
    for name in REQUIRED_SHARED_ROOT_FILES:
        rel = Path(name)
        assert rel in shared, (
            f"{name} must exist in BOTH .agent/ and packages/snp-agent/"
        )
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
    """`.claude/` must mirror the `.agent/` contract byte-for-byte.

    Claude Code reads `.claude/`; every other agent client reads `.agent/`. If
    they diverge, a Claude agent silently operates under a different contract.
    `.agent/` is authoritative — resync `.claude/` from it, never the reverse.

    The sole exception is the `superpowers-*` layer, which is deliberately
    absent from `.claude/`; see `CLAUDE_EXCLUDED_PREFIXES`. That carve-out is
    itself verified below, so it cannot quietly widen into real drift.
    """
    assert AGENT_DIR.is_dir(), f"Agent dir missing: {AGENT_DIR}"
    assert CLAUDE_DIR.is_dir(), (
        f"{CLAUDE_DIR} is missing; it must be a tracked mirror of .agent/"
    )

    problems: list[str] = []
    total_mirrored = 0
    total_excluded = 0
    for subdir in MIRRORED_CONTRACT_DIRS:
        agent_sub = AGENT_DIR / subdir
        claude_sub = CLAUDE_DIR / subdir
        assert agent_sub.is_dir(), f"Missing .agent/{subdir}/"
        assert claude_sub.is_dir(), f"Missing .claude/{subdir}/"

        all_agent_files = _relative_files(agent_sub)
        excluded = {rel for rel in all_agent_files if _is_claude_excluded(subdir, rel)}
        total_excluded += len(excluded)

        agent_files = all_agent_files - excluded
        claude_files = _relative_files(claude_sub)
        total_mirrored += len(agent_files)

        # The carve-out is an absence, not a licence: anything excluded must be
        # genuinely gone from `.claude/`, or a stale copy keeps loading.
        problems += [
            f".claude/{subdir}/{rel} is excluded from the Claude tree but still present"
            for rel in sorted(excluded & claude_files)
        ]
        problems += [
            f".agent/{subdir}/{rel} is not mirrored in .claude/"
            for rel in sorted(agent_files - claude_files)
        ]
        problems += [
            f".claude/{subdir}/{rel} has no .agent/ counterpart"
            for rel in sorted(claude_files - agent_files - excluded)
        ]
        problems += [
            f"{subdir}/{rel} differs between .agent/ and .claude/"
            for rel in sorted(agent_files & claude_files)
            if (agent_sub / rel).read_bytes() != (claude_sub / rel).read_bytes()
        ]

    assert total_mirrored >= 20, (
        f"Contract mirror looks truncated: only {total_mirrored} file(s)"
    )
    # Guard the other side too: if the excluded layer vanishes from `.agent/`,
    # that is deletion of Antigravity's discipline, not a Claude scope decision.
    assert total_excluded >= 21, (
        f"the superpowers layer looks truncated in .agent/: {total_excluded} file(s)"
    )
    assert not problems, (
        "`.claude/` has drifted from the authoritative `.agent/` contract:\n  "
        + "\n  ".join(problems)
        + "\nResync with: for d in "
        + " ".join(MIRRORED_CONTRACT_DIRS)
        + "; do rsync -a --delete --exclude '"
        + "' --exclude '".join(f"{pre}*" for pre in CLAUDE_EXCLUDED_PREFIXES)
        + '\' ".agent/$d/" ".claude/$d/"; done'
        + "  (then remove any file named in CLAUDE_EXCLUDED_FILES)"
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
        assert match is not None, (
            f"SKILL.md in {skill_dir.name} missing YAML frontmatter delimiters"
        )
        frontmatter = yaml.safe_load(match.group(1))
        assert isinstance(frontmatter, dict)
        assert "name" in frontmatter, (
            f"SKILL.md in {skill_dir.name} missing 'name' in frontmatter"
        )
        assert frontmatter["name"] == skill_dir.name, (
            f"Frontmatter name '{frontmatter['name']}' does not match directory '{skill_dir.name}'"
        )
        assert "description" in frontmatter, (
            f"SKILL.md in {skill_dir.name} missing 'description'"
        )
        assert len(frontmatter["description"].strip()) > 10


def test_workflows_frontmatter_schema() -> None:
    """Ensure all workflow .md files in packages/snp-agent have valid description frontmatter."""
    workflows_dir = PACKAGE_DIR / "workflows"
    assert workflows_dir.is_dir()
    for wf_file in workflows_dir.glob("*.md"):
        content = wf_file.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match is not None, (
            f"Workflow {wf_file.name} missing YAML frontmatter delimiters"
        )
        frontmatter = yaml.safe_load(match.group(1))
        assert isinstance(frontmatter, dict)
        assert "description" in frontmatter, (
            f"Workflow {wf_file.name} missing 'description'"
        )


# ── the package ships what it says it ships (Tier 3, F-4) ─────────────────


def _declared() -> dict[str, object]:
    data = json.loads((PACKAGE_DIR / "plugin.json").read_text(encoding="utf-8"))
    ships: dict[str, object] = data["extensions"]["io.snp.memory"]["ships"]
    return ships


def test_the_package_ships_exactly_what_plugin_json_declares() -> None:
    """The check that actually closes the 25-file gap.

    Before this, parity was enforced `.agent` → `.claude` for four subtrees and
    `packages` → `.agent` for root files only. The package could lose any number
    of skills, workflows or instructions and no test would notice — which is how
    25 files went missing and stayed missing.
    """
    declared = _declared()

    actual_skills = sorted(
        d.name for d in (PACKAGE_DIR / "skills").iterdir() if (d / "SKILL.md").is_file()
    )
    assert actual_skills == sorted(declared["skills"]), (  # type: ignore[arg-type]
        "packages/snp-agent/skills does not match plugin.json's declared set"
    )

    for kind, subdir in (("workflows", "workflows"), ("instructions", "instructions")):
        actual = sorted(p.name for p in (PACKAGE_DIR / subdir).glob("*.md"))
        assert actual == sorted(declared[kind]), (  # type: ignore[arg-type]
            f"packages/snp-agent/{subdir} does not match plugin.json's declared set"
        )

    actual_rules = sorted(p.name for p in (PACKAGE_DIR / "rules").glob("*.md"))
    assert actual_rules == sorted(declared["rules"])  # type: ignore[arg-type]


def test_the_repo_local_layer_is_declared_and_genuinely_absent() -> None:
    """Repo-local by decision, not by accident — and the two must agree.

    The superpowers layer is this repository's development discipline, not part
    of the memory system's operating contract. Shipping it would tell a
    consumer's agent to write brainstorms and plans into *their*
    `artifacts/superpowers/`, for work unrelated to the memory system.
    """
    data = json.loads((PACKAGE_DIR / "plugin.json").read_text(encoding="utf-8"))
    repo_local = data["extensions"]["io.snp.memory"]["repoLocal"]
    prefixes = tuple(repo_local["prefixes"])
    assert prefixes, "the exclusion must name what it excludes"
    assert prefixes == CLAUDE_EXCLUDED_PREFIXES
    assert {"rules": tuple(repo_local["rules"])} == CLAUDE_EXCLUDED_FILES

    # Nothing carrying a repo-local prefix may appear in the distribution.
    leaked = [
        str(rel)
        for rel in _relative_files(PACKAGE_DIR)
        if any(part.startswith(prefixes) for part in rel.parts)
    ]
    assert not leaked, f"repo-local components leaked into the package: {leaked}"

    for name in repo_local["rules"]:
        assert not (PACKAGE_DIR / "rules" / name).exists()
        assert (AGENT_DIR / "rules" / name).is_file(), (
            f"{name} is declared repo-local but is missing from .agent/ too — "
            "that is deletion, not a scope decision"
        )
    for name in repo_local["instructions"]:
        assert not (PACKAGE_DIR / "instructions" / name).exists()
        assert (AGENT_DIR / "instructions" / name).is_file()

    # And the layer must actually exist where it was said to live.
    agent_superpowers = [
        d.name for d in (AGENT_DIR / "skills").iterdir() if d.name.startswith(prefixes)
    ]
    assert len(agent_superpowers) == 9, (
        f"expected 9 repo-local skills in .agent/skills, found {agent_superpowers}"
    )


def test_the_plugin_manifest_validates_against_agent_plugins_1_0_0() -> None:
    """Vendored schemas, like the CLI Spec. A spec revision is a test failure."""
    import jsonschema

    fixtures = REPO_ROOT / "tests" / "fixtures"
    for name, document in (
        ("plugin", PACKAGE_DIR / "plugin.json"),
        ("mcp", PACKAGE_DIR / "mcp.json"),
    ):
        schema = json.loads(
            (fixtures / f"agent-plugins-1.0.0-{name}.schema.json").read_text(
                encoding="utf-8"
            )
        )
        instance = json.loads(document.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(instance)


def test_the_mcp_declaration_matches_the_one_config_generator() -> None:
    """A sixth surface would be a sixth chance to drift."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    import scripts.export_mcp_config as exporter

    declared = json.loads((PACKAGE_DIR / "mcp.json").read_text(encoding="utf-8"))
    assert set(declared["mcpServers"]) == set(
        exporter.generate_config("claude")["mcpServers"]
    )
