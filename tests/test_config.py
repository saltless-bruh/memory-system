from __future__ import annotations

from pathlib import Path

import pytest

from scout.config import ConfigError, postgres_settings


def _base() -> dict[str, str]:
    return {
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "memory",
        "POSTGRES_QUERY_USER": "query-role",
        "POSTGRES_QUERY_PASSWORD": "synthetic-query-password",
    }


def test_postgres_settings_requires_role_password() -> None:
    env = _base()
    env.pop("POSTGRES_QUERY_PASSWORD")
    with pytest.raises(ConfigError, match="POSTGRES_QUERY_PASSWORD"):
        postgres_settings("query", env)


def test_postgres_settings_prefers_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "query-password"
    secret.write_text("from-file\n", encoding="utf-8")
    env = _base() | {
        "POSTGRES_QUERY_PASSWORD": "from-env",
        "POSTGRES_QUERY_PASSWORD_FILE": str(secret),
    }
    settings = postgres_settings("query", env)
    assert settings.password == "from-file"


@pytest.mark.parametrize("port", ["not-a-number", "0", "65536"])
def test_postgres_settings_rejects_invalid_port(port: str) -> None:
    env = _base() | {"POSTGRES_PORT": port}
    with pytest.raises(ConfigError, match="POSTGRES_PORT"):
        postgres_settings("query", env)


def test_postgres_roles_do_not_share_credentials() -> None:
    env = _base() | {
        "POSTGRES_INGEST_USER": "ingest-role",
        "POSTGRES_INGEST_PASSWORD": "synthetic-ingest-password",
    }
    assert postgres_settings("query", env).user == "query-role"
    assert postgres_settings("ingest", env).user == "ingest-role"
