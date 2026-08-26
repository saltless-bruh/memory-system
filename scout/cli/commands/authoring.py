"""The authoring family: `mint`, `compile`, and `propose`.

Everything here writes, so everything here refuses first. A mutation without
`--confirm` returns exit `5` **and** an envelope naming what would change and
the exact command that would do it — a refusal an agent can act on, rather than
a prompt it cannot answer.

Each command calls the module that already does the work (`scripts/mint.py`,
`scripts/compile_note.py`, `scripts/propose_page.py`) rather than reproducing
it. A wrapper that reimplements its script is a second copy free to drift, which
is the failure this layer exists to prevent, not cause.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import (
    CliError,
    conflict_error,
    infrastructure_error,
    input_error,
)
from scout.cli.result import CommandResult, ErrorKind, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def _refusal(action: str, rerun: str, **details: Any) -> CliError:
    """Refuse a mutation, saying what it would do and how to authorise it.

    Deliberately not an interactive prompt: the same refusal has to be readable
    by a person, a CI step, and an agent, and only one of those can answer a
    question.
    """
    return CliError(
        ErrorKind.CONFIRMATION_REQUIRED,
        f"{action} — not performed",
        hint=f"re-run with --confirm: {rerun}",
        details=details,
    )


def mint(
    *,
    path: str,
    hint: str,
    dept: str,
    loc: str,
    config: Injected = None,
) -> CommandResult:
    """Find whether a hint provably retrieves its own file (R-6.3).

    Writes nothing: minting answers a question, and the caller decides what to
    do with the address. Exit `1` when no candidate clears the gate — a finding,
    not a failure, and never a hint invented to make the answer come out.
    """
    import asyncio

    from scout.cli.commands.rag import _scope_for
    from scripts.mint import MintStatus, mint_address

    cfg: Config = config
    cfg.require_repo()
    _scope_for(dept)  # canonical department, and `all` refused, before any I/O

    backend = _backend(cfg)

    async def run() -> Any:
        try:
            return await mint_address(backend, path, [hint], department=dept, loc=loc)
        finally:
            await _close(backend)

    try:
        result = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - one envelope for any transport fault
        raise infrastructure_error(
            "the RAG backend could not be reached",
            hint="minting needs postgres AND the embedding route; bring the stack up",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    tried = [
        {"hint": candidate, "outcome": outcome.value}
        for candidate, outcome in result.tried
    ]
    if result.status is MintStatus.MINTED and result.address is not None:
        return CommandResult(
            data={
                "status": "minted",
                "path": result.path,
                "department": result.department,
                "hint": result.address.hint,
                "loc": result.address.loc,
                "tried": tried,
                "available_locs": list(result.available_locs),
            },
            summary=f"MINTED  {result.path}  loc={result.address.loc}",
        )

    return CommandResult(
        exit_code=ExitCode.SEMANTIC_FAILURE,
        data={
            "status": "no_hint_works",
            "path": result.path,
            "department": result.department,
            "tried": tried,
            "available_locs": list(result.available_locs),
        },
        summary=f"no candidate hint retrieves {result.path}",
        messages=(
            "FAIL = the file returned nothing (is it indexed?) · "
            "DRIFT = another file outranked it, or the phrase is not grounded · "
            "LOC_MISMATCH = the hint works but --loc names a locator the file lacks",
        ),
    )


def compile(  # noqa: A001 - the specified command name
    *,
    path: str,
    title: str,
    category: str,
    dept: str,
    loc: str,
    confirm: bool = False,
    dry_run: bool = False,
    skip_groundedness: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Compile one source section into a grounded wiki page."""
    from scout.cli.commands.rag import _scope_for
    from scripts.compile_note import CompileNoteError, prepare_page, publish_page

    cfg: Config = config
    cfg.require_repo()
    _scope_for(dept)

    try:
        prepared = prepare_page(
            path,
            title,
            category,
            department=dept,
            loc=loc,
            skip_groundedness=skip_groundedness,
        )
    except CompileNoteError as exc:
        # The page exists, or the branch is protected: a state conflict, not a
        # malformed request, and never something to overwrite.
        message = str(exc)
        if "exists" in message.lower() or "branch" in message.lower():
            raise conflict_error(
                message, hint="pick another title, or work on a feature branch"
            ) from exc
        raise input_error(message) from exc
    except Exception as exc:  # noqa: BLE001
        raise infrastructure_error(
            "the page could not be prepared",
            hint="compilation needs postgres, the embedding route, and the model route",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    target = prepared.path.relative_to(cfg.require_repo()).as_posix()
    proposed = {
        "path": target,
        "title": title,
        "category": category,
        "department": dept,
        "sources": prepared.frontmatter.get("sources", []),
    }

    if dry_run:
        return CommandResult(
            data={"status": "dry_run", **proposed},
            summary=f"[dry-run] would write {target}",
        )
    if not confirm:
        raise _refusal(
            f"would write {target}",
            f"snpmemory compile --path {path} --title {title!r} "
            f"--category {category} --dept {dept} --loc {loc} --confirm",
            proposed=proposed,
        )

    written = publish_page(prepared)
    return CommandResult(
        data={"status": "written", **proposed},
        summary=f"wrote {written.relative_to(cfg.require_repo()).as_posix()}",
    )


def propose(
    *,
    page: str,
    title: str = "",
    base: str = "main",
    remote: str = "origin",
    push: bool = False,
    confirm: bool = False,
    dry_run: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Move a compiled page onto its own branch for review (R-6.4 / R-7.3).

    The page is never committed to the branch you are standing on: a new
    `wiki/<slug>-<timestamp>` branch is created first, and only the named page
    and its generated companions are committed to it.
    """
    from scripts import propose_page

    cfg: Config = config
    cfg.require_repo()

    try:
        normalized = propose_page._normalize_page(page)
    except ValueError as exc:
        raise input_error(str(exc), hint="pass a path under wiki/") from exc

    staged = propose_page._staged_paths()
    if staged:
        # Committing here would sweep up work the caller never named.
        raise conflict_error(
            "refusing to propose while staged paths exist",
            hint="unstage them (`git restore --staged .`) and re-run",
            staged=sorted(staged),
        )

    allowed = (normalized, *propose_page.GENERATED_COMPANIONS)
    changes = propose_page.wiki_changes(allowed)
    if normalized not in changes:
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"status": "no_change", "page": normalized},
            summary=f"{normalized} has no working-tree change to propose",
        )

    selected = [candidate for candidate in allowed if candidate in changes]
    proposed = {
        "page": normalized,
        "paths": selected,
        "base_branch": propose_page.current_branch(),
        "target": base,
    }

    if dry_run:
        return CommandResult(
            data={"status": "dry_run", **proposed},
            summary=f"[dry-run] would branch from {proposed['base_branch']} and commit {len(selected)} path(s)",
        )
    if not confirm:
        raise _refusal(
            f"would branch from {proposed['base_branch']} and commit {len(selected)} path(s)",
            f"snpmemory propose --page {normalized} --confirm",
            proposed=proposed,
        )

    argv = ["--page", normalized, "--base", base, "--remote", remote]
    if title:
        argv += ["--title", title]
    if push:
        argv.append("--push")
    code = propose_page.main(argv)
    if code != 0:
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={"status": "refused", **proposed},
            summary="propose refused; the working tree and branch are unchanged",
        )
    return CommandResult(
        data={"status": "proposed", **proposed},
        summary=f"proposed {normalized} for review",
    )


def _backend(cfg: Config) -> Any:
    """The RLS backend, wired entirely from resolved configuration."""
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.chunker import LiteLLMBatchEmbedder
    from scout.config import postgres_settings

    try:
        settings = postgres_settings("query", env=cfg.values)
        embedder = LiteLLMBatchEmbedder(
            base_url=cfg.get("LITELLM_BASE_URL"),
            api_key=cfg.require("LITELLM_MASTER_KEY"),
        )
    except CliError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise infrastructure_error(
            "the RAG backend could not be configured",
            hint="check POSTGRES_* and LITELLM_* settings",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc
    return PgVectorRlsBackend(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
        embedder=embedder,
    )


async def _close(backend: Any) -> None:
    close = getattr(backend, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
