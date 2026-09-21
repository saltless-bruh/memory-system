"""The verification family: `verify-vault`, `verify-addresses`,
`verify-groundedness`, `verify-secrets`, and the `check` aggregate.

These are the first real consumers of `Config`, and they exercise the whole
contract: each one distinguishes a **finding** (the check ran and the vault has
a problem — exit 1) from a **failure** (the check could not run — exit 2). A
finding can authorize a caller's follow-up; a failure never authorizes mutation.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
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


def _pages_and_lint(wiki_dir: Any) -> tuple[Any, Any, bool, int]:
    """Load the vault at `wiki_dir`, lint it, and report whether its index is current.

    Every path here derives from `wiki_dir`. Earlier this function took the tree
    as an argument and then linted, rendered and index-checked against module
    constants anchored to the installed package, so it produced the same verdict
    whatever it was pointed at (register #59).
    """
    from scout import vault
    from scripts.gen_index import collect_lint, render_index

    pages = vault.load_pages(wiki_dir)
    lint = collect_lint(pages, wiki_dir=wiki_dir)
    rendered = render_index(pages, wiki_dir=wiki_dir)
    index_path = wiki_dir / "index.md"
    current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    return pages, lint, rendered == current, len(pages)


def _resolved_wiki_dir(cfg: Config) -> Path:
    """The vault beneath the caller's checkout, not beneath this package."""
    from scout.cli.commands.wiki import _wiki_dir

    return _wiki_dir(cfg)


def verify_vault(*, config: Injected = None) -> CommandResult:
    """Lint page frontmatter and confirm `wiki/index.md` is current."""
    cfg: Config = config
    try:
        pages, lint, index_current, count = _pages_and_lint(_resolved_wiki_dir(cfg))
    except ValueError as exc:
        # A malformed vault root is a configuration problem, not a finding.
        raise infrastructure_error(
            "the wiki tree could not be read", hint=str(exc), retryable=False
        ) from exc

    ok = lint.ok and index_current
    messages = [f"LINT ERROR: {e}" for e in lint.errors]
    messages += [f"LINT WARN:  {w}" for w in lint.warnings]
    if not index_current:
        messages.append(
            "INDEX STALE: wiki/index.md is out of date — run `snpmemory index` to regenerate."
        )

    return CommandResult(
        exit_code=ExitCode.SUCCESS if ok else ExitCode.SEMANTIC_FAILURE,
        data={
            "pages": count,
            "errors": list(lint.errors),
            "warnings": list(lint.warnings),
            "index_current": index_current,
            "status": "pass" if ok else "fail",
        },
        summary=(
            f"{count} pages · {len(lint.errors)} errors · {len(lint.warnings)} warnings · "
            f"index {'current' if index_current else 'STALE'} — {'PASS' if ok else 'FAIL'}"
        ),
        messages=tuple(messages),
    )


def verify_secrets(*, history: bool = False, config: Injected = None) -> CommandResult:
    """Scan tracked, staged, and untracked bytes for credential-shaped values.

    Args:
        history: Also scan every reachable Git object. Slower, and the mode the
            CI gate runs.
    """
    from scripts.scan_secrets import scan_all_current, scan_git_history

    cfg: Config = config
    repo = cfg.require_repo()
    findings = list(scan_all_current(repo))
    if history:
        findings += list(scan_git_history(repo))

    ok = not findings
    return CommandResult(
        exit_code=ExitCode.SUCCESS if ok else ExitCode.SEMANTIC_FAILURE,
        data={
            # `Finding.format()` is already redacted at the source; no matched
            # value ever reaches this payload.
            "findings": [f.format() for f in findings],
            "count": len(findings),
            "scanned_history": history,
            "status": "pass" if ok else "fail",
        },
        summary=(
            "Secret scan passed: no prohibited values found."
            if ok
            else f"Secret scan failed: {len(findings)} finding(s); matched values are redacted."
        ),
        messages=tuple(f.format() for f in findings),
    )


def _pgvector_backend(cfg: Config) -> Any:
    """Build the production backend from resolved config, not from `os.environ`.

    Both the connection and the embedding gateway are passed explicitly. The
    resolver deliberately does not export anything, so a component that read the
    process environment here would silently see nothing.
    """
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.chunker import LiteLLMBatchEmbedder
    from scout.config import ConfigError, postgres_settings

    try:
        settings = postgres_settings("query", env=cfg.values)
    except ConfigError as exc:
        raise infrastructure_error(
            "database configuration is incomplete",
            hint=str(exc),
            retryable=False,
        ) from exc

    embedder = LiteLLMBatchEmbedder(
        base_url=cfg.get("LITELLM_BASE_URL"),
        api_key=cfg.get("LITELLM_MASTER_KEY"),
    )
    return PgVectorRlsBackend(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
        embedder=embedder,
    )


def verify_addresses(*, config: Injected = None) -> CommandResult:
    """Check that every page's `sources[]` hint still retrieves its own file."""
    import asyncio

    from scout import vault
    from scripts.verify_addresses import (
        VerifyStatus,
        _close_backend,
        _collect_addresses,
        verify_all,
    )

    cfg: Config = config
    addresses = _collect_addresses(vault.load_pages(_resolved_wiki_dir(cfg)))
    if not addresses:
        return CommandResult(
            data={"checked": 0, "pass": 0, "fail": 0, "drift": 0, "status": "pass"},
            summary="No sources[] addresses found in the vault. Nothing to verify.",
        )

    backend = _pgvector_backend(cfg)

    async def run() -> list[Any]:
        try:
            return await verify_all(backend, addresses)
        finally:
            await _close_backend(backend)

    try:
        reports = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - driver text may carry a DSN
        raise infrastructure_error(
            f"address verification could not complete ({type(exc).__name__})",
            hint="check that PostgreSQL and the LiteLLM gateway are reachable",
        ) from exc

    counts = {s.value: sum(1 for r in reports if r.status is s) for s in VerifyStatus}
    ok = all(r.status is VerifyStatus.PASS for r in reports)
    return CommandResult(
        exit_code=ExitCode.SUCCESS if ok else ExitCode.SEMANTIC_FAILURE,
        data={
            "checked": len(reports),
            **counts,
            "status": "pass" if ok else "fail",
            "addresses": [
                {
                    "page": r.scoped_address.page_path,
                    "source_index": r.scoped_address.source_index,
                    "path": r.path,
                    "status": r.status.value,
                    "retrieved_from": list(r.matched_files),
                }
                for r in reports
                if r.status is not VerifyStatus.PASS
            ],
        },
        summary=(
            f"{len(reports)} address(es) checked — "
            f"{counts.get('pass', 0)} PASS · {counts.get('fail', 0)} FAIL · "
            f"{counts.get('drift', 0)} DRIFT · "
            # Reported separately because it needs a different fix: FAIL and
            # DRIFT say re-mint the hint, NO_EVIDENCE says the source itself
            # yields nothing and no hint can repair that (SH-2).
            f"{counts.get('no_evidence', 0)} NO_EVIDENCE"
        ),
    )


def verify_extraction(*, config: Injected = None) -> CommandResult:
    """Name every indexed document that did not arrive whole.

    A structural extraction failure is quiet by design: the parser logs it and
    returns a *smaller document*, ingestion writes that document, and the run
    reports `ingested_ok` because the write did succeed. On 2026-09-15 that is
    how a 7-figure paper reached the index with zero figures described and
    nothing said so for four days.

    Reads `rag_documents.extraction_status` (migration 008), which records the
    outcome of a parse, never a capability. Documents ingested before that
    column existed are reported as **unknown**, not as healthy: "no record" is
    not evidence of completeness.
    """
    import asyncio

    import asyncpg

    from scout.config import ConfigError, postgres_settings

    cfg: Config = config
    try:
        settings = postgres_settings("query", env=cfg.values)
    except ConfigError as exc:
        raise infrastructure_error(
            "database configuration is incomplete", hint=str(exc), retryable=False
        ) from exc

    async def read() -> list[Any]:
        conn = await asyncpg.connect(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            user=settings.user,
            password=settings.password,
        )
        try:
            # Fail-closed RLS: rag_documents is filtered by
            # `scout.current_depts`, so an unscoped read returns nothing at all
            # and this command would report "no incomplete documents" about a
            # table it could not see.
            from scout.policy import CANONICAL_DEPARTMENTS

            await conn.execute(
                "SELECT set_config('scout.current_depts', $1, false);",
                ",".join(sorted(CANONICAL_DEPARTMENTS)),
            )
            return list(
                await conn.fetch(
                    """
                    SELECT source_uri, title, extraction_status
                    FROM rag_documents
                    ORDER BY source_uri;
                    """
                )
            )
        finally:
            await conn.close()

    try:
        rows = asyncio.run(read())
    except Exception as exc:  # noqa: BLE001 - driver text may carry a DSN
        raise infrastructure_error(
            f"the index could not be read ({type(exc).__name__})",
            hint="check that PostgreSQL is reachable and the role can read "
            "rag_documents",
        ) from exc

    import json as _json

    incomplete: list[dict[str, Any]] = []
    unknown: list[str] = []
    complete = 0
    for row in rows:
        raw = row["extraction_status"]
        if raw is None:
            unknown.append(str(row["source_uri"]))
            continue
        record = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        if record.get("complete"):
            complete += 1
            continue
        incomplete.append(
            {
                "source_uri": str(row["source_uri"]),
                "title": row["title"],
                "incomplete": record.get("incomplete", []),
                "extractors": record.get("extractors", {}),
                "figure_count": record.get("figure_count"),
                "figures_described": record.get("figures_described"),
            }
        )

    # A check that cannot see its subject is not a pass. Documents predating
    # the column read as unknown; some unknowns are expected until they are
    # re-ingested, but *only* unknowns means this command answered nothing.
    # An empty read is not a clean bill of health. It means the index holds no
    # documents, or this connection cannot see them -- and reporting "nothing
    # incomplete" for a table it never read is the T5.1 shape this command
    # exists to catch, one layer up.
    saw_nothing = not rows
    nothing_recorded = complete == 0 and not incomplete and bool(unknown)
    ok = not incomplete and not nothing_recorded and not saw_nothing
    return CommandResult(
        exit_code=ExitCode.SUCCESS if ok else ExitCode.SEMANTIC_FAILURE,
        data={
            "status": "pass" if ok else "fail",
            "checked": len(rows),
            "complete": complete,
            "incomplete_count": len(incomplete),
            "unknown_count": len(unknown),
            "documents": incomplete,
            "unknown": unknown,
        },
        summary=(
            f"{len(rows)} document(s) checked — {complete} complete · "
            f"{len(incomplete)} incomplete · {len(unknown)} unrecorded"
            + (
                " — the index returned no documents at all, so this check "
                "answered nothing about the corpus"
                if saw_nothing
                else " — no document records an extraction outcome, so this "
                "check answered nothing; re-ingest to record one"
                if nothing_recorded
                else " (unrecorded means ingested before the outcome was "
                "stored, which is unknown rather than healthy)"
            )
        ),
    )


def verify_groundedness(
    *, changed_only: bool = False, config: Injected = None
) -> CommandResult:
    """Judge each page's body against the sources it cites.

    Args:
        changed_only: Judge only pages this branch changed — one model call per
            changed page instead of one per vault page.
    """
    from scripts import verify_groundedness as impl

    cfg: Config = config
    cfg.require_repo()

    argv = ["--changed-only"] if changed_only else []
    # The judge is built from resolved config, not from `os.environ`: the
    # resolver exports nothing, so `from_env()` with no argument would find an
    # empty gateway URL and report an infrastructure failure that isn't real.
    code = impl.main(
        argv,
        backend_factory=lambda: _pgvector_backend(cfg),
        judge_factory=lambda: impl.LiteLLMJudge.from_env(cfg.values),
    )
    exit_code = ExitCode(code) if code in (0, 1) else ExitCode.INFRASTRUCTURE
    if exit_code is ExitCode.INFRASTRUCTURE:
        raise infrastructure_error(
            "groundedness verification could not complete",
            hint="check that the snp-llm route resolves and the database is reachable",
        )
    return CommandResult(
        exit_code=exit_code,
        data={
            "scope": "changed" if changed_only else "vault",
            "status": "pass" if code == 0 else "fail",
        },
        summary="All judged pages are grounded."
        if code == 0
        else "Unsupported claims were found.",
    )


#: Ordered cheapest-first, so a lint error is reported in milliseconds instead
#: of after a full pass over the corpus and a model call per page.
DEFAULT_STAGES: tuple[tuple[str, Any], ...] = (
    ("vault", verify_vault),
    ("secrets", verify_secrets),
    # One SQL read, and no embedding or model call — so it sits ahead of the
    # two paid stages, which is what "cheapest first" means here. An index
    # missing the evidence its sources contain makes a groundedness verdict on
    # that evidence worth paying for only after it is fixed.
    ("extraction", verify_extraction),
    ("addresses", verify_addresses),
    ("groundedness", verify_groundedness),
)


def check(
    *,
    stages: Injected = None,
    config: Injected = None,
) -> CommandResult:
    """Run every verification in order and stop at the first failure.

    Stops rather than continuing because the stages are not independent: an
    unlinted vault makes address results meaningless, and there is no value in
    paying for a model call per page to judge a tree that does not parse.
    """
    stages = tuple(stages) if stages is not None else DEFAULT_STAGES
    completed: dict[str, Any] = {}
    messages: list[str] = []
    for name, run in stages:
        messages.append(f"[check] {name}")
        result = run(config=config)
        completed[name] = result.data
        messages.extend(result.messages)
        if result.exit_code is not ExitCode.SUCCESS:
            return CommandResult(
                exit_code=result.exit_code,
                data={"failed_stage": name, "stages": completed},
                summary=f"check failed at {name}: {result.summary}",
                messages=tuple(messages),
                error=result.error,
            )
    return CommandResult(
        data={"failed_stage": None, "stages": completed},
        summary="check passed: vault, secrets, addresses, groundedness.",
        messages=tuple(messages),
    )
