"""CLI tool for bundling, installing, verifying, and synchronizing SNP Agent Packages.

Supports:
- Bundling into distributable tar.gz archives (dist/snp-agent-v2.0.0.tar.gz)
- Bidirectional syncing between packages/snp-agent/ and .agent/
- Deploying configuration to Cursor, Claude Code, Gemini CLI, VS Code, and Antigravity
- Validating package manifest schemas and frontmatters
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.export_mcp_config import (  # noqa: E402
    _load_existing,
    generate_config,
    merge_configs,
)

DEFAULT_PACKAGE_DIR = REPO_ROOT / "packages" / "snp-agent"

#: The Agent Plugins release this package targets. Pinned rather than accepted
#: loosely: the schema identifiers are immutable and a new release must use a
#: new one, so a mismatch is a migration to make, not a version to tolerate.
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
DEFAULT_AGENT_DIR = REPO_ROOT / ".agent"
DEFAULT_CLAUDE_DIR = REPO_ROOT / ".claude"
DEFAULT_DIST_DIR = REPO_ROOT / "dist"
CONTRACT_SUBDIRS = ("instructions", "rules", "skills", "workflows")
RETIRED_COMPONENTS = (
    Path("skills/snp-auto-heal-vault"),
    Path("workflows/snp-heal.md"),
)


class PackageError(Exception):
    """Raised when package verification, bundling, or installation fails."""


def load_manifest(package_dir: Path) -> dict[str, Any]:
    """Load and validate `plugin.json` (Agent Plugins 1.0.0).

    Replaces the bespoke `manifest.json`, whose shape only this repository's own
    tests understood. Two fields the old manifest carried are gone by design and
    not by omission: MCP servers now live in `mcp.json`, because the
    specification says they are "never inline in the manifest"; and
    `entrypoints` moved under the project's `extensions` namespace, because
    `skills/` is a fixed location the specification already defines.
    """
    manifest_path = package_dir / "plugin.json"
    if not manifest_path.is_file():
        raise PackageError(f"Missing plugin.json in {package_dir}")
    try:
        data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"Invalid JSON in plugin.json: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError("plugin.json must contain a JSON object")
    for key in ("$schema", "name"):
        if key not in data:
            raise PackageError(f"plugin.json missing required field '{key}'")
    if data["$schema"] != PLUGIN_SCHEMA:
        raise PackageError(
            f"plugin.json targets {data['$schema']!r}; this tooling implements "
            f"{PLUGIN_SCHEMA!r}"
        )
    return data


def verify_package(package_dir: Path) -> bool:
    """Verify manifest, schemas, and frontmatter across the package."""
    manifest = load_manifest(package_dir)
    version = manifest.get("version", "unversioned")
    print(f"✓ Valid plugin.json: {manifest['name']} v{version}")

    # Verify skills frontmatter
    skills_dir = package_dir / "skills"
    if skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                raise PackageError(f"Missing SKILL.md in {skill_dir}")
            content = skill_md.read_text(encoding="utf-8")
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if not match:
                raise PackageError(
                    f"SKILL.md in {skill_dir.name} missing YAML frontmatter"
                )
            frontmatter = yaml.safe_load(match.group(1))
            if (
                not isinstance(frontmatter, dict)
                or "name" not in frontmatter
                or "description" not in frontmatter
            ):
                raise PackageError(
                    f"SKILL.md in {skill_dir.name} has invalid frontmatter schema"
                )
            print(f"  ✓ Skill: {skill_dir.name}")

    # Verify workflows frontmatter
    workflows_dir = package_dir / "workflows"
    if workflows_dir.is_dir():
        for wf_file in workflows_dir.glob("*.md"):
            content = wf_file.read_text(encoding="utf-8")
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if not match:
                raise PackageError(f"Workflow {wf_file.name} missing YAML frontmatter")
            frontmatter = yaml.safe_load(match.group(1))
            if not isinstance(frontmatter, dict) or "description" not in frontmatter:
                raise PackageError(f"Workflow {wf_file.name} missing 'description'")
            print(f"  ✓ Workflow: /{wf_file.stem}")

    print("✓ Package verification PASSED with 0 errors.")
    return True


def sync_packages(
    source_dir: Path,
    target_dir: Path,
    *,
    filter_snp_only: bool = False,
    prune_retired: bool = False,
) -> list[Path]:
    """Synchronize files from source_dir to target_dir."""
    if not source_dir.is_dir():
        raise PackageError(f"Source directory does not exist: {source_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)
    if prune_retired:
        remove_retired_components(target_dir)
    synced_files: list[Path] = []

    for src_file in source_dir.rglob("*"):
        if src_file.is_file():
            rel_path = src_file.relative_to(source_dir)
            if any(
                rel_path == retired or retired in rel_path.parents
                for retired in RETIRED_COMPONENTS
            ):
                continue
            if filter_snp_only:
                # If filtering, only copy snp-* or manifest files
                parts = rel_path.parts
                if not (
                    rel_path.name in ("plugin.json", "mcp.json", "package.json")
                    or (len(parts) > 1 and parts[1].startswith("snp-"))
                    or (parts[0] in ("rules", "instructions"))
                ):
                    continue

            dst_file = target_dir / rel_path
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if not dst_file.exists() or dst_file.read_bytes() != src_file.read_bytes():
                shutil.copy2(src_file, dst_file)
                synced_files.append(dst_file)

    print(
        f"✓ Synchronized {len(synced_files)} file(s) from {source_dir.name}/ -> {target_dir.name}/"
    )
    return synced_files


def remove_retired_components(target_dir: Path) -> list[Path]:
    """Remove only the two SNP-owned components retired by V3.

    This is intentionally not a generic mirror delete: custom skills,
    workflows, and repository-local development files must survive upgrades.
    """
    removed: list[Path] = []
    for relative in RETIRED_COMPONENTS:
        if relative.is_absolute() or ".." in relative.parts:
            raise PackageError(f"unsafe retired component path: {relative}")
        target = target_dir / relative
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(target)
        elif target.exists():
            target.unlink()
            removed.append(target)
    return removed


def sync_contract_mirrors(
    package_dir: Path = DEFAULT_PACKAGE_DIR,
    agent_dir: Path = DEFAULT_AGENT_DIR,
    claude_dir: Path = DEFAULT_CLAUDE_DIR,
) -> list[Path]:
    """Sync portable files to `.agent` and active contract files to `.claude`."""
    synced = sync_packages(
        package_dir,
        agent_dir,
        prune_retired=True,
    )
    remove_retired_components(claude_dir)
    for subdir in CONTRACT_SUBDIRS:
        synced.extend(sync_packages(package_dir / subdir, claude_dir / subdir))

    manifest = load_manifest(package_dir)
    repo_local = manifest["extensions"]["io.snp.memory"]["repoLocal"]
    for name in repo_local["instructions"]:
        source = agent_dir / "instructions" / name
        if not source.is_file():
            raise PackageError(f"Missing repo-local instruction: {source}")
        target = claude_dir / "instructions" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
            synced.append(target)
    return synced


def sync_declared_agent_package(
    agent_dir: Path,
    package_dir: Path,
) -> list[Path]:
    """Copy only manifest-declared portable files from `.agent` to a package."""
    manifest = load_manifest(package_dir)
    ships = manifest["extensions"]["io.snp.memory"]["ships"]
    relative_files = {
        Path("package.json"),
        Path("plugin.json"),
        Path("mcp.json"),
        *(Path("instructions") / name for name in ships["instructions"]),
        *(Path("rules") / name for name in ships["rules"]),
        *(Path("workflows") / name for name in ships["workflows"]),
    }
    for skill in ships["skills"]:
        skill_root = agent_dir / "skills" / skill
        relative_files.update(
            path.relative_to(agent_dir)
            for path in skill_root.rglob("*")
            if path.is_file()
        )

    remove_retired_components(package_dir)
    synced: list[Path] = []
    for relative in sorted(relative_files):
        source = agent_dir / relative
        if not source.is_file():
            raise PackageError(f"Missing declared agent file: {source}")
        target = package_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
            synced.append(target)
    return synced


def bundle_package(package_dir: Path, output_dir: Path = DEFAULT_DIST_DIR) -> Path:
    """Create a distributable .tar.gz bundle from package_dir."""
    verify_package(package_dir)
    manifest = load_manifest(package_dir)
    version = manifest.get("version", "1.0.0")
    name = manifest.get("name", "snp-agent").replace("@", "").replace("/", "-")

    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / f"{name}-v{version}.tar.gz"

    with tarfile.open(bundle_path, "w:gz") as tar:
        for file_path in package_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(package_dir)
                tar.add(file_path, arcname=str(arcname))

    print(
        f"✓ Successfully built distribution bundle: {bundle_path} ({bundle_path.stat().st_size} bytes)"
    )
    return bundle_path


def install_to_client(
    package_dir: Path,
    target_client: str,
    base_dir: Path = REPO_ROOT,
) -> dict[str, Path]:
    """Deploy client configuration files for Cursor, Claude, Gemini, VS Code, or Antigravity."""
    verify_package(package_dir)
    created_files: dict[str, Path] = {}
    target = target_client.lower().strip()

    # Client MCP configuration comes from the shared generator, never a second
    # copy maintained here.
    project_config_paths = {
        "cursor": base_dir / ".cursor" / "mcp.json",
        "claude": base_dir / ".mcp.json",
        "vscode": base_dir / ".vscode" / "mcp.json",
    }

    if target in project_config_paths:
        mcp_path = project_config_paths[target]
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        # Merge, so servers this project knows nothing about survive. Replacing
        # the file outright is data loss for anyone who already had one.
        merged = merge_configs(_load_existing(mcp_path), generate_config(target))
        mcp_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        created_files[f"{target}_mcp"] = mcp_path

    elif target == "antigravity":
        agent_dir = base_dir / ".agent"
        sync_packages(package_dir, agent_dir, prune_retired=True)
        created_files["antigravity_agent"] = agent_dir

    elif target not in project_config_paths:
        raise PackageError(
            f"Unsupported target client: '{target}'. Supported: cursor, claude, vscode, antigravity"
        )

    for _name, path in created_files.items():
        print(f"✓ Configured {target_client} target: {path}")
    return created_files


def main(argv: list[str] | None = None) -> int:
    """CLI Entrypoint."""
    parser = argparse.ArgumentParser(description="SNP Agent Package Manager")
    parser.add_argument(
        "--verify", action="store_true", help="Verify package manifest and schema"
    )
    parser.add_argument(
        "--bundle", action="store_true", help="Build distributable tar.gz archive"
    )
    parser.add_argument(
        "--sync", action="store_true", help="Synchronize packages/snp-agent <-> .agent"
    )
    parser.add_argument(
        "--direction",
        choices=["packages-to-agent", "agent-to-packages"],
        default="packages-to-agent",
        help="Direction for synchronization (default: packages-to-agent)",
    )
    parser.add_argument(
        "--install",
        metavar="CLIENT",
        help="Install agent config for target client (cursor, claude, vscode, antigravity)",
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=DEFAULT_PACKAGE_DIR,
        help="Path to package directory",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Path to output dist directory",
    )

    args = parser.parse_args(argv)

    try:
        if args.verify:
            verify_package(args.package_dir)
        elif args.bundle:
            bundle_package(args.package_dir, args.dist_dir)
        elif args.sync:
            if args.direction == "packages-to-agent":
                sync_contract_mirrors(args.package_dir)
            else:
                sync_declared_agent_package(DEFAULT_AGENT_DIR, args.package_dir)
        elif args.install:
            install_to_client(args.package_dir, args.install)
        else:
            # Default action: verify & display manifest
            verify_package(args.package_dir)
        return 0
    except PackageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
