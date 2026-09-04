"""Release backup/restore helpers must protect the live project and evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.release_backup import (
    BackupError,
    _migration_ledger_from_restore_sql,
    _run,
    archive_ledger_command,
    backup_command,
    bootstrap_roles_command,
    create_backup,
    restore_backup,
    restore_commands,
    validate_backup_destination,
    validate_backup_record,
)

ARCHIVE_LEDGER_SQL = """\
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public
COPY public.schema_migrations (version, applied_at) FROM stdin;
001_initial_schema.sql\t2026-08-19 02:02:51+00
005_ingest_events.sql\t2026-08-26 07:12:04+00
\\.
"""


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
        "--dbname",
        "snp_rag",
    ]

    with pytest.raises(BackupError, match="allow-live-source"):
        backup_command("snp-memory", "snp_rag", allow_live_source=False)


def test_restore_bootstraps_roles_then_recreates_the_staging_database() -> None:
    assert bootstrap_roles_command(
        "snp-v021-staging",
        compose_files=("docker-compose.yml", "docker-compose.staging.yml"),
    ) == [
        "docker",
        "compose",
        "--project-name",
        "snp-v021-staging",
        "--file",
        "docker-compose.yml",
        "--file",
        "docker-compose.staging.yml",
        "run",
        "--rm",
        "--no-deps",
        "postgres-migrate",
    ]

    with pytest.raises(BackupError, match="explicit staging compose files"):
        bootstrap_roles_command("snp-v021-staging", compose_files=())

    commands = restore_commands("snp-v021-staging", "snp_rag")

    assert commands[0][-3:] == ["dropdb", "--if-exists", "snp_rag"]
    assert commands[1][-2:] == ["createdb", "snp_rag"]
    assert commands[2][-6:] == [
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--dbname",
        "snp_rag",
    ]

    with pytest.raises(BackupError, match="live default"):
        restore_commands("snp-memory", "snp_rag")


def test_archive_ledger_uses_a_bare_table_selector_in_the_staging_project() -> None:
    assert archive_ledger_command(
        "snp-v021-staging",
        compose_files=("docker-compose.yml", "docker-compose.staging.yml"),
    ) == [
        "docker",
        "compose",
        "--project-name",
        "snp-v021-staging",
        "--file",
        "docker-compose.yml",
        "--file",
        "docker-compose.staging.yml",
        "exec",
        "-T",
        "--user",
        "postgres",
        "postgres",
        "pg_restore",
        "--data-only",
        "--table=schema_migrations",
        "--file=-",
    ]


def test_archive_ledger_parser_has_a_known_positive_and_negative_control() -> None:
    assert _migration_ledger_from_restore_sql(ARCHIVE_LEDGER_SQL) == [
        "001_initial_schema.sql",
        "005_ingest_events.sql",
    ]
    # This is what pg_restore emits when a schema-qualified --table selector
    # silently matches no object. It must never be mistaken for positive proof.
    assert _migration_ledger_from_restore_sql("-- PostgreSQL database dump --\n") == []


def test_run_captures_stdout_when_the_caller_does_not_redirect_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_subprocess_run(command: list[str], **kwargs: object) -> object:
        assert command == ["ledger-reader"]
        assert kwargs["stdout"] is subprocess.PIPE
        return subprocess.CompletedProcess(
            command, 0, stdout=b"005_ingest_events.sql\n", stderr=b""
        )

    monkeypatch.setattr("scripts.release_backup.subprocess.run", fake_subprocess_run)

    assert _run(["ledger-reader"]) == "005_ingest_events.sql"


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
        if "pg_restore" in command and "--data-only" in command:
            return ARCHIVE_LEDGER_SQL
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
        compose_files=("docker-compose.yml", "docker-compose.staging.yml"),
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["target_project"] == "snp-v021-staging"
    assert result["migration_ledger"] == [
        "001_initial_schema.sql",
        "005_ingest_events.sql",
    ]
    assert result["archive_migration_ledger"] == result["migration_ledger"]
    assert result["role_bootstrap"] == "postgres-migrate"
    assert calls[0][-4:] == [
        "pg_restore",
        "--data-only",
        "--table=schema_migrations",
        "--file=-",
    ]
    assert calls[1][-4:] == ["run", "--rm", "--no-deps", "postgres-migrate"]
    assert calls[2][-3:] == ["dropdb", "--if-exists", "snp_rag"]
    assert "pg_restore" in calls[4]
    assert calls[5][4:8] == [
        "--file",
        "docker-compose.yml",
        "--file",
        "docker-compose.staging.yml",
    ]


def test_restore_refuses_archive_and_restored_ledger_disagreement(
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

    def fake_run(command: list[str], **kwargs: object) -> str:
        if "pg_restore" in command and "--data-only" in command:
            return ARCHIVE_LEDGER_SQL
        if "psql" in command:
            return "001_initial_schema.sql"
        return ""

    monkeypatch.setattr("scripts.release_backup._run", fake_run)

    with pytest.raises(BackupError, match="differs from archive"):
        restore_backup(
            target_project="snp-v021-staging",
            archive=archive,
            record_path=record_path,
            confirmed_backup_id="v0.2.1-prestage",
            result=tmp_path / "candidate.restore.json",
            compose_files=("docker-compose.yml", "docker-compose.staging.yml"),
        )
