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
from scout.cli.errors import infrastructure_error
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
    dry_run: bool = False,
    skip_groundedness: bool = False,
    no_resume: bool = False,
    allow_uncertain: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Compile every article in an approved plan into the vault."""
    from scripts.compile_note import CompileNoteError
    from scripts.compile_plan import CompilePlanError
    from scripts.compile_plan import compile_plan as _compile_plan

    cfg: Config = config
    cfg.require_repo()
    try:
        pages = _compile_plan(
            Path(plan),
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
