#!/usr/bin/env python3
"""Create a portable release backup and prove it by restoring only to staging.

The archive deliberately lives outside the repository: a database dump can
contain source-derived data and must not become an accidental Git artifact.
`create` reads from an explicitly approved source project. `restore` rejects
the default live project, requires the exact backup ID, and records the result
of the staging restore.  Ownership is never imported, but ACLs are preserved:
the staged migration service creates the two fixed RLS roles before a restore
so policies and grants can be replayed safely into a fresh cluster.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from scripts.write_release_manifest import (
    DEFAULT_LIVE_PROJECT,
    ManifestError,
    compose_command,
    file_sha256,
    validate_compose_project,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_RE = re.compile(r"[a-z][a-z0-9_]*")
_BACKUP_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")


class BackupError(RuntimeError):
    """The requested backup or restore cannot be performed safely."""


def _validate_database(database: str) -> str:
    if _DATABASE_RE.fullmatch(database) is None:
        raise BackupError(
            "database name must use lowercase letters, digits, and underscores"
        )
    return database


def _validate_backup_id(backup_id: str) -> str:
    if _BACKUP_ID_RE.fullmatch(backup_id) is None:
        raise BackupError(
            "backup ID must use lowercase letters, digits, dots, dashes, or underscores"
        )
    return backup_id


def _outside_repository(path: Path, *, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    raise BackupError(f"{label} must be outside the repository: {resolved}")


def validate_backup_destination(output: Path) -> Path:
    """Refuse an existing or repository-local archive destination."""
    destination = _outside_repository(output, label="backup archive")
    if not destination.parent.is_dir():
        raise BackupError(f"backup archive parent does not exist: {destination.parent}")
    if destination.exists():
        raise BackupError(f"backup archive already exists: {destination}")
    record_path = Path(f"{destination}.json")
    if record_path.exists():
        raise BackupError(f"backup record already exists: {record_path}")
    return destination


def _validate_result_destination(output: Path) -> Path:
    destination = _outside_repository(output, label="restore result")
    if not destination.parent.is_dir():
        raise BackupError(f"restore result parent does not exist: {destination.parent}")
    if destination.exists():
        raise BackupError(f"restore result already exists: {destination}")
    return destination


def backup_command(
    source_project: str, database: str, *, allow_live_source: bool
) -> list[str]:
    """Return the fixed-argv command that streams one custom PostgreSQL archive."""
    try:
        project = validate_compose_project(
            source_project, allow_live_project=allow_live_source
        )
    except ManifestError as exc:
        if source_project == DEFAULT_LIVE_PROJECT and not allow_live_source:
            raise BackupError(
                "refusing the live default project without --allow-live-source"
            ) from exc
        raise BackupError(str(exc)) from exc
    return compose_command(
        project,
        "exec",
        "-T",
        "--user",
        "postgres",
        "postgres",
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--dbname",
        _validate_database(database),
        allow_live_project=allow_live_source,
    )


def archive_check_command(source_project: str, *, allow_live_source: bool) -> list[str]:
    """Return the fixed-argv archive-list check run by PostgreSQL's pg_restore."""
    try:
        project = validate_compose_project(
            source_project, allow_live_project=allow_live_source
        )
    except ManifestError as exc:
        if source_project == DEFAULT_LIVE_PROJECT and not allow_live_source:
            raise BackupError(
                "refusing the live default project without --allow-live-source"
            ) from exc
        raise BackupError(str(exc)) from exc
    return compose_command(
        project,
        "exec",
        "-T",
        "--user",
        "postgres",
        "postgres",
        "pg_restore",
        "--list",
        allow_live_project=allow_live_source,
    )


def archive_ledger_command(
    target_project: str, *, compose_files: tuple[str, ...]
) -> list[str]:
    """Render only the migration-ledger data from an archive on stdin.

    ``pg_restore --table`` matches a bare relation name. A schema-qualified
    selector silently matches nothing, which produced the false empty-ledger
    diagnosis during the first v0.2.1 staging drill.
    """
    if not compose_files:
        raise BackupError("archive ledger inspection requires staging compose files")
    try:
        project = validate_compose_project(target_project)
        return compose_command(
            project,
            "exec",
            "-T",
            "--user",
            "postgres",
            "postgres",
            "pg_restore",
            "--data-only",
            "--table=schema_migrations",
            "--file=-",
            compose_files=compose_files,
        )
    except ManifestError as exc:
        raise BackupError(str(exc)) from exc


def bootstrap_roles_command(
    target_project: str, *, compose_files: tuple[str, ...]
) -> list[str]:
    """Run the staged migration service once to create the fixed cluster roles.

    Migrations create the database schema too, but `restore_backup` immediately
    drops that disposable database. PostgreSQL roles are cluster-scoped, so the
    two role identities and their secret-backed login passwords remain for the
    incoming archive's policies and grants.
    """
    if not compose_files:
        raise BackupError("restore requires explicit staging compose files")
    try:
        project = validate_compose_project(target_project)
        return compose_command(
            project,
            "run",
            "--rm",
            "--no-deps",
            "postgres-migrate",
            compose_files=compose_files,
        )
    except ManifestError as exc:
        raise BackupError(str(exc)) from exc


def restore_commands(target_project: str, database: str) -> list[list[str]]:
    """Return staging-only drop, create, and transactional restore commands."""
    try:
        project = validate_compose_project(target_project)
    except ManifestError as exc:
        raise BackupError(str(exc)) from exc
    checked_database = _validate_database(database)
    prefix = ("exec", "-T", "--user", "postgres", "postgres")
    return [
        compose_command(project, *prefix, "dropdb", "--if-exists", checked_database),
        compose_command(project, *prefix, "createdb", checked_database),
        compose_command(
            project,
            *prefix,
            "pg_restore",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--dbname",
            checked_database,
        ),
    ]


def _run(
    command: list[str],
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | int | None = None,
) -> str:
    effective_stdout: BinaryIO | int = subprocess.PIPE if stdout is None else stdout
    try:
        completed = subprocess.run(  # noqa: S603 - command is assembled fixed argv
            command,
            check=False,
            stdin=stdin,
            stdout=effective_stdout,
            stderr=subprocess.PIPE,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError(f"could not run {' '.join(command)!r}: {exc}") from exc
    if completed.returncode:
        detail = (
            completed.stderr.decode("utf-8", errors="replace").strip() or "no output"
        )
        raise BackupError(f"{' '.join(command)!r} failed: {detail}")
    return (
        completed.stdout.decode("utf-8", errors="replace").strip()
        if completed.stdout
        else ""
    )


def _migration_ledger_from_restore_sql(output: str) -> list[str]:
    """Extract ordered migration versions from pg_restore's COPY data."""
    lines = output.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.startswith("COPY public.schema_migrations ")
    ]
    if not starts:
        return []
    if len(starts) != 1:
        raise BackupError("archive contains multiple schema_migrations data sections")

    versions: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line == r"\.":
            return versions
        version = line.split("\t", 1)[0]
        if not version or not version.endswith(".sql"):
            raise BackupError("archive migration ledger contains a malformed row")
        if version in versions:
            raise BackupError("archive migration ledger contains a duplicate version")
        versions.append(version)
    raise BackupError("archive migration ledger COPY section is unterminated")


def _record_path(archive: Path) -> Path:
    return Path(f"{archive}.json")


def create_backup(
    *,
    source_project: str,
    database: str,
    backup_id: str,
    output: Path,
    allow_live_source: bool,
) -> Path:
    """Create, inspect, checksum, and record a new PostgreSQL custom archive."""
    destination = validate_backup_destination(output)
    checked_database = _validate_database(database)
    checked_id = _validate_backup_id(backup_id)
    dump = backup_command(
        source_project, checked_database, allow_live_source=allow_live_source
    )
    inspect = archive_check_command(source_project, allow_live_source=allow_live_source)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as archive_stream:
            _run(dump, stdout=archive_stream)
        if temporary.stat().st_size == 0:
            raise BackupError("pg_dump produced an empty archive")
        with temporary.open("rb") as archive_stream:
            _run(inspect, stdin=archive_stream, stdout=subprocess.DEVNULL)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    record = {
        "backup_id": checked_id,
        "created_at": datetime.now(UTC).isoformat(),
        "database": checked_database,
        "source_project": source_project,
        "archive_name": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": file_sha256(destination),
    }
    record_path = _record_path(destination)
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record_path


def validate_backup_record(
    *, archive: Path, record_path: Path, confirmed_backup_id: str
) -> dict[str, Any]:
    """Verify archive bytes and require an operator's exact backup-ID confirmation."""
    if not archive.is_file():
        raise BackupError(f"backup archive does not exist: {archive}")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BackupError(f"could not read backup record {record_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"backup record is not valid JSON: {record_path}") from exc
    if not isinstance(record, dict):
        raise BackupError("backup record must be a JSON object")
    for field in ("backup_id", "database", "source_project", "sha256"):
        if not isinstance(record.get(field), str):
            raise BackupError(f"backup record lacks a valid {field}")
    if record["backup_id"] != confirmed_backup_id:
        raise BackupError("backup ID confirmation does not match the backup record")
    if record["sha256"] != file_sha256(archive):
        raise BackupError("backup archive checksum differs from its record")
    _validate_database(record["database"])
    _validate_backup_id(record["backup_id"])
    return record


def restore_backup(
    *,
    target_project: str,
    archive: Path,
    record_path: Path,
    confirmed_backup_id: str,
    result: Path,
    compose_files: tuple[str, ...],
) -> Path:
    """Restore a validated archive transactionally into an explicit staging project."""
    result_path = _validate_result_destination(result)
    record = validate_backup_record(
        archive=archive,
        record_path=record_path,
        confirmed_backup_id=confirmed_backup_id,
    )
    with archive.open("rb") as archive_stream:
        archive_ledger_sql = _run(
            archive_ledger_command(target_project, compose_files=compose_files),
            stdin=archive_stream,
        )
    archive_ledger = _migration_ledger_from_restore_sql(archive_ledger_sql)

    _run(bootstrap_roles_command(target_project, compose_files=compose_files))
    commands = restore_commands(target_project, record["database"])
    _run(commands[0])
    _run(commands[1])
    with archive.open("rb") as archive_stream:
        _run(commands[2], stdin=archive_stream, stdout=subprocess.DEVNULL)

    project = validate_compose_project(target_project)
    ledger = _run(
        compose_command(
            project,
            "exec",
            "-T",
            "--user",
            "postgres",
            "postgres",
            "psql",
            "-d",
            record["database"],
            "-At",
            "-c",
            "SELECT version FROM schema_migrations ORDER BY version",
            compose_files=compose_files,
        )
    ).splitlines()
    if ledger != archive_ledger:
        raise BackupError(
            "restored migration ledger differs from archive "
            f"(archive={len(archive_ledger)}, restored={len(ledger)})"
        )
    restore_result = {
        "backup_id": record["backup_id"],
        "archive_sha256": record["sha256"],
        "database": record["database"],
        "target_project": project,
        "restored_at": datetime.now(UTC).isoformat(),
        "archive_migration_ledger": archive_ledger,
        "migration_ledger": ledger,
        "role_bootstrap": "postgres-migrate",
    }
    result_path.write_text(
        json.dumps(restore_result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result_path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create and validate a custom archive")
    create.add_argument("--source-project", required=True)
    create.add_argument("--allow-live-source", action="store_true")
    create.add_argument("--database", default="snp_rag")
    create.add_argument("--backup-id", required=True)
    create.add_argument("--output", type=Path, required=True)

    restore = commands.add_parser("restore", help="restore only into isolated staging")
    restore.add_argument("--target-project", required=True)
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--record", type=Path, required=True)
    restore.add_argument("--confirm-backup-id", required=True)
    restore.add_argument("--result", type=Path, required=True)
    restore.add_argument(
        "--compose-file",
        action="append",
        required=True,
        help="ordered staging Compose file; repeat for overlays",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "create":
            print(
                create_backup(
                    source_project=args.source_project,
                    database=args.database,
                    backup_id=args.backup_id,
                    output=args.output,
                    allow_live_source=args.allow_live_source,
                )
            )
        else:
            print(
                restore_backup(
                    target_project=args.target_project,
                    archive=args.archive,
                    record_path=args.record,
                    confirmed_backup_id=args.confirm_backup_id,
                    result=args.result,
                    compose_files=tuple(args.compose_file),
                )
            )
        return 0
    except BackupError as exc:
        print(f"release backup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
