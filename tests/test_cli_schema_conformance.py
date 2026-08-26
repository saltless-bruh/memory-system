"""`snpmemory schema` must validate against The CLI Spec's own schema.

`docs/CLI_SPEC.md` states that this tool follows https://clispec.dev/. That is a
claim about a machine-readable document, so it is checked against the real
schema rather than by reading the prose.

The schema is **vendored** at `tests/fixtures/clispec-v0.3.json` rather than
fetched. `pytest-socket` blocks network access in this suite by design, and a
conformance test that reaches the internet fails for reasons unrelated to the
code while pinning no particular version of the contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SPEC_SCHEMA = Path(__file__).parent / "fixtures" / "clispec-v0.3.json"


@pytest.fixture(scope="module")
def spec_schema() -> dict:
    return json.loads(SPEC_SCHEMA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def document() -> dict:
    from scout.cli.commands.schema import schema

    result = schema()
    return result.data


def test_schema_document_validates_against_the_spec(
    document: dict, spec_schema: dict
) -> None:
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(spec_schema).iter_errors(document),
        key=lambda e: list(e.absolute_path),
    )
    assert not errors, "\n".join(
        f"{list(e.absolute_path) or '<root>'}: {e.message}" for e in errors[:15]
    )


def test_required_top_level_fields_are_present(document: dict) -> None:
    for field in ("clispec", "name", "version", "commands", "errors"):
        assert field in document, field


def test_the_text_default_is_declared_not_merely_divergent(document: dict) -> None:
    """The spec allows a human-readable default only if it is declared.

    "Structured output SHOULD be the default when piped; a tool that keeps a
    human-readable default MAY do so if it declares that default in the
    top-level `output` field." `auto` resolves to text here on purpose — CI
    steps and agent workflows read that text — so the declaration is what makes
    the divergence conformant instead of silent.
    """
    assert document["output"]["piped"] == "text"


def test_every_command_declares_its_arguments(document: dict) -> None:
    """The whole point: an agent must not have to parse `--help`.

    A command that takes flags and declares none teaches an agent that it takes
    none, which is worse than saying nothing.
    """
    by_name = {command["name"]: command for command in document["commands"]}
    compile_plan = by_name["compile-plan"]
    declared = {arg["name"] for arg in compile_plan.get("args", ())}
    assert {"--confirm", "--background", "--dry-run"} <= declared, declared


def test_a_command_that_requires_confirmation_names_the_flag(document: dict) -> None:
    by_name = {command["name"]: command for command in document["commands"]}
    assert by_name["compile-plan"]["confirmation_bypass_arg"] == "--confirm"


def test_every_argument_referenced_elsewhere_is_declared(document: dict) -> None:
    """The spec's referential-integrity rule, checked for real.

    "Every argument referenced by `pagination`, `fields_arg`,
    `confirmation_bypass_arg` or `idempotency_key_arg` is declared in that
    command's `args` or in `global_args`."
    """
    global_args = {arg["name"] for arg in document.get("global_args", ())}
    for command in document["commands"]:
        local = {arg["name"] for arg in command.get("args", ())} | global_args
        referenced = [
            command.get("confirmation_bypass_arg"),
            command.get("fields_arg"),
            command.get("idempotency_key_arg"),
        ]
        pagination = command.get("pagination") or {}
        referenced += [pagination.get("cursor_arg"), pagination.get("offset_arg")]
        referenced += [pagination.get("limit_arg")]
        for name in filter(None, referenced):
            assert name in local, f"{command['name']}: {name} is not declared"


def test_unbounded_commands_declare_pagination(document: dict) -> None:
    for command in document["commands"]:
        if command.get("cardinality") == "unbounded":
            assert command.get("pagination"), command["name"]


def test_schema_narrows_to_one_command() -> None:
    from scout.cli.commands.schema import schema

    document = schema("verify-vault").data
    assert [entry["name"] for entry in document["commands"]] == ["verify-vault"]
    # The tool-level tables stay: a consumer reading one command still has to
    # resolve the error kinds and outcomes that command references.
    assert document["errors"]
    assert document["clispec"] == "0.3"


def test_narrowing_to_an_unknown_command_is_a_caller_mistake() -> None:
    from scout.cli.commands.schema import schema
    from scout.cli.errors import CliError
    from scout.cli.result import ExitCode

    with pytest.raises(CliError) as caught:
        schema("no-such-command")
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_a_narrowed_document_still_conforms(spec_schema: dict) -> None:
    from jsonschema import Draft202012Validator

    from scout.cli.commands.schema import schema

    errors = list(Draft202012Validator(spec_schema).iter_errors(schema("check").data))
    assert not errors, [e.message for e in errors[:5]]


# ── the spec document and the registry must not drift ─────────────────────


def _spec_command_names() -> set[str]:
    """Command names from the tables in `docs/CLI_SPEC.md`.

    The spec lists commands that are not implemented yet, so the invariant is
    one-directional: everything the registry offers must be in the spec. A
    command that exists without being specified is undocumented surface, which
    is how a CLI grows flags nobody agreed to.
    """
    import re

    text = (REPO_ROOT / "docs" / "CLI_SPEC.md").read_text(encoding="utf-8")
    names: set[str] = set()
    for row in re.findall(r"^\|\s*`snpmemory ([^`]+)`", text, re.MULTILINE):
        # "up │ down │ status │ logs [service]" declares four commands.
        head = row.split("<")[0].split("--")[0].split("[")[0]
        for candidate in head.split("│"):
            token = candidate.strip()
            if token:
                names.add(token.split()[0])
    return names


def test_every_implemented_command_appears_in_the_spec(document: dict) -> None:
    implemented = {entry["name"] for entry in document["commands"]}
    missing = implemented - _spec_command_names()
    assert not missing, f"implemented but unspecified: {sorted(missing)}"
