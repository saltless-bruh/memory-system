"""Shared fixtures for the Scout test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.backends.fake import FakeRagBackend
from scout.types import RagChunk

_POSTGRES_ROLES = {
    "test_pgvector_live.py": ("QUERY", "INGEST"),
    "test_postgres_migrations.py": ("QUERY", "INGEST", "MIGRATION"),
    "test_postgres_rls.py": ("QUERY", "INGEST", "MIGRATION"),
    "test_ingest_v2.py": ("INGEST",),
    "test_eval_benchmarks.py": ("QUERY", "INGEST"),
}


def _missing_value_or_file(value_name: str, file_name: str) -> list[str]:
    if os.environ.get(value_name, "").strip():
        return []
    configured_file = os.environ.get(file_name, "").strip()
    if configured_file:
        path = Path(configured_file)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return []
        except OSError:
            pass
    return [f"{value_name} or readable {file_name}"]


def _postgres_prerequisites(roles: tuple[str, ...]) -> list[str]:
    missing = [
        name
        for name in ("POSTGRES_HOST", "POSTGRES_DB")
        if not os.environ.get(name, "").strip()
    ]
    for role in roles:
        user_name = f"POSTGRES_{role}_USER"
        if not os.environ.get(user_name, "").strip():
            missing.append(user_name)
        missing.extend(
            _missing_value_or_file(
                f"POSTGRES_{role}_PASSWORD",
                f"POSTGRES_{role}_PASSWORD_FILE",
            )
        )
    return missing


#: Ambient variables that must not leak into the deterministic suite.
#: `README.md` tells developers to export `LITELLM_BASE_URL` before an
#: integration run; leaving it set then made seven offline tests in
#: `tests/test_chunker.py` fail, because production embedder paths switch on its
#: presence. The offline suite must be hermetic, so a non-integration test never
#: sees it (audit finding NEW-2).
_AMBIENT_LIVE_ENV = ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY")


@pytest.fixture(autouse=True)
def isolate_offline_tests_from_ambient_live_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clear live-gateway variables for every non-integration test."""
    if request.node.get_closest_marker("integration") is not None:
        return
    for name in _AMBIENT_LIVE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def require_live_integration_prerequisites(request: pytest.FixtureRequest) -> None:
    """Fail any explicitly selected live test clearly instead of skipping."""
    if request.node.get_closest_marker("integration") is None:
        return

    missing: list[str] = []
    if os.environ.get("SNP_INTEGRATION_PROJECT") != "snp-memory-it":
        missing.append("SNP_INTEGRATION_PROJECT=snp-memory-it")

    module_name = Path(str(request.node.path)).name
    if module_name in _POSTGRES_ROLES:
        missing.extend(_postgres_prerequisites(_POSTGRES_ROLES[module_name]))
    elif module_name == "test_litellm_embedding.py":
        missing.extend(
            name
            for name in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY")
            if not os.environ.get(name, "").strip()
        )
    elif module_name == "test_mcp_http_auth_live.py":
        if not os.environ.get("SCOUT_INTEGRATION_URL", "").strip():
            missing.append("SCOUT_INTEGRATION_URL")
        missing.extend(
            _missing_value_or_file(
                "SCOUT_INTEGRATION_INFRA_TOKEN",
                "SCOUT_INTEGRATION_INFRA_TOKEN_FILE",
            )
        )

    if missing:
        pytest.fail(
            "live integration prerequisites are missing: " + ", ".join(missing),
            pytrace=False,
        )


@pytest.fixture
def chunks() -> list[RagChunk]:
    """A small mixed-source corpus: two files, varied scores."""
    return [
        RagChunk(
            text="Kerberoasting requests a TGS for an SPN and cracks it offline",
            file_path="raw/reports/acme.pdf",
            score=0.9,
            loc="p.12",
            meta={"team": "redteam"},
        ),
        RagChunk(
            text="The service account had a weak password",
            file_path="raw/reports/acme.pdf",
            score=0.5,
            loc="p.13",
        ),
        RagChunk(
            text="ESC8 relays NTLM to the AD CS web enrollment endpoint",
            file_path="raw/advisories/adcs.md",
            score=0.8,
        ),
    ]


@pytest.fixture
def backend(chunks: list[RagChunk]) -> FakeRagBackend:
    """A fake backend seeded with the sample corpus."""
    return FakeRagBackend(chunks=chunks)
