"""A local, stdio MCP server exposing this repository's own operations.

Why local and stdio, when `scout` is already an MCP server: `scout` runs in a
container with no read-write repository mount, and `docs/ARCHITECTURE_STATUS.md`
lists such a mount among its prohibited claims. Seven of the eight declared
commands need a checkout on disk. Serving them from `scout` would restore the
topology v2 hardening removed and would make a retrieval token a page-writing
token.

**Authority model — read this before changing the transport.** This server has
exactly the authority of the user who launches it: no token, no scope check, no
network listener. That is what makes it safe *and* what makes it unsafe to
expose. It must not be served over HTTP without an OAuth 2.1 design with
audience validation; a server that accepts a token it was not issued is the
confused-deputy failure the MCP guidance exists to prevent.

`scout/mcp_server.py` is untouched: `rag_fetch` remains the only door into RAG.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from scout.cli.declarations import DECLARED
from scout.cli.invoke import invoke
from scout.cli.mcp_policy import DEFAULT_VERIFY_STAGE, VERIFY_TOOL, stages
from scout.cli.mcp_policy import standalone_tools as _standalone_tools
from scout.cli.mcp_result import to_tool_result
from scout.cli.registry import CommandSpec, Effect

SERVER_NAME = "snpmemory"


def annotations_for(spec: CommandSpec, *, title: str) -> ToolAnnotations:
    """MCP hints derived from the declaration, never from the tool author.

    A client uses these to decide whether to prompt before running something.
    Taking them from `Effect` means a command cannot quietly acquire write
    authority without that showing up in the registry, in review.
    """
    read_only = spec.effect is Effect.READ
    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=not read_only,
        idempotentHint=read_only,
        openWorldHint=False,
    )


def _describe(spec: CommandSpec) -> str:
    return spec.summary


def build_server(mcp: FastMCP | None = None) -> FastMCP:
    """Build the local server. Touches no database, gateway, or credential.

    Building and listing tools must stay as cheap and side-effect-free as
    `snpmemory schema`, which is why implementations are loaded by `invoke` at
    call time rather than imported here.
    """
    server = mcp or FastMCP(name=SERVER_NAME)
    by_name = {spec.name: spec for spec in DECLARED}
    stage_specs = stages()
    verify_stages = sorted(stage_specs)

    check_spec = stage_specs[DEFAULT_VERIFY_STAGE]

    @server.tool(
        name=VERIFY_TOOL,
        description=(
            "Verify the knowledge vault. Stages: "
            + ", ".join(verify_stages)
            + ". Returns ok=false with findings when the vault has problems; "
            "raises only when a check could not run."
        ),
        annotations=annotations_for(check_spec, title="Verify vault"),
    )
    def verify(
        stage: Annotated[
            Literal["all", "addresses", "groundedness", "secrets", "vault"],
            "Which verification to run.",
        ] = "all",
        detail: Annotated[bool, "Return every field instead of a summary."] = False,
    ) -> dict[str, Any]:
        spec = stage_specs[stage]
        return to_tool_result(invoke(spec), detail=detail)

    standalone = _standalone_tools()

    plan_spec = standalone.get("plan_articles") or by_name["plan-articles"]

    @server.tool(
        name="plan_articles",
        description=(
            _describe(plan_spec)
            + " Deterministic: no model call, and the same source yields the same "
            "plan every time. Write it with `out`, edit it by hand, then compile it."
        ),
        annotations=annotations_for(plan_spec, title="Plan articles"),
    )
    def plan_articles(
        path: Annotated[str, "Source path beneath raw/."],
        dept: Annotated[str, "redteam | blueteam | ai_eng | infra"],
        category: Annotated[str, "concept | technique | entity | playbook"] = "concept",
        max_depth: Annotated[int, "Deepest heading level to propose."] = 2,
        out: Annotated[str | None, "Write the plan here instead of returning it."] = None,
        detail: Annotated[bool, "Return every field instead of a summary."] = False,
    ) -> dict[str, Any]:
        return to_tool_result(
            invoke(
                plan_spec,
                path,
                dept=dept,
                category=category,
                max_depth=max_depth,
                out=out,
            ),
            detail=detail,
        )

    compile_spec = standalone.get("compile_plan") or by_name["compile-plan"]

    @server.tool(
        name="compile_plan",
        description=(
            _describe(compile_spec)
            + " WRITES PAGES. Requires confirm=true. Use dry_run=true first to see "
            "what would be written."
        ),
        annotations=annotations_for(compile_spec, title="Compile plan (writes pages)"),
    )
    def compile_plan(
        plan: Annotated[str, "Path to an approved article plan."],
        confirm: Annotated[bool, "Must be true to write anything."] = False,
        dry_run: Annotated[bool, "Prepare and judge, but publish nothing."] = False,
        skip_groundedness: Annotated[bool, "Write unverified prose. Not advised."] = False,
        allow_uncertain: Annotated[bool, "Proceed past pre-flight warnings."] = False,
        detail: Annotated[bool, "Return every field instead of a summary."] = False,
    ) -> dict[str, Any]:
        return to_tool_result(
            invoke(
                compile_spec,
                plan,
                confirm=confirm,
                dry_run=dry_run,
                skip_groundedness=skip_groundedness,
                allow_uncertain=allow_uncertain,
            ),
            detail=detail,
        )

    return server


def run() -> None:  # pragma: no cover - transport wiring
    """Serve over stdio. Never bind a socket here; see the module docstring."""
    build_server().run(transport="stdio")
