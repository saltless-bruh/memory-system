"""The generated local MCP server: surface, annotations, and import purity."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

from scout.cli.mcp_policy import Exposure, policy_for
from scout.cli.registry import Effect
from scout.mcp.local_server import annotations_for, build_server

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tools() -> list[Any]:
    return asyncio.run(build_server().list_tools())


def test_the_tool_surface_matches_the_policy() -> None:
    names = {tool.name for tool in _tools()}
    assert names == {"verify", "plan_articles", "compile_plan", "compile_status"}


def test_building_the_server_touches_no_socket() -> None:
    """The suite runs with sockets disabled; building must stay that cheap.

    `snpmemory schema` already guarantees it answers with no database, gateway
    or credential. The MCP surface makes the same promise, and it only holds
    because implementations load at call time rather than at registration.
    """
    assert len(_tools()) == 4


def test_read_tools_are_annotated_read_only_and_write_tools_are_not() -> None:
    """A client decides whether to prompt from these hints."""
    by_name = {tool.name: tool for tool in _tools()}

    for name in ("verify", "plan_articles", "compile_status"):
        annotations = by_name[name].annotations
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False

    compile_annotations = by_name["compile_plan"].annotations
    assert compile_annotations.readOnlyHint is False
    assert compile_annotations.destructiveHint is True


def test_annotations_come_from_the_declared_effect() -> None:
    """Effect is the source of truth, so write authority cannot appear quietly."""
    from scout.cli.registry import CommandSpec

    read = CommandSpec(name="r", summary="s", target="x:y", effect=Effect.READ)
    write = CommandSpec(name="w", summary="s", target="x:y", effect=Effect.WRITE)
    destructive = CommandSpec(
        name="d", summary="s", target="x:y", effect=Effect.DESTRUCTIVE
    )

    assert annotations_for(read, title="t").readOnlyHint is True
    assert annotations_for(write, title="t").destructiveHint is True
    assert annotations_for(destructive, title="t").destructiveHint is True


def test_no_tool_exceeds_the_parameter_budget() -> None:
    """Bloated schemas degrade tool selection; guidance is roughly eight."""
    for tool in _tools():
        properties = tool.parameters.get("properties", {})
        assert len(properties) <= 8, f"{tool.name} has {len(properties)} parameters"


def test_every_exposed_tool_has_a_description() -> None:
    for tool in _tools():
        assert tool.description and len(tool.description) > 20


def test_hidden_commands_do_not_become_tools() -> None:
    policy = policy_for("schema")
    assert policy is not None and policy.exposure is Exposure.HIDDEN
    assert "schema" not in {tool.name for tool in _tools()}


def test_compile_plan_advertises_that_it_writes() -> None:
    by_name = {tool.name: tool for tool in _tools()}
    assert "WRITES" in by_name["compile_plan"].description.upper()


def test_importing_the_server_module_reads_no_environment() -> None:
    """Importing must not run dotenv or resolve a credential.

    Several scripts here call `load_dotenv()` at module scope. If one were
    imported to register a tool, thirty variables — including a provider key —
    would enter the environment before the agent chose anything.
    """
    probe = (
        "import os, sys;"
        "before = set(os.environ);"
        "import scout.mcp.local_server as m;"
        "m.build_server();"
        "added = set(os.environ) - before;"
        "sys.exit(1) if added else sys.exit(0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"import added env vars: {result.stdout}{result.stderr}"
