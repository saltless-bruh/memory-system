#!/usr/bin/env python3
"""Write or verify the immutable inputs observed for one release candidate.

This is an inventory, not an attestation.  It records exactly which source,
locks, images, database migration ledger, and parser capability were observed
when a candidate was built, so a later promotion can compare rather than guess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout.capabilities import capability_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCKFILES = (
    Path("scout/requirements.lock"),
    Path("basic-memory/requirements.lock"),
    Path("scripts/sync-service.lock"),
)
_IMMUTABLE_KEYS = (
    "compose_project",
    "compose_files",
    "git_revision",
    "dirty",
    "locks",
    "images",
    "migration_ledger",
    "capability_fingerprint",
)
DEFAULT_LIVE_PROJECT = "snp-memory"
_COMPOSE_PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ManifestError(RuntimeError):
    """The release inputs could not be collected or compared safely."""


def validate_compose_project(project: str, *, allow_live_project: bool = False) -> str:
    """Validate an explicit Compose target and protect the live default by default."""
    if _COMPOSE_PROJECT_RE.fullmatch(project) is None:
        raise ManifestError(
            "Compose project name must start with lowercase letter/digit and contain "
            "only lowercase letters, digits, dashes, or underscores"
        )
    if project == DEFAULT_LIVE_PROJECT and not allow_live_project:
        raise ManifestError(
            "refusing the live default Compose project; use an isolated staging project "
            "or explicitly allow the live project"
        )
    return project


def compose_command(
    project: str,
    *arguments: str,
    allow_live_project: bool = False,
    compose_files: tuple[str, ...] = (),
) -> list[str]:
    """Build one fixed-argv Compose command for the selected deployment only."""
    if any(not filename for filename in compose_files):
        raise ManifestError("Compose file paths must be nonempty")
    file_flags = [part for filename in compose_files for part in ("--file", filename)]
    return [
        "docker",
        "compose",
        "--project-name",
        validate_compose_project(project, allow_live_project=allow_live_project),
        *file_flags,
        *arguments,
    ]


def validate_manifest_destination(output: Path) -> Path:
    """Keep runtime release evidence out of the Git candidate it verifies."""
    destination = output.resolve()
    try:
        destination.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        raise ManifestError(
            f"release manifest must be outside the repository: {destination}"
        )
    if not destination.parent.is_dir():
        raise ManifestError(
            f"release manifest parent does not exist: {destination.parent}"
        )
    return destination


def file_sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest without loading a lockfile wholesale."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def image_inventory_from_inspect(
    image: str, inspect: dict[str, Any]
) -> dict[str, str | None]:
    """Normalise one Docker inspect record into immutable release evidence."""
    repo_digests = inspect.get("RepoDigests") or []
    digest = next(
        (entry.rsplit("@", 1)[1] for entry in repo_digests if "@sha256:" in entry),
        None,
    )
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    revision = labels.get("org.opencontainers.image.revision")
    if digest is None and not revision:
        raise ManifestError(
            f"local image {image!r} has neither a pulled digest nor an OCI revision label"
        )
    return {"image": image, "digest": digest, "revision": revision}


def manifest_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare release-defining fields while intentionally ignoring generation time."""
    return all(expected.get(key) == actual.get(key) for key in _IMMUTABLE_KEYS)


def capability_fingerprint_from_file(path: Path) -> dict[str, Any]:
    """Load a fingerprint captured from the exact container being promoted."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(
            f"could not read capability fingerprint {path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"capability fingerprint is not valid JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise ManifestError(f"capability fingerprint must be a JSON object: {path}")
    return value


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv only
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError(f"could not run {' '.join(command)!r}: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ManifestError(f"{' '.join(command)!r} failed: {detail}")
    return completed.stdout.strip()


def _image_names(
    project: str,
    *,
    allow_live_project: bool = False,
    compose_files: tuple[str, ...] = (),
) -> list[str]:
    names = [
        line
        for line in _run(
            compose_command(
                project,
                "config",
                "--images",
                allow_live_project=allow_live_project,
                compose_files=compose_files,
            )
        ).splitlines()
        if line
    ]
    if not names:
        raise ManifestError("docker compose config reported no images")
    return sorted(set(names))


def _inspect_image(image: str) -> dict[str, Any]:
    output = _run(["docker", "image", "inspect", image])
    try:
        records = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"docker image inspect returned invalid JSON for {image!r}"
        ) from exc
    if (
        not isinstance(records, list)
        or len(records) != 1
        or not isinstance(records[0], dict)
    ):
        raise ManifestError(
            f"docker image inspect returned no single record for {image!r}"
        )
    return records[0]


def _migration_ledger(
    project: str,
    *,
    allow_live_project: bool = False,
    compose_files: tuple[str, ...] = (),
) -> list[str]:
    output = _run(
        compose_command(
            project,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "snp_rag",
            "-At",
            "-c",
            "SELECT version FROM schema_migrations ORDER BY version",
            allow_live_project=allow_live_project,
            compose_files=compose_files,
        )
    )
    return [line for line in output.splitlines() if line]


def collect_manifest(
    repo_root: Path = REPO_ROOT,
    *,
    compose_project: str,
    capability: dict[str, Any] | None = None,
    allow_live_project: bool = False,
    compose_files: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Collect the deployment evidence that must remain identical on promotion."""
    project = validate_compose_project(
        compose_project, allow_live_project=allow_live_project
    )
    locks: dict[str, str] = {}
    for relative in LOCKFILES:
        path = repo_root / relative
        if not path.is_file():
            raise ManifestError(f"required lockfile is missing: {relative}")
        locks[str(relative)] = file_sha256(path)

    images = [
        image_inventory_from_inspect(image, _inspect_image(image))
        for image in _image_names(
            project,
            allow_live_project=allow_live_project,
            compose_files=compose_files,
        )
    ]
    return {
        "compose_project": project,
        "compose_files": list(compose_files),
        "git_revision": _run(["git", "rev-parse", "HEAD"]),
        "dirty": bool(_run(["git", "status", "--porcelain"])),
        "locks": locks,
        "images": images,
        "migration_ledger": _migration_ledger(
            project,
            allow_live_project=allow_live_project,
            compose_files=compose_files,
        ),
        "capability_fingerprint": capability or capability_fingerprint(),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="external path for immutable candidate evidence",
    )
    parser.add_argument(
        "--compose-project",
        required=True,
        help="explicit Docker Compose project to inventory",
    )
    parser.add_argument(
        "--allow-live-project",
        action="store_true",
        help="allow the default live project instead of isolated staging",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Compose file to use; repeat in precedence order for overlays",
    )
    parser.add_argument(
        "--check", action="store_true", help="compare the existing manifest"
    )
    parser.add_argument(
        "--capability-file",
        type=Path,
        help="JSON fingerprint captured from the container being promoted",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        capability = (
            capability_fingerprint_from_file(args.capability_file)
            if args.capability_file is not None
            else None
        )
        actual = collect_manifest(
            compose_project=args.compose_project,
            capability=capability,
            allow_live_project=args.allow_live_project,
            compose_files=tuple(args.compose_file),
        )
        output = validate_manifest_destination(args.output)
        if args.check:
            if not output.is_file():
                raise ManifestError(f"manifest does not exist: {output}")
            expected = json.loads(output.read_text(encoding="utf-8"))
            if not manifest_matches(expected, actual):
                raise ManifestError("release inputs differ from the recorded manifest")
            print(f"manifest matches: {output}")
            return 0

        if output.exists():
            raise ManifestError(f"manifest already exists: {output}")

        output.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(output)
        return 0
    except (ManifestError, json.JSONDecodeError) as exc:
        print(f"release manifest: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
