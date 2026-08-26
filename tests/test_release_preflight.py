"""The production-transition preflight must fail closed on release evidence."""

from __future__ import annotations

from scripts.release_preflight import (
    ReleaseFinding,
    exit_code_for,
    validate_release_evidence,
)

REVISION = "a" * 40
LOCKS = {
    "scout/requirements.lock": "b" * 64,
    "basic-memory/requirements.lock": "c" * 64,
    "scripts/sync-service.lock": "d" * 64,
}
MIGRATIONS = ["001_initial_schema.sql", "004_document_capability_fingerprint.sql"]


def _manifest(**changes: object) -> dict[str, object]:
    return {
        "compose_project": "snp-v021-staging",
        "compose_files": ["docker-compose.yml", "docker-compose.staging.yml"],
        "git_revision": REVISION,
        "dirty": False,
        "locks": LOCKS,
        "images": [
            {"image": "postgres", "digest": "sha256:" + "e" * 64, "revision": None},
            {"image": "snp-scout", "digest": None, "revision": REVISION},
        ],
        "migration_ledger": MIGRATIONS,
        "capability_fingerprint": {"schema_version": 1},
    } | changes


def test_release_evidence_accepts_a_clean_current_candidate() -> None:
    findings = validate_release_evidence(
        recorded=_manifest(),
        observed=_manifest(),
        expected_revision=REVISION,
        expected_migrations=MIGRATIONS,
        backup_id="pg-restore-2026-08-26T10:20Z",
    )

    assert findings == []
    assert exit_code_for(findings) == 0


def test_release_evidence_rejects_a_dirty_or_mismatched_candidate() -> None:
    findings = validate_release_evidence(
        recorded=_manifest(dirty=True),
        observed=_manifest(),
        expected_revision="f" * 40,
        expected_migrations=MIGRATIONS,
        backup_id="backup-1",
    )

    # A bad requested revision also invalidates local-image labels and the
    # re-observed inventory. The named root causes must never be hidden.
    assert {"clean-worktree", "revision"} <= {finding.check for finding in findings}
    assert exit_code_for(findings) == 1


def test_release_evidence_requires_backup_and_complete_migration_ledger() -> None:
    findings = validate_release_evidence(
        recorded=_manifest(migration_ledger=[MIGRATIONS[0]]),
        observed=_manifest(migration_ledger=[MIGRATIONS[0]]),
        expected_revision=REVISION,
        expected_migrations=MIGRATIONS,
        backup_id="",
    )

    assert {finding.check for finding in findings} == {"backup", "migrations"}


def test_release_evidence_rejects_an_unverifiable_image_or_lock() -> None:
    findings = validate_release_evidence(
        recorded=_manifest(
            locks={"scout/requirements.lock": "not-a-sha"},
            images=[{"image": "snp-scout", "digest": None, "revision": "unknown"}],
        ),
        observed=_manifest(
            locks={"scout/requirements.lock": "not-a-sha"},
            images=[{"image": "snp-scout", "digest": None, "revision": "unknown"}],
        ),
        expected_revision=REVISION,
        expected_migrations=MIGRATIONS,
        backup_id="backup-1",
    )

    assert {finding.check for finding in findings} == {"locks", "images"}


def test_release_evidence_rejects_observed_drift() -> None:
    findings = validate_release_evidence(
        recorded=_manifest(),
        observed=_manifest(locks={**LOCKS, "scout/requirements.lock": "f" * 64}),
        expected_revision=REVISION,
        expected_migrations=MIGRATIONS,
        backup_id="backup-1",
    )

    assert findings == [
        ReleaseFinding(
            check="manifest-match",
            detail="the observed release inputs differ from the recorded manifest",
        )
    ]
