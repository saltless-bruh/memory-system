from __future__ import annotations

import json
import stat
from pathlib import Path

from scripts.bootstrap_secrets import SCOUT_TOKEN_MAP_NAME, SECRET_NAMES, ensure_secrets


def test_bootstrap_secrets_are_restrictive_and_not_overwritten(tmp_path: Path) -> None:
    secret_dir = tmp_path / ".secrets"
    created = ensure_secrets(secret_dir)
    managed_names = set(SECRET_NAMES) | {SCOUT_TOKEN_MAP_NAME}
    assert {path.name for path in created} == managed_names
    assert stat.S_IMODE(secret_dir.stat().st_mode) == 0o700
    originals = {path.name: path.read_bytes() for path in created}
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in created)

    token = (secret_dir / "scout_test_token").read_text(encoding="utf-8").strip()
    token_map = json.loads(
        (secret_dir / SCOUT_TOKEN_MAP_NAME).read_text(encoding="utf-8")
    )
    assert token_map == {
        token: {"subject": "integration-test", "departments": ["infra"]}
    }

    assert ensure_secrets(secret_dir) == []
    assert {name: (secret_dir / name).read_bytes() for name in managed_names} == originals

    token_before_refresh = (secret_dir / "scout_test_token").read_bytes()
    (secret_dir / SCOUT_TOKEN_MAP_NAME).write_text("{}\n", encoding="utf-8")
    refreshed = ensure_secrets(secret_dir, refresh_token_map=True)
    assert refreshed == [secret_dir / SCOUT_TOKEN_MAP_NAME]
    assert (secret_dir / "scout_test_token").read_bytes() == token_before_refresh
    refreshed_map = json.loads(
        (secret_dir / SCOUT_TOKEN_MAP_NAME).read_text(encoding="utf-8")
    )
    assert refreshed_map[token] == {
        "subject": "integration-test",
        "departments": ["infra"],
    }

    originals = {name: (secret_dir / name).read_bytes() for name in managed_names}
    rotated = ensure_secrets(secret_dir, rotate=True)
    assert {path.name for path in rotated} == managed_names
    assert all((secret_dir / name).read_bytes() != originals[name] for name in managed_names)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in rotated)
