"""A local, stdio MCP server exposing this repository's own operations.

Why local and stdio, when `scout` is already an MCP server: `scout` runs in a
container with no read-write repository mount, and `docs/ARCHITECTURE_STATUS.md`
lists such a mount among its prohibited claims. Most operator commands need a
checkout on disk. Serving them from `scout` would restore the
topology v2 hardening removed and would make a retrieval token a page-writing
token.

**Authority model — read this before changing the transport.** This server has
exactly the authority of the user who launches it: no token, no scope check, no
network listener. That is what makes it safe *and* what makes it unsafe to
expose. It must not be served over HTTP without an OAuth 2.1 design with
audience validation; a server that accepts a token it was not issued is the
confused-deputy failure the MCP guidance exists to prevent.

The authenticated Scout server exposes the same read-only ``wiki_search`` and
``wiki_read`` contract. Direct address fetch remains an operator-only command.

**Why `compile_plan` is not an MCP task.** It looks like the obvious candidate —
a batch that runs for minutes, handed back as a handle — and `fastmcp` 3.3.1 does
implement Tasks. It was measured rather than assumed, and the cost is not one
decorator argument:

* every task path is gated on **pydocket** (`fastmcp[tasks]`), a distributed task
  system that pulls in `redis>=5`. Without it `get_task_capabilities()` returns
  `None`, so the server advertises no task capability and a client has nothing
  to negotiate against;
* `TaskConfig.validate_function` calls `require_docket` at **registration**, so
  declaring one without the extra does not degrade — it stops the server
  building at all;
* task-augmented functions must be `async`, and these tools are synchronous
  wrappers around a blocking command path.

What that would buy is a `taskId` scoped to this server process. What already
exists is a handle that is a path on disk, backed by a staging directory and a
`.run.json` carrying state, heartbeat, TTL and poll interval — which survives a
server restart, a reboot, and a client that has never heard of Tasks. Requiring
Redis to obtain a weaker record is the wrong trade, so the durable handle stays
and `compile-cancel` covers stopping a batch. See
`tests/test_local_mcp_server.py` for the tests that hold this to the facts it
rests on.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from scout.cli.declarations import DECLARED
from scout.cli.mcp_policy import DEFAULT_VERIFY_STAGE, VERIFY_TOOL, stages
from scout.cli.mcp_policy import standalone_tools as _standalone_tools
from scout.cli.mcp_result import run_tool
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

    **Where the working root is pinned, and why not here.** A client starts this
    server from its own directory, and every tool call resolves configuration
    and relative paths against the process cwd. `snpmemory mcp --root <dir>`
    therefore moves the process before serving, which pins all of those together
    at once — see `scout/cli/commands/mcp.py`. The matching refusal for a plan
    path that escapes that root lives in the commands (`scout/cli/tasks.py`
    `resolve_plan_path`), not in this module, so it protects the CLI as well as
    the tool boundary rather than only the caller who happens to arrive by MCP.
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
        return run_tool(spec, detail=detail)

    standalone = _standalone_tools()

    search_spec = standalone.get("wiki_search") or by_name["search"]

    @server.tool(
        name="wiki_search",
        description=(
            _describe(search_spec)
            + " Returns distinct pages with bounded snippets; pass prior content "
            "hashes in seen to receive stubs instead of repeated context."
        ),
        annotations=annotations_for(search_spec, title="Search wiki pages"),
    )
    def wiki_search(
        query: Annotated[str, "Natural-language query."],
        department: Annotated[str, "redteam | blueteam | ai_eng | infra"],
        k: Annotated[int, "Maximum distinct pages to return."] = 5,
        seen: Annotated[
            list[str] | None, "Content hashes already present in context."
        ] = None,
    ) -> list[dict[str, object]]:
        payload = run_tool(
            search_spec,
            query,
            dept=department,
            limit=k,
            seen=seen,
            detail=True,
        )
        return cast(list[dict[str, object]], payload["hits"])

    read_spec = standalone.get("wiki_read") or by_name["read"]

    @server.tool(
        name="wiki_read",
        description=(
            _describe(read_spec)
            + " Read tldr or outline first when context is tight, then request one "
            "section or the full canonical envelope."
        ),
        annotations=annotations_for(read_spec, title="Read wiki page"),
    )
    def wiki_read(
        path: Annotated[str, "Page title, slug, or vault-relative path."],
        department: Annotated[str, "redteam | blueteam | ai_eng | infra"],
        mode: Annotated[
            Literal["full", "tldr", "outline"], "Read granularity"
        ] = "full",
        section: Annotated[str | None, "One section heading to return."] = None,
    ) -> dict[str, Any]:
        payload = run_tool(
            read_spec,
            path,
            dept=department,
            mode=mode,
            section=section,
            detail=True,
        )
        return {
            key: value
            for key, value in payload.items()
            if key not in {"ok", "exit_code", "summary"}
        }

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
        out: Annotated[
            str | None, "Write the plan here instead of returning it."
        ] = None,
        detail: Annotated[bool, "Return every field instead of a summary."] = False,
    ) -> dict[str, Any]:
        return run_tool(
            plan_spec,
            path,
            dept=dept,
            category=category,
            max_depth=max_depth,
            out=out,
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
        background: Annotated[
            bool, "Return a handle immediately instead of blocking for minutes."
        ] = False,
        dry_run: Annotated[bool, "Prepare and judge, but publish nothing."] = False,
        skip_groundedness: Annotated[
            bool, "Write unverified prose. Not advised."
        ] = False,
        allow_uncertain: Annotated[bool, "Proceed past pre-flight warnings."] = False,
        detail: Annotated[bool, "Return every field instead of a summary."] = False,
    ) -> dict[str, Any]:
        return run_tool(
            compile_spec,
            plan,
            confirm=confirm,
            background=background,
            dry_run=dry_run,
            skip_groundedness=skip_groundedness,
            allow_uncertain=allow_uncertain,
            detail=detail,
        )

    status_spec = standalone.get("compile_status") or by_name["compile-status"]

    @server.tool(
        name="compile_status",
        description=(
            _describe(status_spec)
            + " Poll this after compile_plan(background=true). States: not_started, "
            "running, stalled, staged, complete."
        ),
        annotations=annotations_for(status_spec, title="Compile status"),
    )
    def compile_status(
        handle: Annotated[str, "The handle compile_plan returned (its plan path)."],
        detail: Annotated[bool, "Return every field instead of a summary."] = False,
    ) -> dict[str, Any]:
        return run_tool(status_spec, handle, detail=detail)

    return server


def run() -> None:  # pragma: no cover - transport wiring
    """Serve over stdio. Never bind a socket here; see the module docstring."""
    build_server().run(transport="stdio")
