"""The V3 wiki family: canonical ``read`` and shared-index ``search``.

Both commands use :class:`scout.diy_engine.ScoutDiyEngine`, so the CLI, local
MCP server, and authenticated Scout server share ranking and read envelopes.
Search uses PostgreSQL/pgvector; read always parses the current vault file.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import CliError, infrastructure_error, input_error
from scout.cli.result import CommandResult

if TYPE_CHECKING:
    from scout.diy_engine import ScoutDiyEngine

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def _resolve(pages: list[Any], identifier: str) -> Any:
    """Find one page by path, slug, or title without choosing ambiguously."""
    wanted = identifier.strip()
    if not wanted:
        raise input_error("no page given", hint="pass a title, slug, or path")

    for page in pages:
        if page.rel == wanted or page.path.as_posix() == wanted:
            return page

    lowered = wanted.casefold()
    by_slug = [page for page in pages if page.slug.casefold() == lowered]
    if len(by_slug) == 1:
        return by_slug[0]

    by_title = [page for page in pages if page.title.casefold() == lowered]
    candidates = by_slug or by_title
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise input_error(
            f"{len(candidates)} pages match {wanted!r}",
            hint="pass the path instead",
            candidates=[page.rel for page in candidates],
        )
    raise input_error(
        f"no page matches {wanted!r}",
        hint="run `snpmemory search` to find one, or pass its path under wiki/",
    )


def _wiki_dir(cfg: Config) -> Path:
    """Resolve the served vault beneath the checkout pinned by the dispatcher."""
    root = cfg.require_repo().resolve()
    configured = cfg.get("WIKI_DIR") or "wiki"
    supplied = Path(configured)
    candidate = (supplied if supplied.is_absolute() else root / supplied).resolve()
    if not candidate.is_relative_to(root):
        raise input_error(
            "WIKI_DIR escapes the repository checkout",
            hint="set WIKI_DIR to a directory beneath the active --root",
        )
    return candidate


def _build_search_engine(cfg: Config, wiki_dir: Path) -> ScoutDiyEngine:
    """Build the exact pgvector engine used by the served Scout surface."""
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.chunker import LiteLLMBatchEmbedder
    from scout.config import postgres_settings
    from scout.diy_engine import ScoutDiyEngine

    settings = postgres_settings("query", env=cfg.values)
    embedder = LiteLLMBatchEmbedder(
        base_url=cfg.get("LITELLM_BASE_URL"),
        # Missing/unreachable embeddings are intentionally handled by the
        # backend's sparse degradation arm; they are not configuration failure.
        api_key=cfg.get("LITELLM_MASTER_KEY"),
        model=cfg.get("LITELLM_EMBED_MODEL"),
    )
    backend = PgVectorRlsBackend(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
        embedder=embedder,
        corpus="wiki",
    )
    return ScoutDiyEngine.from_vault(
        embedder,
        wiki_dir=wiki_dir,
        rag_backend=backend,
    )


class _ReadOnlyEmbedder:
    """Uncallable structural adapter for an engine used only for disk reads."""

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        del texts
        raise RuntimeError("a read-only wiki engine cannot embed")


async def _close_backend(engine: ScoutDiyEngine) -> None:
    backend = getattr(engine, "rag_backend", None)
    close = getattr(backend, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def read_async(
    page: str,
    *,
    dept: str,
    mode: str = "full",
    section: str | None = None,
    config: Injected = None,
) -> CommandResult:
    """Read a canonical page envelope by title, slug, or path."""
    from scout.cli.commands.rag import _scope_for
    from scout.diy_engine import ScoutDiyEngine

    cfg: Config = config
    wiki_dir = _wiki_dir(cfg)
    scope = _scope_for(dept)
    engine = ScoutDiyEngine.from_vault(_ReadOnlyEmbedder(), wiki_dir=wiki_dir)

    try:
        found = await engine.wiki_read(page, mode=mode, section=section, scope=scope)
    except CliError:
        raise
    except (KeyError, ValueError) as exc:
        raise input_error(
            str(exc), hint="pass a valid page, mode, and section"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - disk/parser failures are infrastructure
        raise infrastructure_error(
            "the wiki page could not be read",
            hint="check the checkout and wiki path, then retry",
            retryable=False,
            cause=type(exc).__name__,
        ) from exc

    if found.mode == "tldr":
        summary = found.tldr
    elif found.mode == "outline":
        summary = "\n".join(str(item.get("heading", "")) for item in found.outline)
    elif found.mode == "section":
        summary = "\n\n".join(found.sections.values())
    else:
        summary = found.body
    return CommandResult(
        data=found.canonical(),
        summary=summary,
        messages=(f"{found.title} — {found.path}",),
    )


def read(
    page: str,
    *,
    dept: str,
    mode: str = "full",
    section: str | None = None,
    config: Injected = None,
) -> CommandResult:
    """Synchronous CLI adapter for :func:`read_async`."""
    import asyncio

    return asyncio.run(
        read_async(
            page,
            dept=dept,
            mode=mode,
            section=section,
            config=config,
        )
    )


async def search_async(
    query: str,
    *,
    dept: str,
    limit: int = 5,
    seen: list[str] | None = None,
    config: Injected = None,
) -> CommandResult:
    """Rank distinct vault pages through the shared pgvector index."""
    from scout.cli.commands.rag import _scope_for

    cfg: Config = config
    wiki_dir = _wiki_dir(cfg)
    if limit <= 0:
        raise input_error("--limit must be positive", hint="try --limit 5")
    scope = _scope_for(dept)

    try:
        engine = _build_search_engine(cfg, wiki_dir)
    except CliError:
        raise
    except Exception as exc:  # noqa: BLE001 - one envelope for any wiring fault
        raise infrastructure_error(
            "the wiki search backend could not be configured",
            hint="check POSTGRES_* and LITELLM_* settings, then retry",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    try:
        hits = await engine.wiki_search(
            query,
            k=limit,
            seen=seen or (),
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001 - database/provider faults are one class
        raise infrastructure_error(
            "the wiki search backend could not be reached",
            hint=(
                "search requires postgres; a failed embedding route degrades "
                "automatically to sparse retrieval"
            ),
            retryable=True,
            cause=type(exc).__name__,
        ) from exc
    finally:
        await _close_backend(engine)

    return CommandResult(
        data={
            "query": query,
            "count": len(hits),
            "hits": [hit.canonical() for hit in hits],
        },
        summary=(
            "\n".join(f"{hit.page_id}  {hit.path}" for hit in hits)
            if hits
            else f"no page matches {query!r}"
        ),
        messages=(f"{len(hits)} hit(s) for {query!r}",),
    )


def search(
    query: str,
    *,
    dept: str,
    limit: int = 5,
    seen: list[str] | None = None,
    config: Injected = None,
) -> CommandResult:
    """Synchronous CLI adapter for :func:`search_async`."""
    import asyncio

    return asyncio.run(
        search_async(
            query,
            dept=dept,
            limit=limit,
            seen=seen,
            config=config,
        )
    )


def ingest_wiki_command(
    *,
    dir: str = "wiki",  # noqa: A002 - matches the `ingest` command's flag name
    dry_run: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Index every vault page into pgvector under the wiki corpus tier."""
    import asyncio

    from scout.wiki_ingest import ingest_wiki

    del config
    results = asyncio.run(ingest_wiki(Path(dir), dry_run=dry_run))
    indexed = sum(1 for result in results if result.get("status") == "indexed")
    skipped = len(results) - indexed
    return CommandResult(
        data={
            "pages": len(results),
            "indexed": indexed,
            "skipped": skipped,
            "results": results,
        },
        summary=f"{indexed} pages indexed, {skipped} skipped from {dir}",
    )
