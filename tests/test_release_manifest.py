"""Release-manifest inventory must be deterministic and fail closed."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.write_release_manifest import (
    ManifestError,
    _migration_ledger,
    capability_fingerprint_from_file,
    compose_command,
    file_sha256,
    image_inventory_from_inspect,
    manifest_matches,
    validate_compose_project,
    validate_manifest_destination,
)


def test_file_sha256_hashes_exact_bytes(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(b"dependency==1.2.3 --hash=sha256:abc\n")

    assert file_sha256(lock) == hashlib.sha256(lock.read_bytes()).hexdigest()


def test_manifest_destination_must_be_outside_the_candidate_worktree(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-manifest.json"
    assert validate_manifest_destination(output) == output

    with pytest.raises(ManifestError, match="outside the repository"):
        validate_manifest_destination(Path("artifacts/releases/release-manifest.json"))


def test_image_inventory_uses_repo_digest_for_pulled_image() -> None:
    inventory = image_inventory_from_inspect(
        "ghcr.io/example/service:1.2",
        {
            "RepoDigests": [
                "ghcr.io/example/service@sha256:" + "a" * 64,
            ],
            "Config": {"Labels": {}},
        },
    )

    assert inventory == {
        "image": "ghcr.io/example/service:1.2",
        "digest": "sha256:" + "a" * 64,
        "revision": None,
    }


def test_image_inventory_requires_revision_label_for_local_image() -> None:
    with pytest.raises(ManifestError, match="revision label"):
        image_inventory_from_inspect(
            "snp-scout",
            {"RepoDigests": [], "Config": {"Labels": {}}},
        )


def test_image_inventory_records_local_revision_label() -> None:
    inventory = image_inventory_from_inspect(
        "snp-scout",
        {
            "RepoDigests": [],
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": "abc123",
                }
            },
        },
    )

    assert inventory == {
        "image": "snp-scout",
        "digest": None,
        "revision": "abc123",
    }


def test_manifest_match_rejects_changed_lock_or_image_digest() -> None:
    expected = {
        "git_revision": "abc123",
        "dirty": False,
        "locks": {"scout/requirements.lock": "lock-a"},
        "images": [{"image": "postgres", "digest": "sha256:a", "revision": None}],
    }
    actual = {**expected}

    assert manifest_matches(expected, actual)

    actual = {**actual, "locks": {"scout/requirements.lock": "lock-b"}}
    assert not manifest_matches(expected, actual)


def test_capability_file_requires_a_fingerprint_object(tmp_path: Path) -> None:
    path = tmp_path / "capability.json"
    path.write_text('{"schema_version": 1, "parser_revision": 2}', encoding="utf-8")

    assert capability_fingerprint_from_file(path) == {
        "schema_version": 1,
        "parser_revision": 2,
    }

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestError, match="object"):
        capability_fingerprint_from_file(path)


def test_compose_commands_require_a_valid_explicit_project() -> None:
    assert validate_compose_project("snp-v021-staging") == "snp-v021-staging"
    assert compose_command("snp-v021-staging", "config", "--images") == [
        "docker",
        "compose",
        "--project-name",
        "snp-v021-staging",
        "config",
        "--images",
    ]

    with pytest.raises(ManifestError, match="project name"):
        validate_compose_project("SNP Production")


def test_compose_commands_preserve_the_selected_overlay_files() -> None:
    assert compose_command(
        "snp-v021-staging",
        "config",
        "--images",
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
        "config",
        "--images",
    ]


def test_migration_ledger_reads_the_schema_migration_version_from_the_target_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> str:
        commands.append(command)
        return "001_initial_schema.sql\n004_rag_metadata.sql\n"

    monkeypatch.setattr("scripts.write_release_manifest._run", fake_run)

    assert _migration_ledger("snp-v021-staging") == [
        "001_initial_schema.sql",
        "004_rag_metadata.sql",
    ]
    assert commands[0] == [
        "docker",
        "compose",
        "--project-name",
        "snp-v021-staging",
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
    ]
