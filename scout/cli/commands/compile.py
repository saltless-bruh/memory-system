"""The compilation family: `plan-articles` and `compile-plan`.

`plan-articles` is deliberately in the READ class and needs no model, no
database, and no running stack: it is a pure function of the source bytes, and
that is what makes its output stable enough to commit and edit by hand.

`compile-plan` is the WRITE half. It refuses to write anything until every
article in the plan has minted, been generated, and been judged.

Every heavy import happens inside a function, so importing this module reads no
environment and resolves no credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import confirmation_required, infrastructure_error
from scout.cli.result import CommandResult, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def plan_articles(
    path: str,
    *,
    dept: str,
    category: str = "concept",
    max_depth: int = 2,
    out: str | None = None,
    config: Injected = None,
) -> CommandResult:
    """Propose a multi-article decomposition from a source's own headings."""
    from scout.parsers import ParserError, parse_file
    from scripts.plan_articles import (
        PlanArticlesError,
        propose_articles,
        render_plan,
    )

    cfg: Config = config
    cfg.require_repo()
    repo = Path.cwd()
    source = (repo / path).resolve(strict=False)
    try:
        source.relative_to((repo / "raw").resolve(strict=False))
    except ValueError as exc:
        raise infrastructure_error(
            "source must resolve beneath raw/", hint=path, retryable=False
        ) from exc

    try:
        document = parse_file(source, repo)
        articles = propose_articles(
            document, department=dept, category=category, max_depth=max_depth
        )
    except (OSError, ParserError, PlanArticlesError) as exc:
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"source": path, "articles": [], "reason": str(exc)},
            summary=f"no plan proposed — {exc}",
        )

    rendered = render_plan(path, articles)
    written: str | None = None
    if out:
        destination = Path(out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
        written = destination.as_posix()

    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={
            "source": path,
            "written_to": written,
            "articles": [
                {
                    "section": a.section,
                    "title": a.title,
                    "loc": a.loc,
                    "slug": a.slug,
                    "category": a.category,
                    "department": a.department,
                }
                for a in articles
            ],
        },
        summary=(
            f"{len(articles)} article(s) proposed from {path}"
            + (f" → {written}" if written else "")
        ),
        messages=tuple(f"{a.section:>6}  {a.loc:>6}  {a.title}" for a in articles),
    )


def compile_plan(
    plan: str,
    *,
    confirm: bool = False,
    background: bool = False,
    dry_run: bool = False,
    skip_groundedness: bool = False,
    no_resume: bool = False,
    allow_uncertain: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Compile every article in an approved plan into the vault."""
    from scout.cli.tasks import resolve_plan_path
    from scripts.compile_note import CompileNoteError
    from scripts.compile_plan import CompilePlanError
    from scripts.compile_plan import compile_plan as _compile_plan

    cfg: Config = config
    repo = cfg.require_repo()
    # Resolved before anything is spent or written. The staging directory is
    # derived from this path, so a plan path outside the checkout would take the
    # staging directory — and everything the batch writes — out with it.
    plan_path = resolve_plan_path(Path(plan), repo)

    if not confirm and not dry_run:
        # Current guidance is that a mutating tool is approved when it is
        # called, not once at install time. `destructiveHint` asks a client to
        # prompt; this makes the refusal real even for clients that do not.
        raise confirmation_required(
            f"compile-plan writes pages to the vault from {plan}", flag="--confirm"
        )

    if background:
        # A batch runs for minutes. A caller that blocks — an MCP client
        # especially — times out and retries, re-spending the quota the
        # staging checkpoint exists to protect. Hand back a handle instead.
        return _start_background(plan_path, skip_groundedness, allow_uncertain)

    try:
        pages = _compile_plan(
            plan_path,
            skip_groundedness=skip_groundedness,
            dry_run=dry_run,
            resume=not no_resume,
            allow_uncertain=allow_uncertain,
        )
    except (CompilePlanError, CompileNoteError) as exc:
        # The batch declined to write. That is a finding about the plan or the
        # vault, not an infrastructure failure, so healing stays permitted.
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"plan": plan, "published": [], "reason": str(exc)},
            summary=f"batch not published — {exc}",
        )

    published = [p.as_posix() for p in pages]
    verb = "would publish" if dry_run else "published"
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={"plan": plan, "dry_run": dry_run, "published": published},
        summary=f"{verb} {len(published)} page(s) from {plan}",
        messages=tuple(published),
    )


def _start_background(
    plan_path: Path, skip_groundedness: bool, allow_uncertain: bool
) -> CommandResult:
    """Launch the batch detached and return immediately with its handle.

    `plan_path` arrives already resolved: the child process inherits no working
    directory guarantee, so a relative path here would give it a different
    staging directory from the one this process reports on.
    """
    import subprocess
    import sys

    from scout.cli.tasks import TaskState, status_for

    existing = status_for(plan_path)
    if existing.state is TaskState.RUNNING:
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"handle": existing.handle, **existing.to_dict()},
            summary=f"already running (pid {existing.pid}) — nothing started",
        )

    argv = [
        sys.executable,
        "-m",
        "scripts.compile_plan",
        "--plan",
        str(plan_path),
    ]
    if skip_groundedness:
        argv.append("--skip-groundedness")
    if allow_uncertain:
        argv.append("--allow-uncertain")

    log_path = plan_path.with_suffix(".log")
    with log_path.open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - argv is built here, not user text
            argv,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    status = status_for(plan_path)
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={
            "handle": plan_path.as_posix(),
            "pid": process.pid,
            "log": log_path.as_posix(),
            "total": status.total,
            "state": "starting",
        },
        summary=(
            f"started {status.total} article(s) in the background — "
            f"poll compile-status with handle {plan_path.as_posix()}"
        ),
    )


def compile_status(
    handle: str,
    *,
    config: Injected = None,
) -> CommandResult:
    """Report a batch's progress, computed from the plan and its staging."""
    from scout import vault
    from scout.cli.tasks import TaskState, status_for

    cfg: Config = config
    repo = cfg.require_repo()

    # The same anchoring `compile_plan` used, so the handle it returned resolves
    # to the same batch here — including when the two calls happen in different
    # directories, which for an MCP client is the normal case.
    status = status_for(Path(handle), wiki_dir=vault.WIKI_DIR, root=repo)
    # Exit 0 means the batch is fine: working, staged, or finished. Anything a
    # human has to act on is exit 1 — a finding, not a malfunction. `failed` and
    # `cancelled` belong here for the same reason `stalled` always did: a caller
    # chaining `compile-status && publish` must not proceed on a batch that
    # stopped, and reporting one as success is the dishonesty this state machine
    # exists to remove.
    needs_attention = status.state in {
        TaskState.STALLED,
        TaskState.NOT_STARTED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
    return CommandResult(
        exit_code=(
            ExitCode.SEMANTIC_FAILURE
            if needs_attention and status.total
            else ExitCode.SUCCESS
        ),
        data=status.to_dict(),
        summary=f"{status.state.value}: {status.done}/{status.total} — {status.detail}",
    )


def compile_cancel(
    handle: str,
    *,
    config: Injected = None,
) -> CommandResult:
    """Ask a running batch to stop at its next article boundary."""
    from scout import vault
    from scout.cli.errors import input_error
    from scout.cli.tasks import TaskState, request_cancel, resolve_plan_path, status_for

    cfg: Config = config
    repo = cfg.require_repo()
    plan_path = resolve_plan_path(Path(handle), repo)
    if not plan_path.is_file():
        raise input_error(
            f"no plan at {plan_path}",
            hint="pass the handle compile-plan returned",
            handle=plan_path.as_posix(),
        )

    status = status_for(plan_path, wiki_dir=vault.WIKI_DIR, root=repo)

    # Cancelling something that already stopped is a no-op that says so. Making
    # it an error would push a caller into treating a finished batch as a fault.
    if status.is_terminal:
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "handle": status.handle,
                "status": "no_change",
                "state": status.state.value,
            },
            summary=f"already {status.state.value} — nothing to cancel",
        )
    if status.state is not TaskState.RUNNING:
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "handle": status.handle,
                "status": "not_running",
                "state": status.state.value,
            },
            summary=(
                f"{status.state.value}: no process is working on this batch, so "
                "there is nothing to stop — re-run to resume, or delete the "
                "staging directory to discard it"
            ),
        )

    request_cancel(plan_path)
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={
            "handle": status.handle,
            "status": "cancelling",
            "state": status.state.value,
            "pid": status.pid,
        },
        # Cancellation is cooperative: the batch reads the request between
        # articles. Saying it has stopped would be the lie the whole status
        # contract exists to avoid.
        summary=(
            f"cancellation requested — the batch will stop after the current "
            f"article; poll compile-status until it reports "
            f"{TaskState.CANCELLED.value}"
        ),
    )
