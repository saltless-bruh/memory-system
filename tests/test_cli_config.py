"""Tests for prerequisite-gated configuration resolution.

The behaviour being replaced is concrete: importing `scripts/verify_addresses.py`
calls `dotenv.load_dotenv()` at module scope, putting thirty variables into
`os.environ` — `GEMINI_API_KEY` among them — as a side effect of an import, for
every command, whether it needed them or not. These tests pin the two properties
that fix it: a command sees only what its class allows, and `os.environ` is
never written.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.config import (  # noqa: E402
    CONNECTION_KEYS,
    REPO_KEYS,
    Config,
    allowed_keys,
    find_repo_root,
    resolve,
)
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ErrorKind  # noqa: E402


def _repo(tmp_path: Path, env_body: str = "") -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "AGENTS.md").write_text("# contract\n")
    if env_body:
        (tmp_path / ".env").write_text(env_body)
    return tmp_path


# ── gating ───────────────────────────────────────────────────────────────────


def test_none_receives_nothing_at_all(tmp_path: Path) -> None:
    """`schema` must answer on a machine with no credentials and no stack."""
    _repo(tmp_path, "POSTGRES_HOST=db\nGEMINI_API_KEY=sekrit\nSCOUT_URL=http://x\n")
    config = resolve(
        Prerequisite.NONE,
        environ={"SCOUT_URL": "http://y", "GEMINI_API_KEY": "sekrit"},
        start=tmp_path,
    )
    assert dict(config.values) == {}
    assert config.repo_root is None, "NONE must not even walk the filesystem"


def test_remote_gets_connection_keys_only(tmp_path: Path) -> None:
    """A command that only calls rag_fetch has no business holding a DB password."""
    config = resolve(
        Prerequisite.REMOTE,
        environ={
            "SCOUT_URL": "http://scout:8080",
            "POSTGRES_QUERY_PASSWORD": "hunter2",
            "LITELLM_MASTER_KEY": "sk-live",
            "GEMINI_API_KEY": "sekrit",
        },
        start=tmp_path,
    )
    assert config.get("SCOUT_URL") == "http://scout:8080"
    for forbidden in ("POSTGRES_QUERY_PASSWORD", "LITELLM_MASTER_KEY", "GEMINI_API_KEY"):
        assert config.get(forbidden) is None, f"{forbidden} leaked into a REMOTE command"


def test_local_gets_the_repo_set(tmp_path: Path) -> None:
    config = resolve(
        Prerequisite.LOCAL,
        environ={"POSTGRES_HOST": "db", "SCOUT_URL": "http://s"},
        start=_repo(tmp_path),
    )
    assert config.get("POSTGRES_HOST") == "db"
    assert config.get("SCOUT_URL") == "http://s"


def test_provider_keys_are_in_no_allowlist() -> None:
    """GEMINI_API_KEY reaches the gateway through compose, never through the CLI."""
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WEBHOOK_SECRET"):
        assert key not in REPO_KEYS
        assert key not in CONNECTION_KEYS


def test_allowlists_widen_monotonically() -> None:
    assert allowed_keys(Prerequisite.NONE) < allowed_keys(Prerequisite.REMOTE)
    assert allowed_keys(Prerequisite.REMOTE) < allowed_keys(Prerequisite.LOCAL)


# ── no global mutation ───────────────────────────────────────────────────────


def test_resolution_never_writes_to_os_environ(tmp_path: Path) -> None:
    """Configuration is a returned value, not a side effect on global state."""
    _repo(tmp_path, "POSTGRES_HOST=from-file\nLITELLM_MASTER_KEY=sk-file\n")
    before = dict(os.environ)
    resolve(Prerequisite.LOCAL, environ={}, start=tmp_path)
    assert dict(os.environ) == before


# ── precedence ───────────────────────────────────────────────────────────────


def test_process_environment_beats_the_project_file(tmp_path: Path) -> None:
    _repo(tmp_path, "POSTGRES_HOST=from-file\n")
    config = resolve(Prerequisite.LOCAL, environ={"POSTGRES_HOST": "from-env"}, start=tmp_path)
    assert config.get("POSTGRES_HOST") == "from-env"


def test_explicit_override_beats_everything(tmp_path: Path) -> None:
    """Same rule as --interactive: an explicit answer always wins."""
    _repo(tmp_path, "POSTGRES_HOST=from-file\n")
    config = resolve(
        Prerequisite.LOCAL,
        overrides={"POSTGRES_HOST": "from-flag"},
        environ={"POSTGRES_HOST": "from-env"},
        start=tmp_path,
    )
    assert config.get("POSTGRES_HOST") == "from-flag"


def test_remote_ignores_a_project_env_file(tmp_path: Path) -> None:
    """A REMOTE command may be running inside somebody else's project."""
    _repo(tmp_path, "SCOUT_URL=http://not-mine\n")
    config = resolve(Prerequisite.REMOTE, environ={}, start=tmp_path)
    assert config.get("SCOUT_URL") is None


def test_local_reads_the_project_file_when_env_is_silent(tmp_path: Path) -> None:
    _repo(tmp_path, "POSTGRES_HOST=from-file\n")
    config = resolve(Prerequisite.LOCAL, environ={}, start=tmp_path)
    assert config.get("POSTGRES_HOST") == "from-file"


def test_env_file_cannot_smuggle_a_disallowed_key(tmp_path: Path) -> None:
    """The allowlist applies to the file as much as to the environment."""
    _repo(tmp_path, "GEMINI_API_KEY=sekrit\nPOSTGRES_HOST=db\n")
    config = resolve(Prerequisite.LOCAL, environ={}, start=tmp_path)
    assert config.get("GEMINI_API_KEY") is None
    assert config.get("POSTGRES_HOST") == "db"


# ── repository detection ─────────────────────────────────────────────────────


def test_repo_root_found_from_a_subdirectory(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == root.resolve()


def test_no_repo_outside_a_checkout(tmp_path: Path) -> None:
    assert find_repo_root(tmp_path) is None


def test_require_repo_explains_itself(tmp_path: Path) -> None:
    config = resolve(Prerequisite.LOCAL, environ={}, start=tmp_path)
    with pytest.raises(CliError) as caught:
        config.require_repo()
    assert caught.value.kind is ErrorKind.INPUT_VALIDATION
    assert "checkout" in caught.value.message
    assert "clone" in (caught.value.hint or ""), "the hint must say how to fix it"


# ── errors name the fix ──────────────────────────────────────────────────────


def test_missing_key_names_what_to_set(tmp_path: Path) -> None:
    config = resolve(Prerequisite.LOCAL, environ={}, start=_repo(tmp_path))
    with pytest.raises(CliError) as caught:
        config.require("POSTGRES_HOST")
    assert "POSTGRES_HOST" in (caught.value.hint or "")


def test_asking_beyond_the_class_says_so(tmp_path: Path) -> None:
    """A REMOTE command asking for a database password is a declaration bug."""
    config = resolve(Prerequisite.REMOTE, environ={}, start=tmp_path)
    with pytest.raises(CliError) as caught:
        config.require("POSTGRES_QUERY_PASSWORD")
    assert "remote" in caught.value.message
    assert "prerequisite" in (caught.value.hint or "")


# ── secrets ──────────────────────────────────────────────────────────────────


def test_repr_redacts_every_secret_shaped_key() -> None:
    config = Config(
        prerequisite=Prerequisite.LOCAL,
        values={
            "POSTGRES_HOST": "db",
            "POSTGRES_QUERY_PASSWORD": "hunter2",
            "LITELLM_MASTER_KEY": "sk-live-abc",
            "SCOUT_AUTH_TOKEN": "tok-abc",
        },
    )
    rendered = repr(config)
    for secret in ("hunter2", "sk-live-abc", "tok-abc"):
        assert secret not in rendered, "a traceback would have spilled this"
    assert "db" in rendered
    assert rendered.count("<redacted>") == 3
