# For Codex — two corrections in `scout/cli/commands/wiki.py`

Both measured against the repo at `e67a052`. Neither is urgent; both are in
files you are already editing, so a second pass over them later is wasted work.

## 1. `ingest-wiki` reports `0 pages indexed` no matter what it does

`scout/cli/commands/wiki.py:289`:

```python
indexed = sum(1 for result in results if result.get("status") == "indexed")
skipped = len(results) - indexed
```

`"indexed"` is not a status the pipeline can return. The complete vocabulary,
from `scout/ingest.py` and `scout/wiki_ingest.py`:

```
dry_run_ok · ingested_ok · purged_deleted · purged_empty
skipped_no_body · skipped_unmapped_acl · unchanged
```

Measured, against the 8-page sample vault on this branch:

```
$ snpmemory ingest-wiki --dir wiki --dry-run -o json
pages: 6   indexed: 0   skipped: 6
actual statuses: Counter({'dry_run_ok': 6})
```

The command's only visible output is always wrong. `WikiIndexer` had the same
defect and for the same reason — the plan that specified both invented the
token — and it is fixed there in `169bc77`.

There is also a new status to account for. `c22681e` added a content-hash
short-circuit, so an unchanged page now returns `unchanged` rather than being
re-embedded. Folding it into `skipped` hides the distinction an operator most
needs: "nothing needed doing" and "nothing worked" would print identically.

```python
indexed = sum(1 for r in results if r.get("status") == "ingested_ok")
unchanged = sum(1 for r in results if r.get("status") == "unchanged")
skipped = len(results) - indexed - unchanged
```

and say all three in the summary.

## 2. `_build_search_engine` embeds queries through a different route

`scout/cli/commands/wiki.py:85` passes `model=cfg.get("LITELLM_EMBED_MODEL")`.

That variable configures the **gateway** — `config/litellm/config.yaml` resolves
the `snp-embed` route through it — and it is not a client-side model name.
Passing it bypasses the route, and with it the `dimensions: 1024` the route
pins. `PgVectorRlsBackend` builds its own embedder without consulting it, so
the served surface and this CLI path now query through different routes.

The lines immediately below yours already assert that these two paths must
agree: `test_cli_search_engine_is_built_on_the_wiki_tier` exists so the CLI and
`wiki_search` cannot disagree about what the vault contains.

`0311f62` added `scout.chunker.configured_embedding_model()` as the single
definition and removed this override from the three call sites in the engine
lane. Yours is the last one:

```python
    embedder = LiteLLMBatchEmbedder(
        base_url=cfg.get("LITELLM_BASE_URL"),
        api_key=cfg.get("LITELLM_MASTER_KEY"),
    )
```

`tests/test_cli_wiki.py:370` pins the current behaviour with
`LITELLM_EMBED_MODEL="pinned-model-001"` and asserts `seen["model"] ==
"pinned-model-001"`. That test is checking a real thing — that configuration
reaches the embedder — so **update it rather than delete it**: assert that the
embedder's model is `configured_embedding_model(cfg.values)`, and keep the
`base_url` and `api_key` assertions as they are.

## What this does not affect

`ingest_wiki_command` itself is correct on this point: it calls
`ingest_wiki(Path(dir), dry_run=dry_run)` with no embedder, so it builds one
from `configured_embedding_model()` and stamps chunks with the same route the
server queries with. I reported the opposite earlier; that was wrong, and the
correction is the reason this note is only about the counter and the search
path.

## Verification

```bash
rm -rf .agents .codex
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY \
  uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts
```

Baseline after your removals, measured just now: **1336 passed, 0 failed,
29 deselected**; ruff, format and mypy clean.
