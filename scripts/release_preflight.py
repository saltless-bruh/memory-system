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
        image_name = image["image"]
        digest = image.get("digest")
        image_revision = image.get("revision")
        has_digest = (
            isinstance(digest, str)
            and digest.startswith("sha256:")
            and _is_sha256(digest[7:])
        )
        if digest is not None and not has_digest:
            return False

        # A third-party image reference pinned at ``@sha256:...`` is immutable
        # because of that digest. Its OCI revision label belongs to its upstream
        # project, so requiring it to equal this repository's candidate SHA
        # would reject every correctly pinned image that happens to publish the
        # standard label. Locally built images have ordinary candidate tags;
        # those must carry *this* candidate revision label even when the local
        # Docker daemon reports an image digest, since that digest alone does
        # not prove which source revision built it.
        if "@sha256:" in image_name:
            if not has_digest:
                return False
            continue
        if image_revision != revision:
            return False
    return True


def _restore_evidence_findings(
    restore_result: object,
    *,
    expected_project: str,
    expected_migrations: list[str],
    backup_id: str,
) -> list[ReleaseFinding]:
    """Validate the immutable evidence emitted by the staging restore helper.

    The archive ledger and the restored ledger are separate evidence. Looking
    only at current staging state would let a later migration-service run hide a
    bad restore; looking only at an archive selector would repeat the v0.2.1
    false negative caused by a selector that silently matched no table.
    """
    if not isinstance(restore_result, dict):
        return [
            ReleaseFinding(
                "restore-result",
                "the staging restore result is missing or malformed",
            )
        ]

    findings: list[ReleaseFinding] = []
    if restore_result.get("backup_id") != backup_id:
        findings.append(
            ReleaseFinding(
                "restore-backup",
                "the restore result does not name the requested backup ID",
            )
        )
    archive_sha = restore_result.get("archive_sha256")
    if not _is_sha256(archive_sha):
        findings.append(
            ReleaseFinding(
                "restore-archive",
                "the restore result does not record a valid archive checksum",
            )
        )
    if restore_result.get("target_project") != expected_project:
        findings.append(
            ReleaseFinding(
                "restore-target",
                "the restore result names a different Compose project",
            )
        )
    if restore_result.get("role_bootstrap") != "postgres-migrate":
        findings.append(
            ReleaseFinding(
                "restore-roles",
                "the restore result does not prove the staged role bootstrap",
            )
        )
    archive_ledger = restore_result.get("archive_migration_ledger")
    restored_ledger = restore_result.get("migration_ledger")
    if archive_ledger != expected_migrations:
        findings.append(
            ReleaseFinding(
                "restore-archive-migrations",
                "the archive does not contain the complete migration ledger",
            )
        )
    if restored_ledger != expected_migrations:
        findings.append(
            ReleaseFinding(
                "restore-migrations",
                "the archive did not restore the complete migration ledger",
            )
        )
    if archive_ledger != restored_ledger:
        findings.append(
            ReleaseFinding(
                "restore-ledger-match",
                "the archive and restored migration ledgers disagree",
            )
        )
    return findings


def _read_restore_result(path: Path) -> dict[str, Any]:
    """Load restore evidence from outside the candidate worktree."""
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        raise ManifestError(
            f"restore result must be outside the repository: {resolved}"
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"could not read restore result {resolved}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"restore result is not valid JSON: {resolved}") from exc
    if not isinstance(value, dict):
        raise ManifestError("restore result must be a JSON object")
    return value


def validate_release_evidence(
    *,
    recorded: dict[str, Any],
    observed: dict[str, Any],
    expected_revision: str | None,
    expected_migrations: list[str],
    backup_id: str,
    restore_result: object | None = None,
    compose_project: str | None = None,
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

    expected_project = compose_project or str(recorded.get("compose_project", ""))
    findings.extend(
        _restore_evidence_findings(
            restore_result,
            expected_project=expected_project,
            expected_migrations=expected_migrations,
            backup_id=backup_id,
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
    parser.add_argument(
        "--restore-result",
        type=Path,
        required=True,
        help="external JSON result written by scripts/release_backup.py restore",
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
        restore_result = _read_restore_result(args.restore_result)
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
        restore_result=restore_result,
        compose_project=args.compose_project,
    )
    if findings:
        for finding in findings:
            print(f"FAIL {finding.check}: {finding.detail}")
        return exit_code_for(findings)
    print(f"PASS release-preflight: {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
