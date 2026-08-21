"""The verification family: `verify-vault`, `verify-addresses`,
`verify-groundedness`, `verify-secrets`, and the `check` aggregate.

These are the first real consumers of `Config`, and they exercise the whole
contract: each one distinguishes a **finding** (the check ran and the vault has
a problem — exit 1) from a **failure** (the check could not run — exit 2), which
is the distinction `ci_address_gate.py` depends on when it decides whether
healing is permitted.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import infrastructure_error
from scout.cli.result import CommandResult, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def _pages_and_lint(wiki_dir: Any) -> tuple[Any, Any, bool, int]:
    """Load the vault, lint it, and report whether the index is current."""
    from scout import vault
    from scripts.gen_index import INDEX_PATH, collect_lint, render_index

    pages = vault.load_pages(wiki_dir)
    lint = collect_lint(pages)
    rendered = render_index(pages)
    current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
    return pages, lint, rendered == current, len(pages)


def verify_vault(*, config: Injected = None) -> CommandResult:
    """Lint page frontmatter and confirm `wiki/index.md` is current."""
    from scout import vault

    cfg: Config = config
    cfg.require_repo()
    try:
        pages, lint, index_current, count = _pages_and_lint(vault.WIKI_DIR)
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


def verify_secrets(
    *, history: bool = False, config: Injected = None
) -> CommandResult:
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
    cfg.require_repo()
    addresses = _collect_addresses(vault.load_pages())
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
            f"{counts.get('drift', 0)} DRIFT"
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
        data={"scope": "changed" if changed_only else "vault", "status": "pass" if code == 0 else "fail"},
        summary="All judged pages are grounded." if code == 0 else "Unsupported claims were found.",
    )


#: Ordered cheapest-first, so a lint error is reported in milliseconds instead
#: of after a full pass over the corpus and a model call per page.
DEFAULT_STAGES: tuple[tuple[str, Any], ...] = (
    ("vault", verify_vault),
    ("secrets", verify_secrets),
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
