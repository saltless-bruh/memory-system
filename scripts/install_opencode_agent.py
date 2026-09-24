"""Install the portable SNP contract into OpenCode's native project layout.

The shell installer calls this stdlib-only helper. Planning is read-only and is
also used by the CLI wrapper, so malformed configs and target conflicts are
reported before any package file is copied. Workflows are reference guides;
this installer does not register plugins or slash commands.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.export_mcp_config import (
    ConfigTargetConflict,
    _load_existing,
    generate_config,
    merge_configs,
    validate_config_target,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallError(Exception):
    """A safe diagnostic with the same exit-code contract as snpmemory."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InstallFile:
    target: Path
    content: bytes
    mode: int


@dataclass(frozen=True)
class InstallPlan:
    target: Path
    package: Path
    files: tuple[InstallFile, ...]
    owned_targets: tuple[str, ...]
    servers: tuple[str, ...]

    def result(self, *, dry_run: bool) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": "dry_run" if dry_run else "installed",
            "client": "opencode",
            "target": str(self.target),
            "package": str(self.package),
        }
        if dry_run:
            data["would_write"] = [str(item.target) for item in self.files]
        else:
            data["servers"] = list(self.servers)
        return data


def _validate_destination(target: Path, path: Path, *, directory: bool = False) -> None:
    """Reject links and type conflicts along every managed destination path."""
    relative = path.relative_to(target)
    current = target
    for index, part in enumerate(relative.parts):
        current /= part
        if current.is_symlink():
            raise InstallError(7, f"managed destination is a symbolic link: {current}")
        is_directory = directory or index < len(relative.parts) - 1
        if current.exists() and (
            (is_directory and not current.is_dir())
            or (not is_directory and not current.is_file())
        ):
            raise InstallError(
                7, f"managed destination has an incompatible type: {current}"
            )


def _package_files(package: Path) -> tuple[list[Path], list[Path]]:
    """Select only the distributed contract, including skill support files."""
    sources: list[Path] = []
    for folder in ("rules", "instructions", "workflows"):
        selected = sorted((package / folder).glob("*.md"))
        if not selected or (package / folder).is_symlink():
            raise InstallError(2, f"agent package has no usable {folder} directory")
        sources.extend(selected)
    skills = sorted((package / "skills").glob("snp-*"))
    if not skills or (package / "skills").is_symlink():
        raise InstallError(2, "agent package has no usable SNP skills")
    for skill in skills:
        if skill.is_symlink() or not (skill / "SKILL.md").is_file():
            raise InstallError(2, f"agent package skill is incomplete: {skill.name}")
        for source in sorted(skill.rglob("*")):
            if source.is_symlink():
                raise InstallError(2, "agent package contains a symbolic link")
            if source.is_file():
                sources.append(source)
            elif not source.is_dir():
                raise InstallError(2, "agent package contains an unsupported file")
    if any(source.is_symlink() or not source.is_file() for source in sources):
        raise InstallError(2, "agent package contains an unsupported file")
    return sources, skills


def prepare_install(
    target: Path, package: Path = REPO_ROOT / "packages/snp-agent"
) -> InstallPlan:
    """Read and validate a complete install without creating directories or files."""
    target = target.expanduser().resolve()
    package = package.expanduser().resolve()
    if not target.is_dir():
        raise InstallError(3, "the target must be an existing project directory")

    config_path = target / "opencode.json"
    _validate_destination(target, config_path)
    try:
        validate_config_target("opencode", config_path)
        existing = _load_existing(config_path, strict=True)
        instructions = existing.get("instructions", [])
        if not isinstance(instructions, list) or any(
            not isinstance(item, str) or not item.strip() for item in instructions
        ):
            raise ValueError("instructions must be an array of nonempty strings")
        servers = existing.get("mcp", {})
        if not isinstance(servers, dict) or any(
            not isinstance(server, dict) for server in servers.values()
        ):
            raise ValueError("mcp must be an object of server configuration objects")
        owned = [
            f"opencode.json:mcp.{name}"
            for name in ("scout", "snpmemory", "snp-wiki")
            if name in servers
        ]
        merged = merge_configs(existing, generate_config("opencode"))
    except ConfigTargetConflict as exc:
        raise InstallError(7, str(exc)) from exc
    except (OSError, UnicodeError, ValueError) as exc:
        # The file may contain unrelated credentials; never echo its values.
        raise InstallError(
            7,
            f"opencode.json is not a usable native configuration ({type(exc).__name__})",
        ) from exc

    try:
        sources, skills = _package_files(package)
    except OSError as exc:
        raise InstallError(2, "agent package could not be read") from exc
    for skill in skills:
        destination = target / ".opencode/skills" / skill.name
        _validate_destination(target, destination, directory=True)
        if destination.exists():
            owned.append(str(destination.relative_to(target)))

    files: list[InstallFile] = []
    for source in sources:
        relative = source.relative_to(package)
        destination = target / ".opencode"
        if relative.parts[0] != "skills":
            destination /= "snp"
        destination /= relative
        _validate_destination(target, destination)
        if destination.exists():
            owned.append(str(destination.relative_to(target)))
        try:
            files.append(
                InstallFile(
                    destination,
                    source.read_bytes(),
                    stat.S_IMODE(source.stat().st_mode),
                )
            )
        except OSError as exc:
            raise InstallError(2, "agent package could not be read") from exc
        if relative.parts[0] in {"rules", "instructions"}:
            instruction = destination.relative_to(target).as_posix()
            if instruction in instructions:
                owned.append(f"opencode.json:instructions:{instruction}")
            else:
                instructions.append(instruction)

    merged["instructions"] = instructions
    content = (json.dumps(merged, indent=2) + "\n").encode("utf-8")
    files.append(InstallFile(config_path, content, 0o600))
    return InstallPlan(
        target,
        package,
        tuple(files),
        tuple(dict.fromkeys(owned)),
        tuple(sorted(merged["mcp"])),
    )


def require_confirmation(plan: InstallPlan, *, confirm: bool) -> None:
    if plan.owned_targets and not confirm:
        raise InstallError(
            5,
            "SNP-owned OpenCode targets already exist; review --dry-run, then use --confirm",
        )


def apply_install(plan: InstallPlan) -> None:
    """Stage every byte first, then replace managed files with rollback on failure."""
    created_directories: list[Path] = []
    replaced: list[tuple[Path, Path | None]] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix=".snp-install-", dir=plan.target
        ) as staging:
            stage = Path(staging)
            staged: list[tuple[InstallFile, Path, Path | None]] = []
            # Complete source reads, serialization and backups before any managed
            # file changes. Staging is owner-private and never emitted in output.
            for index, item in enumerate(plan.files):
                _validate_destination(plan.target, item.target)
                replacement = stage / f"{index}.new"
                replacement.write_bytes(item.content)
                replacement.chmod(item.mode)
                backup = None
                if item.target.exists():
                    backup = stage / f"{index}.old"
                    backup.write_bytes(item.target.read_bytes())
                    backup.chmod(stat.S_IMODE(item.target.stat().st_mode))
                staged.append((item, replacement, backup))
            try:
                for item, replacement, backup in staged:
                    missing: list[Path] = []
                    parent = item.target.parent
                    while not parent.exists():
                        missing.append(parent)
                        parent = parent.parent
                    for directory in reversed(missing):
                        directory.mkdir()
                        created_directories.append(directory)
                    os.replace(replacement, item.target)
                    replaced.append((item.target, backup))
            except OSError as exc:
                rollback_failed = False
                for destination, backup in reversed(replaced):
                    try:
                        if backup is None:
                            destination.unlink()
                        else:
                            os.replace(backup, destination)
                    except OSError:
                        rollback_failed = True
                for directory in reversed(created_directories):
                    try:
                        directory.rmdir()
                    except OSError:
                        rollback_failed = True
                message = "installation failed; previous files were restored"
                if rollback_failed:
                    message = "installation failed and rollback was incomplete; inspect the target"
                raise InstallError(2, message) from exc
    except OSError as exc:
        raise InstallError(
            2, "installation could not be staged; no package files were replaced"
        ) from exc


def install_opencode_agent(
    target: Path,
    *,
    package: Path = REPO_ROOT / "packages/snp-agent",
    dry_run: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    plan = prepare_install(target, package)
    if not dry_run:
        require_confirmation(plan, confirm=confirm)
        apply_install(plan)
    return plan.result(dry_run=dry_run)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default=".", type=Path)
    parser.add_argument(
        "--package", type=Path, default=REPO_ROOT / "packages/snp-agent"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = install_opencode_agent(
            args.directory,
            package=args.package,
            dry_run=args.dry_run,
            confirm=args.confirm,
        )
    except InstallError as exc:
        print(f"Error: {exc}.", file=sys.stderr)
        return exc.code
    if args.dry_run:
        print("[dry-run] OpenCode installation would write:")
        for path in result["would_write"]:
            print(f"  {path}")
    else:
        print(f"Installed SNP skills into {args.directory / '.opencode/skills'}")
        print("Rules and instructions are loaded by opencode.json.")
        print(f"Workflow guides: {args.directory / '.opencode/snp/workflows'}")
        print(
            "Set SCOUT_AUTH_HEADER before starting OpenCode to authenticate with Scout."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
