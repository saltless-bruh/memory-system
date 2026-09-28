"""`snpmemory schema` must describe what the tool really accepts and emits.

`test_cli_schema_conformance.py` checks that the schema document is *well
formed*: it validates against The CLI Spec and its references resolve. That is
one direction only. Nothing compared the declarations to the code they describe,
so the two drifted both ways -- a global flag published that nothing parsed, a
command accepting three flags the schema never mentioned, a field declared as an
array that arrives as a dict, a state the enum did not list.

An agent follows the schema (that is its stated purpose, CLI_SPEC.md §2). Every
test here reads the real parser, the real signature, or a real payload, and
holds the declaration to it.
"""

from __future__ import annotations

import inspect
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.declarations import DECLARED  # noqa: E402
from scout.cli.registry import GLOBAL_ARGS, FieldSpec, Prerequisite  # noqa: E402
from scout.cli.result import ErrorKind, ExitCode  # noqa: E402

_POSITIONAL = (
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
)


def _accepted(name: str) -> set[str]:
    """Every argument name the real parser accepts for one command.

    Positionals are named as the schema names them (bare); options by every
    spelling cyclopts will match, which is how an undeclared `--no-*` negation
    shows up.
    """
    from scout.cli.app import _build_app

    app = _build_app()
    accepted: set[str] = set()
    # A real parse layers the root app's `default_parameter` under the
    # command's; assembling the command alone would drop it and report
    # spellings `snpmemory` itself refuses.
    collection = app[name].assemble_argument_collection(
        default_parameter=app.default_parameter
    )
    for argument in collection:
        if not argument.parse:
            continue  # injected by the dispatcher, never typed
        if argument.field_info.kind in _POSITIONAL:
            accepted.add(argument.field_info.name)
        else:
            accepted.update(argument.names)
    return accepted


@pytest.mark.parametrize("spec", DECLARED, ids=lambda spec: spec.name)
def test_declared_arguments_match_the_parser_both_ways(spec: Any) -> None:
    declared = {arg.name for arg in spec.args}
    accepted = _accepted(spec.name)
    assert accepted - declared == set(), "accepted but not in the schema"
    assert declared - accepted == set(), "in the schema but not accepted"


def _main(argv: list[str]) -> tuple[int, str]:
    from scout.cli.app import main

    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        ["schema", "--no-color", "-o", "json"],
        ["schema", "--output=json"],
        ["schema", "--format=json"],
        ["--no-color", "schema", "-o", "json"],
    ],
)
def test_every_published_global_argument_is_accepted(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """`global_args` is a promise made for every command, `schema` included."""
    # Set, not deleted: `main` exports NO_COLOR, and only a recorded value is
    # restored at teardown, so it cannot leak into the rest of the session.
    monkeypatch.setenv("NO_COLOR", "")
    code, stdout = _main(argv)
    assert code == ExitCode.SUCCESS
    assert json.loads(stdout)["name"] == "snpmemory"
    assert {arg.name for arg in GLOBAL_ARGS} == {"--output", "--no-color"}


def test_no_color_reaches_the_processes_a_command_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--no-color` must mean something beyond being tolerated.

    This tool writes no ANSI of its own; the colour that can reach a terminal
    comes from what it runs -- compose, git. `NO_COLOR` is how those are told.
    """
    from scout.cli.render import use_color

    monkeypatch.setenv("NO_COLOR", "")  # recorded, so restored at teardown
    _main(["schema", "--no-color", "-o", "json"])
    import os

    assert os.environ.get("NO_COLOR")

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    assert use_color(_Tty()) is False


def test_the_output_scan_leaves_forwarded_tokens_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After `--`, every token belongs to the command -- even `-o`."""
    from scout.cli.commands import stack

    calls: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        stack, "_compose", lambda args, **_k: calls.append(args) or _Done()
    )
    monkeypatch.setattr(stack, "_services", lambda _cwd: [])
    monkeypatch.chdir(REPO_ROOT)
    code, _ = _main(["-o", "json", "up", "--", "--no-color", "-o", "x"])
    assert code == ExitCode.SUCCESS
    assert calls == [["up", "-d", "--no-color", "-o", "x"]]


@pytest.mark.parametrize("spec", DECLARED, ids=lambda spec: spec.name)
def test_every_command_declares_input_validation(spec: Any) -> None:
    """The dispatcher answers any malformed argument with exit 3, for every
    command; LOCAL commands also refuse with it outside a checkout."""
    assert ErrorKind.INPUT_VALIDATION in spec.errors


@pytest.mark.parametrize(
    "spec",
    [spec for spec in DECLARED if spec.prerequisite is Prerequisite.LOCAL],
    ids=lambda spec: spec.name,
)
def test_every_local_command_declares_infrastructure(spec: Any) -> None:
    """Each touches disk, a subprocess, or the database, and the dispatcher
    reports any failure it did not anticipate -- a `CalledProcessError` from
    `git`, an `OSError` writing a secret -- as infrastructure (exit 2)."""
    assert ErrorKind.INFRASTRUCTURE in spec.errors


#: A kind a command's own body can raise, recognised by how it is raised.
_RAISES = {
    "require_repo(": ErrorKind.INPUT_VALIDATION,
    "input_error(": ErrorKind.INPUT_VALIDATION,
    "confirmation_required(": ErrorKind.CONFIRMATION_REQUIRED,
    "ErrorKind.CONFIRMATION_REQUIRED": ErrorKind.CONFIRMATION_REQUIRED,
    "conflict_error(": ErrorKind.CONFLICT,
    "infrastructure_error(": ErrorKind.INFRASTRUCTURE,
    "auth_error(": ErrorKind.AUTH,
    "tty_required(": ErrorKind.TTY_REQUIRED,
}


@pytest.mark.parametrize("spec", DECLARED, ids=lambda spec: spec.name)
def test_a_kind_the_command_raises_is_declared(spec: Any) -> None:
    source = inspect.getsource(spec.load())
    raised = {kind for marker, kind in _RAISES.items() if marker in source}
    assert raised - set(spec.errors) == set()


def test_the_error_table_lists_only_kinds_a_command_can_raise() -> None:
    """Publishing `auth` and `tty_required` told an agent to prepare for
    failures no command produces; the table is the union of what is declared."""
    from scout.cli.commands.schema import schema

    published = {entry["kind"] for entry in schema().data["errors"]}
    declared = {kind.value for spec in DECLARED for kind in spec.errors}
    assert published == declared


def test_task_states_are_the_task_state_enum() -> None:
    from scout.cli.declarations import _TASK_STATES
    from scout.cli.tasks import TaskState

    assert set(_TASK_STATES) == {state.value for state in TaskState}


# ── emitted payloads against their declared shape ──────────────────────────

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list, tuple),
}


def _conforms(data: dict[str, Any], fields: tuple[FieldSpec, ...], where: str) -> None:
    declared = {field.name: field for field in fields}
    undeclared = set(data) - set(declared)
    assert not undeclared, f"{where}: emitted but undeclared {sorted(undeclared)}"
    for name, value in data.items():
        spec = declared[name]
        if value is None:
            assert spec.nullable, f"{where}.{name}: null but not nullable"
            continue
        allowed = _TYPES[spec.type]
        ok = isinstance(value, allowed) and not (
            spec.type in ("integer", "number") and isinstance(value, bool)
        )
        assert ok, f"{where}.{name}: {type(value).__name__} is not {spec.type}"
        if spec.enum:
            assert value in spec.enum, f"{where}.{name}: {value!r} not in enum"
        if spec.type == "object" and spec.fields:
            _conforms(value, spec.fields, f"{where}.{name}")


def _declared(name: str) -> tuple[FieldSpec, ...]:
    return next(spec for spec in DECLARED if spec.name == name).output_fields


def test_check_emits_its_declared_shape() -> None:
    from scout.cli.commands.verify import check
    from scout.cli.result import CommandResult

    def _stage(config: Any = None) -> CommandResult:
        return CommandResult(data={"status": "pass"})

    result = check(stages=(("vault", _stage), ("secrets", _stage)))
    _conforms(result.data, _declared("check"), "check")


def test_compile_status_emits_its_declared_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout.cli.commands.compile import compile_status
    from scout.cli.tasks import write_run_marker

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": "1",
                        "title": "A",
                        "loc": "p.1",
                        "slug": "a",
                        "category": "concept",
                        "department": "ai_eng",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_run_marker(plan, pid=2**22)

    class _Cfg:
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    result = compile_status("plan.json", config=_Cfg())
    assert result.data["started_at"] is not None
    _conforms(result.data, _declared("compile-status"), "compile-status")


def test_a_background_call_on_a_running_batch_emits_its_declared_shape(
    tmp_path: Path,
) -> None:
    """It starts nothing and answers with the running batch's whole status."""
    import os

    from scout.cli.commands.compile import _start_background
    from scout.cli.tasks import write_run_marker

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": "1",
                        "title": "A",
                        "loc": "p.1",
                        "slug": "a",
                        "category": "concept",
                        "department": "ai_eng",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_run_marker(plan, pid=os.getpid())  # a live process: this one

    result = _start_background(plan, False, False)
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["state"] == "running"
    _conforms(result.data, _declared("compile-plan"), "compile-plan")


def test_the_background_launcher_state_is_declared() -> None:
    """`compile-plan --background` answers `state: "starting"` before the
    child has written its marker; the enum must say so."""
    from scout.cli.commands.compile import _start_background

    assert '"state": "starting"' in inspect.getsource(_start_background)
    state = next(f for f in _declared("compile-plan") if f.name == "state")
    assert "starting" in state.enum
