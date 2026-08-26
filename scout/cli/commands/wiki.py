"""The wiki family: `read` and `search`.

Both answer from the checkout rather than from a running server. That is the
point: a compiled page is a file in `wiki/`, and reading it should not require
the stack to be up, a token to be present, or a network to exist. CI and an
agent working from a clone get the same answer.

The `snp-wiki` MCP server (`search_notes` / `read_note`) remains the agent-facing
route and these commands do not compete with it — see `scout/cli/mcp_policy.py`
for why neither becomes an MCP tool.

**`search` is a diagnostic, not a preview of what an agent sees.** The two rank
with different engines: this command embeds through LiteLLM (Gemini) and fuses
with RRF over the checkout, while `snp-wiki` embeds in-process with FastEmbed
`bge-small-en-v1.5` @384. Identical queries will return **different orderings**,
so a page that ranks first here may not rank first for an agent, and vice versa.
"Does not compete" is a scope statement; this is the operational consequence, and
it is the part that misleads if it is left unsaid.

Every heavy import happens inside a function. Importing this module must not
read an environment, open a database, or resolve a credential.
"""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import Parameter

from scout.cli.config import Config
from scout.cli.errors import input_error
from scout.cli.result import CommandResult

#: Injected by the dispatcher; never a user-facing flag.
Injected = Annotated[Any, Parameter(parse=False)]


def _resolve(pages: list[Any], identifier: str) -> Any:
    """Find one page by path, slug, or title.

    Resolution is ordered most-specific first, and an ambiguous title is a
    caller error rather than a silent pick: two pages can legitimately share a
    title across categories, and choosing one for the caller is how the wrong
    page gets cited.
    """
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


def read(page: str, *, config: Injected = None) -> CommandResult:
    """Read a compiled page by its title, slug, or path."""
    from scout import vault

    cfg: Config = config
    cfg.require_repo()
    pages = vault.load_pages(vault.WIKI_DIR)
    found = _resolve(pages, page)
    return CommandResult(
        data={
            "path": found.rel,
            "title": found.title,
            "slug": found.slug,
            "department": found.department,
            "summary": found.summary,
            "entities": found.entities,
            "sources": found.sources,
            "last_compiled": found.last_compiled,
            "wikilinks": found.wikilinks,
            "body": found.body,
        },
        # In text mode only `summary` reaches stdout, and what a person running
        # `snpmemory read` wants on stdout is the page. The identifying header
        # goes to stderr with the other diagnostics, so `snpmemory read x > page.md`
        # writes the page and nothing else.
        summary=found.body,
        messages=(f"{found.title} — {found.rel}",),
    )


def search(
    query: str,
    *,
    limit: int = 5,
    config: Injected = None,
) -> CommandResult:
    """Rank vault pages against a free-text query.

    Runs the same engine the wiki server can be swapped onto
    (`scout.diy_engine.ScoutDiyEngine`), reading `wiki/` directly. Embeddings
    come from the LiteLLM route, so the gateway must be reachable — that is an
    infrastructure prerequisite, not a finding, and it exits 2.

    The reported `score` is a **Reciprocal Rank Fusion weight**, not a
    similarity. It combines a cosine rank with a BM25 rank and is capped near
    0.033; reading it as a relevance percentage is wrong, and no threshold on it
    means anything (`docs/ARCHITECTURE_STATUS.md`).
    """
    import asyncio

    from scout import vault
    from scout.cli.errors import infrastructure_error
    from scout.diy_engine import LiteLLMEmbedder, ScoutDiyEngine

    cfg: Config = config
    cfg.require_repo()
    if limit <= 0:
        raise input_error("--limit must be positive", hint="try --limit 5")

    # The gateway address and key are taken from the resolved config, not read
    # from the ambient environment: `Config` exists so a command receives exactly
    # the keys its prerequisite allows, and so nothing has to be exported into
    # `os.environ` for a command to work.
    key = cfg.require("LITELLM_MASTER_KEY")
    base_url = cfg.get("LITELLM_BASE_URL") or "http://localhost:4000"
    # The embedder posts to `/v1/embeddings` relative to `base_url`, while the
    # configured value is the OpenAI-compatible root and already ends in `/v1`.
    # Passing it through unchanged asks the gateway for `/v1/v1/embeddings`.
    base_url = base_url.rstrip("/").removesuffix("/v1")

    async def run() -> list[Any]:
        async with LiteLLMEmbedder(base_url=base_url, api_key=key) as embedder:
            engine = ScoutDiyEngine.from_vault(embedder, wiki_dir=vault.WIKI_DIR)
            with engine:
                return await engine.wiki_search(query, k=limit)

    try:
        hits = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - one envelope for any transport fault
        raise infrastructure_error(
            f"the embedding route at {base_url} could not be reached",
            hint="bring the stack up (`docker compose up -d litellm`) and retry",
            retryable=True,
            cause=type(exc).__name__,
        ) from exc

    return CommandResult(
        data={
            "query": query,
            "count": len(hits),
            "hits": [
                {
                    "page_id": hit.page_id,
                    "path": hit.path,
                    "score": hit.score,
                    "summary": hit.summary,
                }
                for hit in hits
            ],
        },
        summary=(
            "\n".join(f"{hit.page_id}  {hit.path}" for hit in hits)
            if hits
            else f"no page matches {query!r}"
        ),
        messages=(f"{len(hits)} hit(s) for {query!r}",),
    )
