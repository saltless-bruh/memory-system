"""Tests for `snpmemory install-agent`.

The property most worth protecting: this command writes instructions into a
directory somebody else owns. A target that already has an `.agent/` is somebody
else's configured project, and replacing files there without being asked is the
failure mode — so the refusal, and the fact that `--dry-run` reaches nothing, are
what these tests mostly pin.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.agent import install_agent  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402


def _config() -> Config:
    """The command needs a real checkout: it runs this repo's own installer."""
    return Config(prerequisite=Prerequisite.LOCAL, repo_root=REPO_ROOT)


def _code(caught: pytest.ExceptionInfo[CliError]) -> ExitCode:
    return caught.value.to_result().exit_code


def test_a_target_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(CliError) as caught:
        install_agent(str(target), config=_config())

    assert _code(caught) == ExitCode.INPUT_VALIDATION


def test_a_missing_target_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path / "absent"), config=_config())

    assert _code(caught) == ExitCode.INPUT_VALIDATION


def test_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    result = install_agent(str(tmp_path), dry_run=True, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "dry_run"
    assert result.data["client"] == "portable"
    assert any(".agent/skills" in path for path in result.data["would_write"])
    assert list(tmp_path.iterdir()) == [], "a dry run must touch nothing"


def test_installing_into_an_empty_directory_needs_no_confirmation(
    tmp_path: Path,
) -> None:
    result = install_agent(str(tmp_path), config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "installed"
    assert result.data["client"] == "portable"
    assert (tmp_path / ".agent" / "rules" / "snp-memory.md").is_file()
    assert (tmp_path / ".mcp.json").is_file()


def test_a_target_that_already_has_an_agent_directory_is_refused(
    tmp_path: Path,
) -> None:
    """Somebody else's configured project. Replacing files there is not implicit."""
    (tmp_path / ".agent" / "rules").mkdir(parents=True)
    theirs = tmp_path / ".agent" / "rules" / "theirs.md"
    theirs.write_text("their rule\n", encoding="utf-8")

    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path), config=_config())

    assert _code(caught) == ExitCode.CONFIRMATION_REQUIRED
    assert theirs.read_text(encoding="utf-8") == "their rule\n"
    assert not (tmp_path / ".mcp.json").exists()


def test_confirm_installs_over_an_existing_agent_directory(tmp_path: Path) -> None:
    (tmp_path / ".agent" / "rules").mkdir(parents=True)
    (tmp_path / ".agent" / "rules" / "theirs.md").write_text("keep\n", encoding="utf-8")

    result = install_agent(str(tmp_path), confirm=True, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    # The installer is non-destructive: it copies alongside rather than wiping.
    assert (tmp_path / ".agent" / "rules" / "theirs.md").is_file()
    assert (tmp_path / ".agent" / "rules" / "snp-memory.md").is_file()


def test_the_installed_project_gets_the_two_v3_servers(tmp_path: Path) -> None:
    """`snp-wiki` was basic-memory, and V3 removed it from the stack.

    An installed project must be handed the two servers that exist, and must
    not be told to connect to the retired one — a stale entry here points a
    fresh checkout at a port nothing listens on.
    """
    result = install_agent(str(tmp_path), config=_config())

    assert result.data["servers"] == ["scout", "snpmemory"]
    written = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert set(written["mcpServers"]) == {"scout", "snpmemory"}


def test_installing_is_idempotent(tmp_path: Path) -> None:
    install_agent(str(tmp_path), config=_config())
    before = (tmp_path / ".mcp.json").read_bytes()

    result = install_agent(str(tmp_path), confirm=True, config=_config())

    assert result.exit_code == ExitCode.SUCCESS
    assert (tmp_path / ".mcp.json").read_bytes() == before


def test_the_repo_local_layer_is_not_installed(tmp_path: Path) -> None:
    """A consumer's project must not acquire this repository's own workflow."""
    install_agent(str(tmp_path), config=_config())

    installed = [p.name for p in (tmp_path / ".agent" / "skills").iterdir()]
    assert installed, "nothing was installed"
    assert not any(name.startswith("superpowers-") for name in installed)


def test_install_agent_is_declared_and_hidden_from_the_tool_surface() -> None:
    from scout.cli.declarations import DECLARED
    from scout.cli.mcp_policy import Exposure, policy_for

    assert any(spec.name == "install-agent" for spec in DECLARED)
    policy = policy_for("install-agent")
    assert policy is not None and policy.exposure is Exposure.HIDDEN


def test_native_install_uses_the_shared_shell_and_reports_the_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project with spaces"
    target.mkdir()
    real_run = subprocess.run
    calls: list[list[str]] = []

    def record_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", record_run)

    result = install_agent(str(target), client="opencode", config=_config())

    assert calls == [
        [
            str(REPO_ROOT / "scripts/install-agent.sh"),
            "--client",
            "opencode",
            "--",
            str(target),
        ]
    ]
    assert result.exit_code == ExitCode.SUCCESS
    assert result.data == {
        "status": "installed",
        "client": "opencode",
        "target": str(target),
        "package": str(REPO_ROOT / "packages/snp-agent"),
        "servers": ["scout"],
    }
    assert len(list((target / ".opencode/skills").glob("*/SKILL.md"))) == 7
    assert not (target / ".agent").exists()


def test_native_cli_preview_preserves_existing_files_and_skips_shell_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_agent(str(tmp_path), client="opencode", config=_config())
    before = {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }

    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("a CLI dry-run must not run the shell installer")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    result = install_agent(
        str(tmp_path), client="opencode", dry_run=True, config=_config()
    )

    assert result.data["client"] == "opencode"
    assert result.data["status"] == "dry_run"
    assert str(tmp_path / "opencode.json") in result.data["would_write"]
    assert "servers" not in result.data
    assert before == {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }


def test_native_cli_reinstall_requires_confirm_but_preview_does_not(
    tmp_path: Path,
) -> None:
    install_agent(str(tmp_path), client="opencode", config=_config())
    config_path = tmp_path / "opencode.json"
    before = config_path.read_bytes()

    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path), client="opencode", config=_config())

    assert _code(caught) == ExitCode.CONFIRMATION_REQUIRED
    assert config_path.read_bytes() == before
    assert install_agent(
        str(tmp_path), client="opencode", dry_run=True, config=_config()
    ).ok
    assert install_agent(
        str(tmp_path), client="opencode", confirm=True, config=_config()
    ).ok
    assert config_path.read_bytes() == before


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("problem", ["json", "jsonc", "mcp", "instructions", "symlink"])
def test_native_cli_returns_conflict_before_calling_shell_for_invalid_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirm: bool, problem: str
) -> None:
    contents = {
        "json": "{private-config-value",
        "jsonc": "// private-config-value\n{}",
        "mcp": '{"mcp":["private-config-value"]}',
        "instructions": '{"instructions":"private-config-value"}',
    }
    if problem == "symlink":
        (tmp_path / ".opencode").symlink_to(
            tmp_path / "missing", target_is_directory=True
        )
    else:
        name = "opencode.jsonc" if problem == "jsonc" else "opencode.json"
        (tmp_path / name).write_text(contents[problem])

    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid targets must fail before calling the installer")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    with pytest.raises(CliError) as caught:
        install_agent(
            str(tmp_path), client="opencode", confirm=confirm, config=_config()
        )

    assert _code(caught) == ExitCode.CONFLICT
    assert "reconcile" in (caught.value.hint or "")
    assert "private-config-value" not in str(caught.value.to_result())
    assert len(list(tmp_path.iterdir())) == 1


def test_native_cli_merges_custom_config_and_keeps_its_values_out_of_output(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "opencode.json"
    config_path.write_text(
        json.dumps(
            {"mcp": {"custom": {"headers": {"Authorization": "Bearer private-token"}}}}
        )
    )
    (tmp_path / "AGENTS.md").write_text("Keep the human instructions")
    (tmp_path / ".agent").mkdir()

    result = install_agent(str(tmp_path), client="opencode", config=_config())

    assert result.data["servers"] == ["custom", "scout"]
    assert "private-token" in config_path.read_text()
    assert "private-token" not in str(result)
    assert (tmp_path / "AGENTS.md").read_text() == "Keep the human instructions"
    assert list((tmp_path / ".agent").iterdir()) == []


def test_unknown_install_client_is_input_validation(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path), client="unknown", config=_config())

    assert _code(caught) == ExitCode.INPUT_VALIDATION
    assert "portable or opencode" in (caught.value.hint or "")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("code", [2, 3, 5, 7, 99])
def test_native_shell_errors_keep_the_cli_exit_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def failed_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, "", "private-helper-error")

    monkeypatch.setattr(subprocess, "run", failed_run)

    with pytest.raises(CliError) as caught:
        install_agent(str(tmp_path), client="opencode", config=_config())

    assert _code(caught) == (code if code in {2, 3, 5, 7} else 2)
    assert "private-helper-error" not in str(caught.value.to_result())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_dispatch_accepts_native_target_and_emits_one_json_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    from scout.cli.app import main

    # This local installer needs the checkout path, never its .env credentials.
    monkeypatch.setattr("scout.cli.invoke.resolve_config", lambda *_args: _config())
    argv = ["install-agent", str(tmp_path), "--client", "opencode", "--output", "json"]
    if dry_run:
        argv.append("--dry-run")

    code = main(argv)

    captured = capsys.readouterr()
    assert code == 0, captured.err
    document = json.loads(captured.out)
    assert document["client"] == "opencode"
    assert document["status"] == ("dry_run" if dry_run else "installed")
    if dry_run:
        assert not list(tmp_path.iterdir())


def test_installer_and_exporter_schema_declarations_include_supported_clients() -> None:
    from scout.cli.commands.schema import schema
    from scripts.export_mcp_config import SUPPORTED_CLIENTS

    commands = {item["name"]: item for item in schema().data["commands"]}
    installer_spec = commands["install-agent"]
    installer_args = {item["name"]: item for item in installer_spec["args"]}
    installer_fields = {item["name"]: item for item in installer_spec["output_fields"]}
    assert installer_args["--client"]["enum"] == ["portable", "opencode"]
    assert installer_args["--client"]["default"] == "portable"
    assert installer_fields["client"]["enum"] == ["portable", "opencode"]
    assert {"status", "target", "package", "servers", "would_write"} <= set(
        installer_fields
    )
    assert set(installer_spec["errors"]) == {
        "input_validation",
        "confirmation_required",
        "conflict",
        "infrastructure",
    }
    exporter = commands["mcp-config"]
    exporter_args = {item["name"]: item for item in exporter["args"]}
    exporter_fields = {item["name"]: item for item in exporter["output_fields"]}
    assert exporter_args["--client"]["enum"] == SUPPORTED_CLIENTS
    assert exporter_fields["client"]["enum"] == SUPPORTED_CLIENTS
