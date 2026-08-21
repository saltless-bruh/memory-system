"""Tests for the `snpmemory` dispatcher, renderer, and result contract.

These assert the rules in `docs/CLI_SPEC.md` that exist because three different
consumers read this tool at once. Most of them would pass silently if the rule
were violated in the obvious way, which is why they check bytes and exit codes
rather than appearances.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.app import main  # noqa: E402
from scout.cli.errors import (  # noqa: E402
    CliError,
    answer_required,
    confirmation_required,
    tty_required,
)
from scout.cli.render import OutputFormat, render, use_color  # noqa: E402
from scout.cli.result import (  # noqa: E402
    CommandResult,
    ErrorKind,
    ExitCode,
)


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = main(argv)
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return code, out.getvalue(), err.getvalue()


# ── exit-code contract ───────────────────────────────────────────────────────


def test_every_error_kind_maps_to_its_own_code() -> None:
    """No code serves two kinds; an agent branching on either stays correct."""
    codes = [kind.exit_code for kind in ErrorKind]
    assert len(set(codes)) == len(codes)


def test_auth_is_four_not_two() -> None:
    """`2` is reserved for infrastructure by an inherited, load-bearing promise.

    README and ci_address_gate guarantee that exit 2 never triggers mutation.
    The prevailing external convention puts auth at 2; adopting it would break
    that guarantee, so auth lives at 4.
    """
    assert ErrorKind.AUTH.exit_code is ExitCode.AUTH == 4
    assert ErrorKind.INFRASTRUCTURE.exit_code == 2


def test_outcomes_permit_mutation_and_errors_do_not() -> None:
    assert CommandResult(exit_code=ExitCode.SUCCESS).mutating_is_allowed
    assert CommandResult(
        exit_code=ExitCode.SEMANTIC_FAILURE
    ).mutating_is_allowed, "drift is a finding to act on, not a reason to stop"
    for kind in ErrorKind:
        assert not CommandResult.failure(kind, "x").mutating_is_allowed


def test_error_code_requires_a_matching_envelope() -> None:
    with pytest.raises(ValueError):
        CommandResult(exit_code=ExitCode.AUTH)  # error code, no envelope
    with pytest.raises(ValueError):
        CommandResult(
            exit_code=ExitCode.SUCCESS,
            error=CliError(ErrorKind.AUTH, "x").to_result().error,
        )


def test_envelope_kind_and_exit_code_cannot_disagree() -> None:
    """A mismatch would make the code and the name tell different stories."""
    envelope = CommandResult.failure(ErrorKind.AUTH, "x").error
    with pytest.raises(ValueError):
        CommandResult(exit_code=ExitCode.CONFLICT, error=envelope)


# ── stream discipline ────────────────────────────────────────────────────────


def test_data_goes_to_stdout_and_diagnostics_to_stderr() -> None:
    out, err = io.StringIO(), io.StringIO()
    render(
        CommandResult(data={"k": "v"}, summary="done", messages=("working...",)),
        OutputFormat.JSON,
        stdout=out,
        stderr=err,
    )
    assert json.loads(out.getvalue()) == {"k": "v"}
    assert "working..." in err.getvalue()
    assert "working..." not in out.getvalue(), "progress must never enter a pipe"


def test_errors_never_touch_stdout() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = render(
        CommandResult.failure(ErrorKind.AUTH, "denied", hint="use a broader token"),
        OutputFormat.TEXT,
        stdout=out,
        stderr=err,
    )
    assert code == 4
    assert out.getvalue() == ""
    assert "denied" in err.getvalue()


def test_structured_error_envelope_is_the_last_line_of_stderr() -> None:
    """A reader takes the final line and parses it, without scanning the rest."""
    out, err = io.StringIO(), io.StringIO()
    render(
        CommandResult.failure(
            ErrorKind.CONFLICT, "page exists", hint="pass --overwrite", details={"page": "x.md"}
        ),
        OutputFormat.JSON,
        stdout=out,
        stderr=err,
    )
    envelope = json.loads(err.getvalue().strip().splitlines()[-1])
    assert envelope["kind"] == "conflict"
    assert envelope["message"] == "page exists"
    assert envelope["hint"] == "pass --overwrite"
    assert envelope["details"] == {"page": "x.md"}


def test_text_mode_emits_no_json_envelope() -> None:
    out, err = io.StringIO(), io.StringIO()
    render(CommandResult.failure(ErrorKind.AUTH, "denied"), OutputFormat.TEXT,
           stdout=out, stderr=err)
    with pytest.raises(json.JSONDecodeError):
        json.loads(err.getvalue().strip().splitlines()[-1])


# ── format resolution ────────────────────────────────────────────────────────


def test_auto_resolves_to_text_even_when_piped() -> None:
    """Deliberate divergence: CI and agent workflows read today's text output."""
    assert OutputFormat.AUTO.resolve() is OutputFormat.TEXT
    assert not OutputFormat.AUTO.is_structured


def test_explicit_format_wins() -> None:
    assert OutputFormat.JSON.resolve() is OutputFormat.JSON
    assert OutputFormat.YAML.is_structured


# ── colour ───────────────────────────────────────────────────────────────────


def test_no_color_when_not_a_tty() -> None:
    assert use_color(io.StringIO()) is False


def test_no_color_env_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    assert use_color(_Tty()) is False


def test_explicit_flag_overrides_detection() -> None:
    assert use_color(io.StringIO(), force=True) is True


# ── dispatcher ───────────────────────────────────────────────────────────────


def test_unknown_command_is_input_validation_not_a_finding() -> None:
    """cyclopts exits 1 by default; here 1 means 'real problems were found'."""
    code, out, err = _run(["definitely-not-a-command"])
    assert code == ExitCode.INPUT_VALIDATION == 3
    assert out == ""
    assert err


def test_unknown_output_format_is_rejected() -> None:
    code, _, err = _run(["schema", "-o", "toml"])
    assert code == 3
    assert "toml" in err


def test_output_flag_without_a_value_is_rejected() -> None:
    code, _, _ = _run(["schema", "-o"])
    assert code == 3


def test_no_ansi_escape_reaches_either_stream() -> None:
    for argv in (["schema"], ["definitely-not-a-command"]):
        _, out, err = _run(argv)
        assert "\x1b[" not in out, f"ANSI on stdout for {argv}"
        assert "\x1b[" not in err, f"ANSI on stderr for {argv}"


# ── schema ───────────────────────────────────────────────────────────────────


def test_schema_runs_with_no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """It must answer before anything else does: no creds, no database, no stack."""
    for name in (
        "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_QUERY_USER",
        "POSTGRES_QUERY_PASSWORD_FILE", "LITELLM_BASE_URL", "LITELLM_MASTER_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    code, out, _ = _run(["schema", "-o", "json"])
    assert code == 0
    document = json.loads(out)
    assert document["tool"] == "snpmemory"
    assert {c["name"] for c in document["commands"]} >= {"schema"}


def test_schema_publishes_every_exit_code_with_its_class() -> None:
    _, out, _ = _run(["schema", "-o", "json"])
    codes = {c["code"]: c for c in json.loads(out)["exit_codes"]}
    assert codes[1]["class"] == "outcome"
    assert codes[2]["class"] == "error"
    assert codes[2]["mutating_allowed"] is False


def test_schema_declares_the_error_envelope_shape() -> None:
    """So an agent can rely on the envelope without discovering it by failing."""
    _, out, _ = _run(["schema", "-o", "json"])
    envelope = json.loads(out)["error_envelope"]
    assert envelope["stream"] == "stderr"
    assert envelope["position"] == "last line"
    assert set(envelope["required"]) == {"kind", "message"}


# ── interaction rules ────────────────────────────────────────────────────────


def test_missing_answer_without_interactive_is_input_validation() -> None:
    """A terminal is permission to render a prompt, not a reason to need one."""
    error = answer_required("client selection", flag="--client")
    result = error.to_result()
    assert result.exit_code == 3
    assert "--client" in (result.error.hint or "")
    assert "--interactive" in (result.error.hint or "")


def test_interactive_without_a_tty_is_its_own_code() -> None:
    result = tty_required("client selection", flag="--client").to_result()
    assert result.exit_code == ExitCode.TTY_REQUIRED == 6


def test_destructive_action_refuses_rather_than_proceeding() -> None:
    result = confirmation_required("purge the vault").to_result()
    assert result.exit_code == ExitCode.CONFIRMATION_REQUIRED == 5
    assert "--yes" in (result.error.hint or "")


# ── secrets ──────────────────────────────────────────────────────────────────


def test_unexpected_failures_do_not_leak_their_message() -> None:
    """A driver error can carry a DSN or a token; only the type is reported."""
    from scout.cli.app import _to_result

    secret = "postgresql://user:hunter2@db:5432/snp"
    result = _to_result(RuntimeError(f"connection failed for {secret}"))
    rendered = json.dumps(result.error.to_dict()) + result.summary
    assert "hunter2" not in rendered
    assert secret not in rendered
    assert result.exit_code == ExitCode.INFRASTRUCTURE


def test_multi_word_flags_parse() -> None:
    """`--max-depth` must map onto `max_depth`, and `--dry-run` must be a flag.

    Regression: registering commands through a generic `(*args, **kwargs)`
    wrapper left cyclopts with no signature to parse from. It did not degrade
    gracefully — every multi-word option in the tool broke, `--dry-run`
    demanding a value and `--max-depth` arriving as a keyword literally named
    `max-depth`.
    """
    import inspect

    from scout.cli.app import _build_app
    from scout.cli.declarations import DECLARED

    app = _build_app()
    assert app is not None

    from scout.cli.app import _wrap

    for spec in DECLARED:
        runner = _wrap(spec)
        signature = inspect.signature(runner)
        # A real signature, not (*args, **kwargs)
        kinds = {p.kind for p in signature.parameters.values()}
        assert kinds != {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }, f"{spec.name} registered without a usable signature"


def test_importing_every_command_module_reads_no_environment() -> None:
    """Loading a command must not run dotenv or resolve a credential.

    Commands are now loaded at registration so cyclopts can parse from their
    signatures. That is only safe while each command module keeps its heavy
    imports inside its functions — this test is what holds them to it.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    probe = (
        "import os, sys;"
        "before = set(os.environ);"
        "from scout.cli.declarations import DECLARED;"
        "[s.load() for s in DECLARED];"
        "added = sorted(set(os.environ) - before);"
        "print(added);"
        "sys.exit(1 if added else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONWARNINGS": "ignore"},
    )
    assert result.returncode == 0, (
        f"loading commands added environment variables: {result.stdout}{result.stderr}"
    )
