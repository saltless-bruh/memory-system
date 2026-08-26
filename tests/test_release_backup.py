"""Release backup/restore helpers must protect the live project and evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.release_backup import (
    BackupError,
    backup_command,
    create_backup,
    restore_backup,
    restore_commands,
    validate_backup_destination,
    validate_backup_record,
)


def test_backup_uses_custom_archive_from_the_explicit_source_project() -> None:
    assert backup_command("snp-memory", "snp_rag", allow_live_source=True) == [
        "docker",
        "compose",
        "--project-name",
        "snp-memory",
        "exec",
        "-T",
        "--user",
        "postgres",
        "postgres",
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        "snp_rag",
    ]

    with pytest.raises(BackupError, match="allow-live-source"):
        backup_command("snp-memory", "snp_rag", allow_live_source=False)


def test_restore_is_staging_only_and_drops_before_recreating_the_database() -> None:
    commands = restore_commands("snp-v021-staging", "snp_rag")

    assert commands[0][-3:] == ["dropdb", "--if-exists", "snp_rag"]
    assert commands[1][-2:] == ["createdb", "snp_rag"]
    assert commands[2][-7:] == [
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        "snp_rag",
    ]

    with pytest.raises(BackupError, match="live default"):
        restore_commands("snp-memory", "snp_rag")


def test_backup_destination_is_outside_the_repository_and_never_overwritten(
    tmp_path: Path,
) -> None:
    output = tmp_path / "candidate.dump"
    assert validate_backup_destination(output) == output

    output.write_bytes(b"existing")
    with pytest.raises(BackupError, match="already exists"):
        validate_backup_destination(output)


def test_backup_record_requires_matching_archive_hash_and_confirmed_id(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "candidate.dump"
    archive.write_bytes(b"portable archive")
    record = {
        "backup_id": "v0.2.1-prestage",
        "database": "snp_rag",
        "source_project": "snp-memory",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    record_path = tmp_path / "candidate.dump.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert (
        validate_backup_record(
            archive=archive,
            record_path=record_path,
            confirmed_backup_id="v0.2.1-prestage",
        )
        == record
    )

    with pytest.raises(BackupError, match="confirmation"):
        validate_backup_record(
            archive=archive,
            record_path=record_path,
            confirmed_backup_id="different-backup",
        )


def test_create_backup_validates_the_archive_then_records_its_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        if "pg_dump" in command:
            stream = kwargs["stdout"]
            assert hasattr(stream, "write")
            stream.write(b"custom archive bytes")  # type: ignore[union-attr]
        return ""

    monkeypatch.setattr("scripts.release_backup._run", fake_run)
    record_path = create_backup(
        source_project="snp-v021-source",
        database="snp_rag",
        backup_id="v0.2.1-prestage",
        output=tmp_path / "candidate.dump",
        allow_live_source=False,
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["backup_id"] == "v0.2.1-prestage"
    assert record["sha256"] == hashlib.sha256(b"custom archive bytes").hexdigest()
    assert "pg_dump" in calls[0]
    assert "pg_restore" in calls[1]


def test_restore_backup_records_the_staging_migration_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "candidate.dump"
    archive.write_bytes(b"portable archive")
    record_path = tmp_path / "candidate.dump.json"
    record_path.write_text(
        json.dumps(
            {
                "backup_id": "v0.2.1-prestage",
                "database": "snp_rag",
                "source_project": "snp-memory",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> str:
        calls.append(command)
        return (
            "001_initial_schema.sql\n005_ingest_events.sql" if "psql" in command else ""
        )

    monkeypatch.setattr("scripts.release_backup._run", fake_run)
    result_path = restore_backup(
        target_project="snp-v021-staging",
        archive=archive,
        record_path=record_path,
        confirmed_backup_id="v0.2.1-prestage",
        result=tmp_path / "candidate.restore.json",
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["target_project"] == "snp-v021-staging"
    assert result["migration_ledger"] == [
        "001_initial_schema.sql",
        "005_ingest_events.sql",
    ]
    assert calls[0][-3:] == ["dropdb", "--if-exists", "snp_rag"]
    assert "pg_restore" in calls[2]
