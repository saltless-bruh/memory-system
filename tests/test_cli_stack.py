"""Tests for the stack family: `up`, `down`, `status`, `logs`, `init`.

Docker is never invoked here. `_compose` is the seam, so what is asserted is the
decision layer: which states count as degraded, what a refusal carries, and that
`init` never puts a secret anywhere it could be read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands import stack  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402


def _config(tmp_path: Path) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, repo_root=tmp_path)


class _Completed:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


HEALTHY = "\n".join(
    [
        '{"Service":"postgres","State":"running","Status":"Up 2h","Health":"healthy"}',
        '{"Service":"scout","State":"running","Status":"Up 2h","Health":"healthy"}',
        '{"Service":"postgres-migrate","State":"exited","Status":"Exited (0)","ExitCode":0}',
    ]
)


def _findings(ok: bool = True):
    from scripts.preflight_stack import Finding

    return [
        Finding(check="container-dns", ok=ok, detail="d", remedy="" if ok else "fix"),
        Finding(check="image-revision", ok=True, detail="d"),
    ]


def test_a_completed_one_shot_is_not_a_stopped_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`postgres-migrate` runs the migrations and exits 0 by design.

    Reporting it as down makes `status` cry wolf on a healthy stack, and a check
    that is wrong on the happy path stops being read.
    """
    monkeypatch.setattr(stack, "_compose", lambda *a, **k: _Completed(HEALTHY))
    monkeypatch.setattr(
        "scripts.preflight_stack.collect_findings", lambda **k: _findings()
    )

    result = stack.status(config=_config(tmp_path))
    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["stopped"] == []
    assert result.data["status"] == "ok"


def test_a_one_shot_that_failed_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = HEALTHY.replace('"ExitCode":0', '"ExitCode":1')
    monkeypatch.setattr(stack, "_compose", lambda *a, **k: _Completed(rows))
    monkeypatch.setattr(
        "scripts.preflight_stack.collect_findings", lambda **k: _findings()
    )

    result = stack.status(config=_config(tmp_path))
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["stopped"] == ["postgres-migrate"]


def test_an_unhealthy_service_degrades_the_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = HEALTHY.replace('"Health":"healthy"', '"Health":"unhealthy"', 1)
    monkeypatch.setattr(stack, "_compose", lambda *a, **k: _Completed(rows))
    monkeypatch.setattr(
        "scripts.preflight_stack.collect_findings", lambda **k: _findings()
    )

    result = stack.status(config=_config(tmp_path))
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["unhealthy"] == ["postgres"]


def test_a_failing_preflight_degrades_a_running_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every container up is not the same as every route reachable."""
    monkeypatch.setattr(stack, "_compose", lambda *a, **k: _Completed(HEALTHY))
    monkeypatch.setattr(
        "scripts.preflight_stack.collect_findings", lambda **k: _findings(ok=False)
    )

    result = stack.status(config=_config(tmp_path))
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["preflight"][0]["remedy"] == "fix"


def test_down_without_confirm_stops_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        stack, "_compose", lambda *a, **k: calls.append(a) or _Completed()
    )

    with pytest.raises(CliError) as caught:
        stack.down(config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.CONFIRMATION_REQUIRED
    assert result.error is not None
    assert "postgres" in result.error.details["stateful_services"]
    assert calls == []


def test_a_missing_docker_binary_is_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    def _missing(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", _missing)
    with pytest.raises(CliError) as caught:
        stack.status(config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INFRASTRUCTURE
    assert result.error is not None and not result.error.retryable


def test_init_creates_secrets_and_never_prints_one(tmp_path: Path) -> None:
    result = stack.init(directory=str(tmp_path / "secrets"), config=_config(tmp_path))
    created = result.data["created"]
    assert created

    rendered = result.summary + repr(result.data)
    for name in created:
        value = (tmp_path / "secrets" / name).read_text(encoding="utf-8").strip()
        assert value  # the file really has content
        assert value not in rendered  # and none of it is in the output


def test_init_refuses_to_write_over_an_existing_set(tmp_path: Path) -> None:
    """Half-creating over an existing set is a state nobody asked for."""
    directory = tmp_path / "secrets"
    stack.init(directory=str(directory), config=_config(tmp_path))
    before = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }

    with pytest.raises(CliError) as caught:
        stack.init(directory=str(directory), config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.CONFLICT

    after = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }
    assert after == before


def test_rotation_requires_confirmation(tmp_path: Path) -> None:
    directory = tmp_path / "secrets"
    stack.init(directory=str(directory), config=_config(tmp_path))
    before = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }

    with pytest.raises(CliError) as caught:
        stack.init(directory=str(directory), rotate=True, config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.CONFIRMATION_REQUIRED

    after = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }
    assert after == before


def test_confirmed_rotation_replaces_every_secret(tmp_path: Path) -> None:
    directory = tmp_path / "secrets"
    stack.init(directory=str(directory), config=_config(tmp_path))
    before = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }

    stack.init(
        directory=str(directory), rotate=True, confirm=True, config=_config(tmp_path)
    )
    after = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }
    assert set(after) == set(before)
    assert all(
        after[name] != before[name] for name in before if name.endswith("token") or True
    )


def test_logs_puts_the_log_on_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        stack, "_compose", lambda *a, **k: _Completed("line one\nline two\n")
    )
    result = stack.logs("scout", config=_config(tmp_path))
    assert result.data["lines"] == ["line one", "line two"]
    assert result.summary == "line one\nline two"


# ── forwarding through the real dispatcher ───────────────────────────────────
#
# The tests above call the command functions directly, which is exactly why the
# forwarding promise went unchecked: cyclopts, not the function, decides whether
# `--build` ever reaches `*extra`. These go through `main`, the operator's path.


def _dispatch(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> tuple[int, list[list[str]]]:
    from scout.cli.app import main

    calls: list[list[str]] = []

    def _record(args: list[str], **_k: object) -> _Completed:
        calls.append(list(args))
        return _Completed("")

    monkeypatch.setattr(stack, "_compose", _record)
    monkeypatch.setattr(stack, "_services", lambda _cwd: [])
    monkeypatch.chdir(REPO_ROOT)
    return main([*argv, "-o", "json"]), calls


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["up", "--build"], ["up", "-d", "--build"]),
        (["up", "--build", "scout"], ["up", "-d", "--build", "scout"]),
        (["down", "--confirm", "-v"], ["down", "-v"]),
        (["down", "-v", "--confirm"], ["down", "-v"]),
        (
            ["logs", "sync-job", "--tail", "20"],
            ["logs", "--no-color", "sync-job", "--tail", "20"],
        ),
        (["logs", "--tail", "20"], ["logs", "--no-color", "--tail", "20"]),
        (
            ["logs", "--tail", "20", "sync-job"],
            ["logs", "--no-color", "--tail", "20", "sync-job"],
        ),
    ],
)
def test_unrecognised_flags_reach_compose_through_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: list[str]
) -> None:
    """The module docstring promises `snpmemory up --build` just works."""
    code, calls = _dispatch(monkeypatch, argv)
    assert code == ExitCode.SUCCESS
    assert calls == [expected]


def test_logs_names_the_service_only_when_it_comes_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In `--tail 20 scout` only compose knows `20` is a value; never guess."""
    monkeypatch.setattr(stack, "_compose", lambda *a, **k: _Completed("x\n"))
    first = stack.logs("scout", "--tail", "5", config=_config(tmp_path))
    assert first.data["service"] == "scout"
    flag_first = stack.logs("--tail", "5", "scout", config=_config(tmp_path))
    assert flag_first.data["service"] is None


def test_down_still_refuses_without_confirm_when_flags_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forwarding must not let `--confirm` slip into compose's argv unread."""
    code, calls = _dispatch(monkeypatch, ["down", "-v"])
    assert code == ExitCode.CONFIRMATION_REQUIRED
    assert calls == []
