"""Repository-bound private Git credentials, exercised without network sockets."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import git_sync_credentials as helper
from scripts import host_sync


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    password = tmp_path / "sync-token"
    password.write_text("test-reader-credential\n")
    values = {
        "GIT_SYNC_URL": "http://git:3000/owner/wiki.git",
        "GIT_SYNC_USERNAME": "sync-reader",
        "GIT_SYNC_PASSWORD_FILE": str(password),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def request(**changes: str) -> str:
    fields = {"protocol": "http", "host": "git:3000", "path": "owner/wiki.git"}
    fields.update(changes)
    return "".join(f"{key}={value}\n" for key, value in fields.items()) + "\n"


def test_only_exact_repository_receives_credentials(configured: dict[str, str]) -> None:
    assert helper.credentials(request(), configured) == {
        "username": "sync-reader",
        "password": "test-reader-credential",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "attacker.invalid"},
        {"host": "git:3001"},
        {"path": "owner/other.git"},
        {"path": "owner/wiki.git/extra"},
        {"path": ""},
        {"protocol": "https"},
        {"username": "other-user"},
    ],
)
def test_other_targets_receive_nothing(
    configured: dict[str, str], changes: dict[str, str]
) -> None:
    # The positive control above proves credentials exist for the intended target.
    assert helper.credentials(request(**changes), configured) == {}


@pytest.mark.parametrize(
    "bad", ["protocol=http\nprotocol=https\n\n", "malformed\n", "x" * 8193]
)
def test_malformed_requests_fail_closed(configured: dict[str, str], bad: str) -> None:
    assert helper.credentials(bad, configured) == {}


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@git:3000/owner/wiki.git",
        "file:///owner/wiki.git",
        "http://git:3000/",
        "http://git:3000/owner/wiki.git?extra=1",
    ],
)
def test_invalid_config_is_rejected(configured: dict[str, str], url: str) -> None:
    with pytest.raises(ValueError, match="configuration"):
        helper.credentials(request(), {**configured, "GIT_SYNC_URL": url})


@pytest.mark.parametrize(
    "value", ["", "secret\ninjected=value", "secret\0value", "x" * 16385]
)
def test_invalid_secret_is_rejected(configured: dict[str, str], value: str) -> None:
    Path(configured["GIT_SYNC_PASSWORD_FILE"]).write_text(value)
    with pytest.raises(ValueError, match="credential value"):
        helper.credentials(request(), configured)


def test_real_git_credential_protocol(
    configured: dict[str, str], tmp_path: Path
) -> None:
    command = f"!{shlex.quote(sys.executable)} {shlex.quote(helper.__file__)}"
    environment = {
        **os.environ,
        **configured,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    args = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        f"credential.helper={command}",
        "-c",
        "credential.useHttpPath=true",
        "credential",
        "fill",
    ]
    allowed = subprocess.run(
        args,
        input=request(),
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
        timeout=10,
    )
    assert allowed.returncode == 0
    assert "password=test-reader-credential" in allowed.stdout
    denied = subprocess.run(
        args,
        input=request(path="owner/other.git"),
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
        timeout=10,
    )
    assert denied.returncode != 0
    assert "test-reader-credential" not in denied.stdout + denied.stderr


def test_error_output_does_not_disclose_secret(configured: dict[str, str]) -> None:
    Path(configured["GIT_SYNC_PASSWORD_FILE"]).write_text("private-fragment\ninjection")
    result = subprocess.run(
        [sys.executable, helper.__file__, "get"],
        input=request(),
        capture_output=True,
        text=True,
        env={**os.environ, **configured},
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Git sync credential unavailable\n"


def test_authentication_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SYNC_USERNAME", raising=False)
    monkeypatch.delenv("GIT_SYNC_PASSWORD_FILE", raising=False)
    assert host_sync._credential_options() == []
    monkeypatch.setenv("GIT_SYNC_USERNAME", "sync-reader")
    with pytest.raises(host_sync.SyncConfigurationError, match="configured together"):
        host_sync._credential_options()


def test_git_options_do_not_contain_password(configured: dict[str, str]) -> None:
    options = host_sync._credential_options()
    assert "credential.helper=" in options
    assert "credential.useHttpPath=true" in options
    assert "http.followRedirects=false" in options
    assert "test-reader-credential" not in " ".join(options)
