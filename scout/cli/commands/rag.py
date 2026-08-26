"""The RAG family: `snpmemory fetch`.

Resolves one `sources[]` address to its verbatim passage. This is the operator's
route into Layer 2 and it goes through `scout.core.rag_fetch` — the same
function the Scout MCP server calls — rather than issuing its own query, so
there is one retrieval path with one post-filter and one no-source contract.

Two invariants this module exists to hold:

**Retrieved text is data.** `FetchResult` carries text and provenance and
nothing else; there is no action field to execute (R-8.5). The command prints
quotes and citations; it never interprets them.

**`all` is not caller authority.** It is a document ACL meaning *visible to
every authenticated caller*. A caller may not claim it, and `--dept` is required
precisely so no invocation silently runs with a wildcard.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import CliError, infrastructure_error, input_error
from scout.cli.result import CommandResult, ExitCode

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def _scope_for(dept: str) -> Any:
    """Build a caller scope from `--dept`, refusing anything non-canonical.

    There is no token to narrow here: a local invocation has no verified caller,
    so the department is stated and validated rather than inherited. `all` fails
    this check like any other non-canonical value, and the hint says why -- a
    caller reaching for it is reaching for authority the ACL does not confer.
    """
    from scout.policy import PolicyValidationError
    from scout.types import Scope

    wanted = dept.strip()
    if not wanted:
        raise input_error(
            "--dept is required", hint="one of: redteam, blueteam, ai_eng, infra"
        )
    try:
        return Scope(departments=frozenset({wanted}))
    except (PolicyValidationError, ValueError) as exc:
        hint = "one of: redteam, blueteam, ai_eng, infra"
        if wanted == "all":
            hint = (
                "`all` is a document ACL meaning 'visible to every authenticated "
                "caller'. It is not a department a caller can hold; " + hint
            )
        raise input_error(f"unknown department {wanted!r}", hint=hint) from exc


def fetch(
    *,
    path: str,
    hint: str,
    loc: str | None = None,
    dept: str,
    k: int = 10,
    config: Injected = None,
) -> CommandResult:
    """Resolve one address to its verbatim passage and citations."""
    import asyncio

    from scout.core import rag_fetch
    from scout.types import Address, FetchStatus

    cfg: Config = config
    cfg.require_repo()
    if k <= 0:
        raise input_error("--k must be positive", hint="try --k 10")

    scope = _scope_for(dept)
    address = Address(path=path, hint=hint, loc=loc)

    try:
        from scout.backends.pgvector import PgVectorRlsBackend
        from scout.chunker import LiteLLMBatchEmbedder
        from scout.config import postgres_settings

        # Everything is wired from the resolved config rather than read from
        # `os.environ`: that is what lets the command work from a checkout whose
        # settings live in `.env`, without exporting thirty variables into the
        # process (and into every subprocess) as a side effect.
        settings = postgres_settings("query", env=cfg.values)
        embedder = LiteLLMBatchEmbedder(
            base_url=cfg.get("LITELLM_BASE_URL"),
            api_key=cfg.require("LITELLM_MASTER_KEY"),
        )
        backend = PgVectorRlsBackend(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            user=settings.user,
            password=settings.password,
            embedder=embedder,
        )
    except CliError:
        raise
    except Exception as exc:  # noqa: BLE001 - one envelope for any wiring fault
        raise infrastructure_error(
            "the RAG backend could not be configured",
            hint=(
                "check POSTGRES_* and LITELLM_* settings; `snpmemory status` "
                "reports whether the stack is reachable"
            ),
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    async def run() -> Any:
        try:
            return await rag_fetch(backend, address, scope=scope, k=k)
        finally:
            close = getattr(backend, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    try:
        outcome = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - one envelope for any transport fault
        # Retrieval needs both the database and the embedding route, so the
        # hint names both rather than sending an operator to look at the one
        # that happens to be healthy.
        raise infrastructure_error(
            "the RAG backend could not be reached",
            hint=(
                "retrieval needs postgres AND the embedding route; bring the "
                "stack up (`docker compose up -d`) and retry"
            ),
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    if outcome.status is FetchStatus.NO_SOURCE:
        # A finding, not a failure: the address named a real file and the hint
        # retrieved nothing on it. Fabricating a passage is the one thing this
        # path must never do (R-4.5).
        return CommandResult(
            exit_code=ExitCode.SEMANTIC_FAILURE,
            data={
                "status": "no_source",
                "path": path,
                "hint": hint,
                "quotes": [],
                "citations": [],
            },
            summary=f"no passage on {path} matches that hint",
            messages=(
                "re-mint the address rather than editing the hint by hand (R-6.3)",
            ),
        )

    return CommandResult(
        data={
            "status": "ok",
            "path": path,
            "hint": hint,
            "quotes": [
                {"text": piece.text, "file_path": piece.file_path, "loc": piece.loc}
                for piece in outcome.context
            ],
            "citations": [
                {
                    "file_path": citation.file_path,
                    "loc": citation.loc,
                    "score": citation.score,
                }
                for citation in outcome.citations
            ],
        },
        summary="\n\n".join(
            f"[{piece.file_path} {piece.loc or ''}]\n{piece.text}"
            for piece in outcome.context
        ),
        messages=(f"{len(outcome.context)} passage(s) from {path}",),
    )
