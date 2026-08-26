#!/usr/bin/env python3
"""Fail closed unless a release candidate still matches its recorded evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.migrate_postgres import MigrationError, discover_migrations
from scripts.write_release_manifest import (
    LOCKFILES,
    ManifestError,
    capability_fingerprint_from_file,
    collect_manifest,
    manifest_matches,
    validate_manifest_destination,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REVISION_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class ReleaseFinding:
    """One release condition that failed without implying a safe remedy."""

    check: str
    detail: str


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_revision(value: object) -> bool:
    return isinstance(value, str) and _REVISION_RE.fullmatch(value) is not None


def _locks_are_complete(locks: object) -> bool:
    expected = {str(path) for path in LOCKFILES}
    return (
        isinstance(locks, dict)
        and set(locks) == expected
        and all(_is_sha256(value) for value in locks.values())
    )


def _images_are_immutable(images: object, revision: str) -> bool:
    if not isinstance(images, list) or not images:
        return False
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("image"), str):
            return False
        digest = image.get("digest")
        image_revision = image.get("revision")
        if digest is not None and not (
            isinstance(digest, str)
            and digest.startswith("sha256:")
            and _is_sha256(digest[7:])
        ):
            return False
        if image_revision is not None and image_revision != revision:
            return False
        if digest is None and image_revision != revision:
            return False
    return True


def validate_release_evidence(
    *,
    recorded: dict[str, Any],
    observed: dict[str, Any],
    expected_revision: str | None,
    expected_migrations: list[str],
    backup_id: str,
) -> list[ReleaseFinding]:
    """Return every release blocker determinable from recorded/observed evidence."""
    findings: list[ReleaseFinding] = []
    candidate_revision = (
        expected_revision
        if isinstance(expected_revision, str)
        and _REVISION_RE.fullmatch(expected_revision)
        else None
    )

    if recorded.get("dirty") is not False:
        findings.append(
            ReleaseFinding(
                "clean-worktree",
                "the recorded candidate was built from a dirty worktree",
            )
        )

    if candidate_revision is None:
        findings.append(
            ReleaseFinding(
                "revision",
                "SNP_GIT_REVISION must name the exact 40-character candidate commit",
            )
        )
    elif recorded.get("git_revision") != candidate_revision:
        findings.append(
            ReleaseFinding(
                "revision",
                "the requested candidate revision differs from the recorded manifest",
            )
        )

    if not _locks_are_complete(recorded.get("locks")):
        findings.append(
            ReleaseFinding(
                "locks",
                "the manifest does not contain all required lockfile SHA-256 values",
            )
        )

    if candidate_revision is not None and not _images_are_immutable(
        recorded.get("images"), candidate_revision
    ):
        findings.append(
            ReleaseFinding(
                "images",
                "every image must carry a digest or the candidate OCI revision label",
            )
        )

    if recorded.get("migration_ledger") != expected_migrations:
        findings.append(
            ReleaseFinding(
                "migrations",
                "the recorded migration ledger does not include every repository migration",
            )
        )

    if not backup_id.strip():
        findings.append(
            ReleaseFinding(
                "backup", "a PostgreSQL backup or restore-point identifier is required"
            )
        )

    if not manifest_matches(recorded, observed):
        findings.append(
            ReleaseFinding(
                "manifest-match",
                "the observed release inputs differ from the recorded manifest",
            )
        )
    return findings


def exit_code_for(findings: list[ReleaseFinding]) -> int:
    """Use one for an unsafe candidate; operational collection failures use two."""
    return 0 if not findings else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="external manifest recorded for this candidate",
    )
    parser.add_argument(
        "--compose-project",
        required=True,
        help="explicit Docker Compose project whose candidate is being checked",
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
        "--capability-file",
        type=Path,
        required=True,
        help="fingerprint captured from the exact parser container being promoted",
    )
    parser.add_argument(
        "--backup-id",
        default=os.environ.get("SNP_RELEASE_BACKUP_ID", ""),
        help="operator-recorded PostgreSQL backup or restore-point identifier",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Collect current evidence, compare it, and never turn a failure into a deploy."""
    args = _parse_args(argv)
    try:
        manifest_path = validate_manifest_destination(args.manifest)
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(recorded, dict):
            raise ManifestError(
                f"release manifest must be a JSON object: {manifest_path}"
            )
        capability = capability_fingerprint_from_file(args.capability_file)
        expected_migrations = [path.name for path in discover_migrations()]
        observed = collect_manifest(
            compose_project=args.compose_project,
            capability=capability,
            allow_live_project=args.allow_live_project,
            compose_files=tuple(args.compose_file),
        )
    except (OSError, json.JSONDecodeError, ManifestError, MigrationError) as exc:
        print(f"release preflight unavailable: {exc}", file=sys.stderr)
        return 2

    findings = validate_release_evidence(
        recorded=recorded,
        observed=observed,
        expected_revision=os.environ.get("SNP_GIT_REVISION"),
        expected_migrations=expected_migrations,
        backup_id=args.backup_id,
    )
    if findings:
        for finding in findings:
            print(f"FAIL {finding.check}: {finding.detail}")
        return exit_code_for(findings)
    print(f"PASS release-preflight: {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
