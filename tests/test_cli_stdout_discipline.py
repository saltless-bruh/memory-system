"""`snpmemory -o json` must put exactly one document on stdout.

`scout/cli/render.py` owns stdout: it writes the payload, and everything else
goes to stderr. Several commands call a script's `main` in-process (propose,
verify-groundedness, publish), and those scripts print. Each such print reached
stdout ahead of the payload, so `-o json` could not be parsed and every scripted
caller had to scrape. These tests hold the dispatcher to the rule whatever a
command's implementation does, and hold the stack commands' compose children to
it too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.result import ExitCode  # noqa: E402


def test_a_command_that_prints_cannot_corrupt_the_json_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through the real dispatcher: an in-process script's print is not data."""
    from scout.cli.app import main

    def _noisy_script(argv: list[str]) -> int:
        print("Committed exact proposal scope to wiki/x")  # noqa: T201
        return 0

    monkeypatch.setattr("scripts.propose_page._normalize_page", lambda p: p)
    monkeypatch.setattr("scripts.propose_page._staged_paths", lambda: set())
    monkeypatch.setattr(
        "scripts.propose_page.wiki_changes", lambda allowed: ["wiki/concepts/x.md"]
    )
    monkeypatch.setattr("scripts.propose_page.main", _noisy_script)
    monkeypatch.chdir(REPO_ROOT)

    code = main(["propose", "--page", "wiki/concepts/x.md", "--confirm", "-o", "json"])

    captured = capsys.readouterr()
    assert code == ExitCode.SUCCESS
    assert json.loads(captured.out)["status"] == "proposed"
    assert "Committed exact proposal scope" in captured.err


def test_help_still_goes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """The guard covers a command's run, not cyclopts' own `--help` output,
    which a reader pipes into a pager."""
    from scout.cli.app import main

    assert main(["--help"]) == ExitCode.SUCCESS
    assert "snpmemory" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("command", "argv"),
    [("up", ()), ("down", ("-v",))],
)
def test_compose_passthrough_output_is_relayed_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    command: str,
    argv: tuple[str, ...],
) -> None:
    """`up` and `down` let compose talk to the operator directly, but a child
    inherits file descriptor 1 unless told otherwise, and whatever compose puts
    there would precede the payload. Only its stdout is taken; its stderr, where
    compose writes progress, stays live."""
    from scout.cli.commands import stack
    from scout.cli.config import Config
    from scout.cli.registry import Prerequisite

    seen: dict[str, Any] = {}

    def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "compose said this\n", "")

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(stack, "_services", lambda _cwd: [])
    config = Config(prerequisite=Prerequisite.LOCAL, repo_root=tmp_path)

    if command == "up":
        stack.up(*argv, config=config)
    else:
        stack.down(*argv, confirm=True, config=config)

    captured = capsys.readouterr()
    assert seen.get("stdout") is subprocess.PIPE
    assert "stderr" not in seen and not seen.get("capture_output")
    assert captured.out == ""
    assert "compose said this" in captured.err
