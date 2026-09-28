"""The compilation family: `plan-articles` and `compile-plan`.

`plan-articles` is deliberately in the READ class and needs no model, no
database, and no running stack: it is a pure function of the source bytes, and
that is what makes its output stable enough to commit and edit by hand.

`compile-plan` is the WRITE half. It refuses to write anything until every
article in the plan has minted, been generated, and been judged. It is
**draft-only** on a vault whose `index.md` is authored (owner ruling,
2026-09-26): the batch stages every page, then refuses to publish and says
where the drafts are.

Every command here resolves the checkout **and the vault** from the pinned
configuration and hands both down. The scripts default to the tree they ship
in, which is a different tree whenever the package is installed anywhere but
the vault checkout -- and then the plan was bounded to one tree while its pages
went to another.

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


def _vault_dir(cfg: Config) -> Path:
    """The served vault under the pinned checkout, honouring `WIKI_DIR`.

    The same resolution `wiki` and `verify-*` use, so every command agrees on
    which pages "the vault" means.
    """
    from scout.cli.commands.wiki import _wiki_dir

    return _wiki_dir(cfg)


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
    repo = cfg.require_repo().resolve()
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

    wiki_dir = _vault_dir(cfg)

    if background:
        # A batch runs for minutes. A caller that blocks — an MCP client
        # especially — times out and retries, re-spending the quota the
        # staging checkpoint exists to protect. Hand back a handle instead.
        #
        # Every flag that changes what the run does travels with it. The
        # confirmation guard above waives `--confirm` for a dry run, so a child
        # that was not told it is one would publish with neither.
        return _start_background(
            plan_path,
            repo=repo,
            wiki_dir=wiki_dir,
            dry_run=dry_run,
            no_resume=no_resume,
            skip_groundedness=skip_groundedness,
            allow_uncertain=allow_uncertain,
        )

    try:
        pages = _compile_plan(
            plan_path,
            skip_groundedness=skip_groundedness,
            dry_run=dry_run,
            resume=not no_resume,
            allow_uncertain=allow_uncertain,
            repo_root=repo,
            wiki_dir=wiki_dir,
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
    plan_path: Path,
    *,
    repo: Path,
    wiki_dir: Path,
    dry_run: bool,
    no_resume: bool,
    skip_groundedness: bool,
    allow_uncertain: bool,
) -> CommandResult:
    """Launch the batch detached and return immediately with its handle.

    `plan_path` arrives already resolved: the child process inherits no working
    directory guarantee, so a relative path here would give it a different
    staging directory from the one this process reports on. The checkout and
    vault are passed explicitly for the same reason.
    """
    import subprocess
    import sys

    from scout.cli.tasks import TaskState, status_for

    # Read with the vault, as `compile-status` reads it. Without it a batch that
    # already published -- its staging removed -- reads `not_started`, and was
    # relaunched only to spend its pre-flight and then fail on "Destination
    # page already exists".
    existing = status_for(plan_path, wiki_dir=wiki_dir, root=repo)
    if existing.state is TaskState.RUNNING:
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"handle": existing.handle, **existing.to_dict()},
            summary=f"already running (pid {existing.pid}) — nothing started",
        )
    if existing.state is TaskState.COMPLETE:
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={"handle": existing.handle, **existing.to_dict()},
            summary="already complete — every planned page exists; nothing started",
        )

    argv = [
        sys.executable,
        "-m",
        "scripts.compile_plan",
        "--plan",
        str(plan_path),
        "--repo-root",
        str(repo),
        "--wiki-dir",
        str(wiki_dir),
    ]
    for flag, wanted in (
        ("--dry-run", dry_run),
        ("--no-resume", no_resume),
        ("--skip-groundedness", skip_groundedness),
        ("--allow-uncertain", allow_uncertain),
    ):
        if wanted:
            argv.append(flag)

    log_path = plan_path.with_suffix(".log")
    with log_path.open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - argv is built here, not user text
            argv,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=repo,
        )

    status = status_for(plan_path, wiki_dir=wiki_dir, root=repo)
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        data={
            "handle": plan_path.as_posix(),
            "pid": process.pid,
            "log": log_path.as_posix(),
            "total": status.total,
            "state": "starting",
            "dry_run": dry_run,
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
    from scout.cli.errors import input_error
    from scout.cli.tasks import TaskState, status_for

    cfg: Config = config
    repo = cfg.require_repo()

    # The same anchoring `compile_plan` used, so the handle it returned resolves
    # to the same batch here — including when the two calls happen in different
    # directories, which for an MCP client is the normal case.
    status = status_for(Path(handle), wiki_dir=_vault_dir(cfg), root=repo)

    # A handle that resolves to no readable plan is a bad argument, not a batch
    # in a bad state, and it is the one answer this command used to get wrong:
    # it returned exit 0 with `done: 0, pending: []`, which a caller reading the
    # exit code — a script, or an agent — cannot tell from an idle batch.
    # `compile-cancel` has always raised here; this is the same refusal, and
    # `docs/CLI_SPEC.md` §1 already assigns an unresolvable identifier to
    # input validation.
    if status.state is TaskState.UNKNOWN:
        raise input_error(
            status.detail or f"no plan at {handle}",
            hint="pass the handle compile-plan returned",
            handle=status.handle,
        )

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
    from scout.cli.errors import input_error
    from scout.cli.tasks import (
        TaskState,
        process_is_alive,
        request_cancel,
        resolve_plan_path,
        status_for,
    )

    cfg: Config = config
    repo = cfg.require_repo()
    plan_path = resolve_plan_path(Path(handle), repo)
    if not plan_path.is_file():
        raise input_error(
            f"no plan at {plan_path}",
            hint="pass the handle compile-plan returned",
            handle=plan_path.as_posix(),
        )

    status = status_for(plan_path, wiki_dir=_vault_dir(cfg), root=repo)

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
    # A live process with an expired heartbeat is `stalled`, and it is the batch
    # an operator most needs to stop: if it wakes it goes on toward publish.
    # Refusing here with "no process is working on this batch" contradicted
    # the status detail and wrote nothing for the process to find. The request
    # is written; what it can and cannot do is said plainly.
    hung = (
        status.state is TaskState.STALLED
        and status.pid is not None
        and process_is_alive(status.pid)
    )
    if hung:
        request_cancel(plan_path)
        return CommandResult(
            exit_code=ExitCode.SUCCESS,
            data={
                "handle": status.handle,
                "status": "cancelling",
                "state": status.state.value,
                "pid": status.pid,
            },
            summary=(
                f"cancellation requested — process {status.pid} is alive but "
                "has finished no article within its heartbeat ttl, so it may be "
                "hung. It will stop at its next article boundary if it resumes; "
                f"if it never does, stop it yourself (kill {status.pid}) and "
                "re-run to resume from staging"
            ),
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
