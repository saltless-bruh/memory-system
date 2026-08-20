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
DEFAULT_PACKAGE_DIR = REPO_ROOT / "packages" / "snp-agent"
DEFAULT_AGENT_DIR = REPO_ROOT / ".agent"
DEFAULT_DIST_DIR = REPO_ROOT / "dist"


class PackageError(Exception):
    """Raised when package verification, bundling, or installation fails."""


def load_manifest(package_dir: Path) -> dict[str, Any]:
    """Load and validate manifest.json."""
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PackageError(f"Missing manifest.json in {package_dir}")
    try:
        data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PackageError("manifest.json must contain a JSON object")
        for key in ("name", "version", "mcpServers", "entrypoints"):
            if key not in data:
                raise PackageError(f"manifest.json missing required field '{key}'")
        return data
    except json.JSONDecodeError as exc:
        raise PackageError(f"Invalid JSON in manifest.json: {exc}") from exc


def verify_package(package_dir: Path) -> bool:
    """Verify manifest, schemas, and frontmatter across the package."""
    manifest = load_manifest(package_dir)
    print(f"✓ Valid manifest: {manifest['name']} v{manifest['version']}")

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
                raise PackageError(f"SKILL.md in {skill_dir.name} missing YAML frontmatter")
            frontmatter = yaml.safe_load(match.group(1))
            if not isinstance(frontmatter, dict) or "name" not in frontmatter or "description" not in frontmatter:
                raise PackageError(f"SKILL.md in {skill_dir.name} has invalid frontmatter schema")
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
) -> list[Path]:
    """Synchronize files from source_dir to target_dir."""
    if not source_dir.is_dir():
        raise PackageError(f"Source directory does not exist: {source_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)
    synced_files: list[Path] = []

    for src_file in source_dir.rglob("*"):
        if src_file.is_file():
            rel_path = src_file.relative_to(source_dir)
            if filter_snp_only:
                # If filtering, only copy snp-* or manifest files
                parts = rel_path.parts
                if not (
                    rel_path.name in ("manifest.json", "package.json")
                    or (len(parts) > 1 and parts[1].startswith("snp-"))
                    or (parts[0] in ("rules", "instructions"))
                ):
                    continue

            dst_file = target_dir / rel_path
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if not dst_file.exists() or dst_file.read_bytes() != src_file.read_bytes():
                shutil.copy2(src_file, dst_file)
                synced_files.append(dst_file)

    print(f"✓ Synchronized {len(synced_files)} file(s) from {source_dir.name}/ -> {target_dir.name}/")
    return synced_files


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

    print(f"✓ Successfully built distribution bundle: {bundle_path} ({bundle_path.stat().st_size} bytes)")
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

    if target == "cursor":
        cursor_dir = base_dir / ".cursor"
        cursor_dir.mkdir(parents=True, exist_ok=True)
        mcp_path = cursor_dir / "mcp.json"
        cursor_config = {
            "mcpServers": {
                "basic-memory": {
                    "url": "http://localhost:8765/mcp"
                },
                "scout": {
                    "url": "http://localhost:8080/mcp",
                    "headers": {
                        "Authorization": "Bearer ${env:SCOUT_AUTH_TOKEN}"
                    }
                }
            }
        }
        mcp_path.write_text(json.dumps(cursor_config, indent=2) + "\n", encoding="utf-8")
        created_files["cursor_mcp"] = mcp_path

    elif target == "claude":
        mcp_path = base_dir / ".mcp.json"
        claude_config = {
            "mcpServers": {
                "basic-memory": {
                    "url": "http://localhost:8765/mcp"
                },
                "scout": {
                    "url": "http://localhost:8080/mcp",
                    "headers": {
                        "Authorization": "Bearer ${SCOUT_AUTH_TOKEN}"
                    }
                }
            }
        }
        mcp_path.write_text(json.dumps(claude_config, indent=2) + "\n", encoding="utf-8")
        created_files["claude_mcp"] = mcp_path

    elif target == "vscode":
        vscode_dir = base_dir / ".vscode"
        vscode_dir.mkdir(parents=True, exist_ok=True)
        mcp_path = vscode_dir / "mcp.json"
        vscode_config = {
            "servers": {
                "basic-memory": {
                    "type": "http",
                    "url": "http://localhost:8765/mcp"
                },
                "scout": {
                    "type": "http",
                    "url": "http://localhost:8080/mcp",
                    "headers": {
                        "Authorization": "Bearer ${env:SCOUT_AUTH_TOKEN}"
                    }
                }
            }
        }
        mcp_path.write_text(json.dumps(vscode_config, indent=2) + "\n", encoding="utf-8")
        created_files["vscode_mcp"] = mcp_path

    elif target == "antigravity":
        agent_dir = base_dir / ".agent"
        sync_packages(package_dir, agent_dir)
        created_files["antigravity_agent"] = agent_dir

    else:
        raise PackageError(f"Unsupported target client: '{target}'. Supported: cursor, claude, vscode, antigravity")

    for _name, path in created_files.items():
        print(f"✓ Configured {target_client} target: {path}")
    return created_files


def main(argv: list[str] | None = None) -> int:
    """CLI Entrypoint."""
    parser = argparse.ArgumentParser(description="SNP Agent Package Manager")
    parser.add_argument("--verify", action="store_true", help="Verify package manifest and schema")
    parser.add_argument("--bundle", action="store_true", help="Build distributable tar.gz archive")
    parser.add_argument("--sync", action="store_true", help="Synchronize packages/snp-agent <-> .agent")
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
    parser.add_argument("--package-dir", type=Path, default=DEFAULT_PACKAGE_DIR, help="Path to package directory")
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR, help="Path to output dist directory")

    args = parser.parse_args(argv)

    try:
        if args.verify:
            verify_package(args.package_dir)
        elif args.bundle:
            bundle_package(args.package_dir, args.dist_dir)
        elif args.sync:
            if args.direction == "packages-to-agent":
                sync_packages(args.package_dir, DEFAULT_AGENT_DIR)
            else:
                sync_packages(DEFAULT_AGENT_DIR, args.package_dir, filter_snp_only=True)
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
