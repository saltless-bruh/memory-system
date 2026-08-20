from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scout.config import ConfigError
from scripts.provision_postgres_roles import provision_roles


def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_QUERY_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("POSTGRES_INGEST_PASSWORD_FILE", raising=False)
    values = {
        "POSTGRES_HOST": "db",
        "POSTGRES_DB": "memory",
        "POSTGRES_QUERY_USER": "rag_app_role",
        "POSTGRES_QUERY_PASSWORD": "synthetic-query-value",
        "POSTGRES_INGEST_USER": "rag_ingest_role",
        "POSTGRES_INGEST_PASSWORD": "synthetic-ingest-value",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.asyncio
async def test_provision_uses_server_side_quoting(monkeypatch: pytest.MonkeyPatch) -> None:
    _environment(monkeypatch)
    conn = MagicMock()
    conn.fetchval = AsyncMock(
        side_effect=[
            True,
            "ALTER ROLE rag_app_role LOGIN PASSWORD 'redacted'",
            True,
            "ALTER ROLE rag_ingest_role LOGIN PASSWORD 'redacted'",
        ]
    )
    conn.execute = AsyncMock()
    assert await provision_roles(conn) == ["rag_app_role", "rag_ingest_role"]
    format_calls = [call for call in conn.fetchval.await_args_list if "format(" in call.args[0]]
    assert len(format_calls) == 2
    assert format_calls[0].args[1:] == ("rag_app_role", "synthetic-query-value")


@pytest.mark.asyncio
async def test_provision_rejects_unexpected_role_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _environment(monkeypatch)
    monkeypatch.setenv("POSTGRES_QUERY_USER", "postgres")
    with pytest.raises(ConfigError, match="rag_app_role"):
        await provision_roles(MagicMock())
