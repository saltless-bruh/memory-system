"""PostgreSQL 16 + pgvector & RLS Backend for Scout (V2 Production Adapter).

Implements `RagBackend` protocol with:
  1. Fail-closed Row-Level Security (RLS) via transaction-scoped `set_config('scout.current_depts', ...)`.
  2. SQL-native Hybrid Search: Dense Vector (HNSW cosine) + Sparse Keyword (GIN tsvector)
     fused via Reciprocal Rank Fusion (RRF).
  3. Pre-filtering by source file path and precise metadata extraction.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import asyncpg

from scout.chunker import AsyncEmbedder, LiteLLMBatchEmbedder
from scout.config import postgres_settings
from scout.types import RagBackend, RagChunk, Scope

DEFAULT_RRF_K = 60
DEFAULT_RAW_RANK_PENALTY = 15
DEFAULT_CONTESTED_RANK_PENALTY = 30
# Measured against the live LiteLLM -> gemini-embedding-001 route on
# 2026-08-31: a single-query embedding takes 407-611 ms. The former 250 ms
# budget expired on every request, so retrieval silently fell back to the
# English tsvector arm and non-English queries returned nothing at all.
# Three seconds clears the observed worst case with headroom while still
# shedding a genuinely dead embedding service quickly.
DEFAULT_DENSE_TIMEOUT_SECONDS = 3.0

logger = logging.getLogger(__name__)

#: The text `probe_dense` embeds. Any short string would do; a fixed one keeps
#: the probe from ever carrying a caller's query into a log or a cache.
_PROBE_TEXT = "dense arm health probe"


@dataclass
class DenseArmHealth:
    """What this process has seen of the dense arm, shared by every sibling.

    The sparse fallback is deliberate -- a dead embedder degrades retrieval
    rather than turning it off -- and it used to be *silent*: the exception was
    folded into a per-hit `degraded` flag an agent is free to ignore, with no
    log line and no count. Under the 2026-09-26 DNS flap recall@1 fell to 0.38
    and scout itself said nothing; only litellm's own healthcheck noticed. A
    scout whose *own* embedder URL or key is wrong has no such bystander, and
    answered from the sparse arm for as long as it ran while looking healthy
    throughout. This record is what `/healthz`
    reports, so the compose healthcheck -- and `snpmemory status`, which rolls
    an unhealthy service into `degraded` -- can see it.

    Only the exception *class* is kept. The message of a connection error can
    carry the gateway URL or a key fragment, and this record leaves the process
    through an unauthenticated health route.
    """

    degraded_total: int = 0
    by_reason: Counter[str] = field(default_factory=Counter)
    last_reason: str | None = None
    last_error: str | None = None
    #: The outcome of the most recent attempt, ``None`` before the first. An
    #: ordering flag rather than two timestamps: two attempts inside one clock
    #: tick must still resolve to whichever happened last.
    last_attempt_ok: bool | None = None

    def record_ok(self) -> None:
        self.last_attempt_ok = True

    def record_degraded(
        self, reason: str, error: BaseException | None, *, retrieval: bool
    ) -> None:
        # Counts are of *answers* that came back degraded. A failed health
        # probe moves the state but is not an answer anyone received, and
        # counting it would let a quiet, broken scout's count grow with the
        # healthcheck interval instead of with its traffic.
        if retrieval:
            self.degraded_total += 1
            self.by_reason[reason] += 1
        self.last_reason = reason
        self.last_error = type(error).__name__ if error is not None else None
        self.last_attempt_ok = False

    @property
    def state(self) -> str:
        """`ok` or `degraded` by the most recent attempt; `unknown` before any."""
        if self.last_attempt_ok is None:
            return "unknown"
        return "ok" if self.last_attempt_ok else "degraded"


# The lexical arm is built in two tiers because it does a different job
# depending on whether the dense arm is available, and measurement on
# 2026-09-13 showed one form cannot serve both.
#
#   HYBRID (dense present) -- strict conjunction.
#       The dense arm supplies recall; the lexical arm's value is precision on
#       terms that must match exactly, such as `CVE-2026-7309`, a path, or a
#       symbol. Relaxing it to a disjunction here cost recall@1 en 0.95 -> 0.85
#       and vi 0.80 -> 0.60: topically adjacent pages with heavy lexical overlap
#       displaced the correct page from rank 1 (`Concepts/Self-Hosted Mesh
#       VPN.md` over `Entities/Headscale.md`). recall@3 stayed at 1.00, so the
#       right pages were still found -- only the ordering was damaged. A per-arm
#       RRF penalty was tried and rejected: adjacent RRF ranks differ by ~1/61
#       vs ~1/62, so any comparable second signal reshuffles rank 1, and a
#       constant large enough to prevent that simply silences the arm.
#
#   SPARSE-ONLY (dense unavailable) -- relaxed disjunction.
#       With no dense arm there is nothing to supply recall, and a conjunction
#       returns nothing at all: 35 of 40 benchmark queries matched zero chunks,
#       leaving recall@1 0.10 overall and 0.00 for Vietnamese. The documented
#       degradation path was therefore a placebo. Relaxing the conjunction here
#       removed every empty result and raised recall@5 to 0.62 / en recall@3 to
#       0.90, which is what a fallback has to deliver.
#
# Both tiers reuse PostgreSQL's own normalisation, stemming and stop-word
# handling, so there is no hand-rolled tokenizer to drift and no injection
# surface -- `plainto_tsquery` sanitises its input.


def _strict_tsquery(config: str, param: str) -> str:
    """Conjunctive query: every lexeme must be present. Precision-oriented."""
    return f"plainto_tsquery('{config}', {param})"


def _relaxed_tsquery(config: str, param: str) -> str:
    """Disjunctive query: any lexeme may match, ranked by coverage.

    `plainto_tsquery` only ever emits `&` between lexemes, so substituting `|`
    cannot corrupt a phrase or negation operator, and an empty query stays an
    empty tsquery that matches nothing rather than raising.
    """
    return f"replace(plainto_tsquery('{config}', {param})::text, '&', '|')::tsquery"


_TsQueryBuilder = Callable[[str, str], str]


def _match(build: _TsQueryBuilder, param: str) -> str:
    """Predicate selecting chunks sharing lexemes with the query, either tier."""
    return (
        f"(c.tsv @@ {build('english', param)}"
        f" OR c.tsv_simple @@ {build('simple', param)})"
    )


def _rank(build: _TsQueryBuilder, param: str) -> str:
    """Lexical score summing the English and language-agnostic arms."""
    return (
        f"COALESCE(ts_rank(c.tsv, {build('english', param)}), 0)\n"
        f"                      + COALESCE("
        f"ts_rank(c.tsv_simple, {build('simple', param)}), 0)"
    )


def _strict_match(param: str) -> str:
    return _match(_strict_tsquery, param)


def _strict_rank(param: str) -> str:
    return _rank(_strict_tsquery, param)


def _sparse_match(param: str) -> str:
    return _match(_relaxed_tsquery, param)


def _sparse_rank(param: str) -> str:
    return _rank(_relaxed_tsquery, param)


_HYBRID_QUERY = f"""
WITH vector_matches AS (
    SELECT c.chunk_id, c.doc_id, c.chunk_text, c.metadata, d.source_uri,
           ROW_NUMBER() OVER (ORDER BY c.embedding <=> $1::vector) AS v_rank
    FROM rag_chunks c
    JOIN rag_documents d ON d.doc_id = c.doc_id
    WHERE ($9::text IS NULL OR c.metadata->>'corpus' = $9::text)
      AND ($2::text IS NULL OR d.source_uri = $2::text)
    ORDER BY c.embedding <=> $1::vector
    LIMIT $3
),
text_matches AS (
    SELECT c.chunk_id, c.doc_id, c.chunk_text, c.metadata, d.source_uri,
           ROW_NUMBER() OVER (
               ORDER BY {_strict_rank("$4")}
                      DESC,
                        c.chunk_id
           ) AS t_rank
    FROM rag_chunks c
    JOIN rag_documents d ON d.doc_id = c.doc_id
    WHERE ($9::text IS NULL OR c.metadata->>'corpus' = $9::text)
      AND {_strict_match("$4")}
      AND ($2::text IS NULL OR d.source_uri = $2::text)
    ORDER BY {_strict_rank("$4")}
           DESC, c.chunk_id
    LIMIT $3
),
combined AS (
    SELECT COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
           COALESCE(v.doc_id, t.doc_id) AS doc_id,
           COALESCE(v.chunk_text, t.chunk_text) AS chunk_text,
           COALESCE(v.metadata, t.metadata) AS metadata,
           COALESCE(v.source_uri, t.source_uri) AS source_uri,
           v.v_rank,
           t.t_rank,
           $6::double precision
             + CASE
                   WHEN COALESCE(v.metadata, t.metadata)->>'type' = 'raw'
                   THEN $7::double precision
                   ELSE 0.0
               END
             + CASE
                   WHEN COALESCE(v.metadata, t.metadata)->>'contested' = 'true'
                   THEN $8::double precision
                   ELSE 0.0
               END AS class_k
    FROM vector_matches v
    FULL OUTER JOIN text_matches t ON v.chunk_id = t.chunk_id
),
scored AS (
    SELECT chunk_id, doc_id, chunk_text, metadata, source_uri,
           COALESCE(1.0 / (class_k + v_rank), 0.0)
             + COALESCE(1.0 / (class_k + t_rank), 0.0) AS rrf_score
    FROM combined
),
page_best AS (
    SELECT DISTINCT ON (doc_id)
           chunk_id, doc_id, chunk_text, metadata, source_uri, rrf_score
    FROM scored
    ORDER BY doc_id, rrf_score DESC, chunk_id
)
SELECT chunk_id, chunk_text, metadata, source_uri, rrf_score
FROM page_best
ORDER BY rrf_score DESC, source_uri, chunk_id
LIMIT $5;
"""

_SPARSE_QUERY = f"""
WITH text_matches AS (
    SELECT c.chunk_id, c.doc_id, c.chunk_text, c.metadata, d.source_uri,
           ROW_NUMBER() OVER (
               ORDER BY {_sparse_rank("$3")}
                      DESC,
                        c.chunk_id
           ) AS t_rank
    FROM rag_chunks c
    JOIN rag_documents d ON d.doc_id = c.doc_id
    WHERE ($8::text IS NULL OR c.metadata->>'corpus' = $8::text)
      AND {_sparse_match("$3")}
      AND ($1::text IS NULL OR d.source_uri = $1::text)
    ORDER BY {_sparse_rank("$3")}
           DESC, c.chunk_id
    LIMIT $2
),
scored AS (
    SELECT chunk_id, doc_id, chunk_text, metadata, source_uri,
           1.0 / (
               $5::double precision
               + CASE
                     WHEN metadata->>'type' = 'raw'
                     THEN $6::double precision
                     ELSE 0.0
                 END
               + CASE
                     WHEN metadata->>'contested' = 'true'
                     THEN $7::double precision
                     ELSE 0.0
                 END
               + t_rank
           ) AS rrf_score
    FROM text_matches
),
page_best AS (
    SELECT DISTINCT ON (doc_id)
           chunk_id, doc_id, chunk_text, metadata, source_uri, rrf_score
    FROM scored
    ORDER BY doc_id, rrf_score DESC, chunk_id
)
SELECT chunk_id, chunk_text, metadata, source_uri, rrf_score
FROM page_best
ORDER BY rrf_score DESC, source_uri, chunk_id
LIMIT $4;
"""


class PgVectorRlsBackend(RagBackend):
    """Production V2 RAG backend querying PostgreSQL 16 + pgvector with RLS."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
        embedder: AsyncEmbedder | None = None,
        pool: asyncpg.Pool | None = None,
        rrf_k: int = DEFAULT_RRF_K,
        raw_rank_penalty: int = DEFAULT_RAW_RANK_PENALTY,
        contested_rank_penalty: int = DEFAULT_CONTESTED_RANK_PENALTY,
        dense_timeout_seconds: float = DEFAULT_DENSE_TIMEOUT_SECONDS,
        corpus: str | None = None,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if raw_rank_penalty < 0 or contested_rank_penalty < 0:
            raise ValueError("rank penalties must not be negative")
        if dense_timeout_seconds <= 0:
            raise ValueError("dense_timeout_seconds must be positive")
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.embedder = embedder or LiteLLMBatchEmbedder()
        self._pool = pool
        self.rrf_k = rrf_k
        self.raw_rank_penalty = raw_rank_penalty
        self.contested_rank_penalty = contested_rank_penalty
        self.dense_timeout_seconds = dense_timeout_seconds
        self.corpus = corpus
        # One record per process, not per corpus tier: `with_corpus` makes a
        # shallow copy, so `wiki_quote`'s raw-tier sibling reports into the
        # same record as the wiki-tier backend `/healthz` is handed.
        self.health = DenseArmHealth()

    def with_corpus(self, corpus: str | None) -> PgVectorRlsBackend:
        """A sibling reading another corpus tier, sharing this one's pool.

        The served engine is scoped to `corpus="wiki"` (scout/serve.py), which
        is right for `wiki_search`: the vault is what an agent searches. It is
        wrong for `wiki_quote`, whose addresses point into `raw/`, where chunks
        carry no corpus stamp -- a wiki-scoped backend would answer `no_source`
        for every address that exists, which reads exactly like a hint that
        retrieved nothing.

        The pool is shared rather than duplicated: two pools against the same
        database would double the connection budget for one extra WHERE clause.
        """
        if corpus == self.corpus:
            return self
        sibling = copy.copy(self)
        sibling.corpus = corpus
        return sibling

    async def _get_pool(self) -> asyncpg.Pool:
        """Lazily creates and returns the connection pool.

        The environment is consulted only for what the caller did not supply. A
        fully parameterised backend must not need `POSTGRES_*` exported as well:
        the constructor advertises those five values, and a caller that resolved
        them itself -- from a `.env` mapping, from a secret store -- would
        otherwise be forced to put them into `os.environ` anyway, where every
        subprocess inherits them.
        """
        if self._pool is None:
            supplied = (self.host, self.port, self.database, self.user, self.password)
            if all(value is not None for value in supplied):
                host, port, database, user, password = supplied
            else:
                settings = postgres_settings("query")
                host = self.host or settings.host
                port = self.port or settings.port
                database = self.database or settings.database
                user = self.user or settings.user
                password = self.password or settings.password
            self._pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                min_size=2,
                max_size=20,
            )
        return self._pool

    async def close(self) -> None:
        """Closes the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def dense_health(self) -> dict[str, object]:
        """The dense arm's state and degradation counts, for `/healthz`."""
        health = self.health
        return {
            "dense": health.state,
            "degraded_total": health.degraded_total,
            "by_reason": dict(health.by_reason),
            "last_reason": health.last_reason,
            "last_error": health.last_error,
        }

    async def probe_dense(self) -> bool:
        """Embed a fixed probe through the query path and record the outcome.

        The health route calls this only while the arm reads `degraded`, so a
        recovered embedder is seen without waiting for an agent query, and a
        healthy one costs no embedding call per healthcheck. A caller with a
        tighter deadline than the query budget wraps this in `asyncio.timeout`.
        """
        vector, _reason = await self._embed_query(_PROBE_TEXT, retrieval=False)
        return vector is not None

    async def _embed_query(
        self, text: str, *, retrieval: bool = True
    ) -> tuple[str | None, str | None]:
        """The pgvector literal for `text`, or the reason there is none.

        Every failure is recorded and logged here, once, whichever caller asked.
        """
        error: BaseException | None = None
        try:
            embeddings = await asyncio.wait_for(
                self.embedder.aembed_texts([text]),
                timeout=self.dense_timeout_seconds,
            )
            if len(embeddings) == 1 and len(embeddings[0]) == 1024:
                self.health.record_ok()
                return f"[{','.join(str(x) for x in embeddings[0])}]", None
            reason = "embedding_invalid"
        except TimeoutError as exc:
            reason, error = "embedding_timeout", exc
        except Exception as exc:  # noqa: BLE001 - provider failures all use sparse fallback
            reason, error = "embedding_error", exc
        self.health.record_degraded(reason, error, retrieval=retrieval)
        logger.warning(
            "dense arm unavailable (%s%s); %s. %d degraded retrievals since start.",
            reason,
            f": {type(error).__name__}" if error is not None else "",
            "answering from the relaxed sparse arm only"
            if retrieval
            else "health probe failed",
            self.health.degraded_total,
        )
        return None, reason

    def _resolve_depts(self, scope: Scope | None) -> str:
        """Extracts and sanitizes allowed department string from caller Scope."""
        if scope is None:
            return ""
        return ",".join(sorted(scope.departments))

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        """Retrieves verbatim passages using Hybrid RRF Search enforced by Postgres RLS."""
        if not hint or not hint.strip():
            return ()
        if k <= 0:
            return ()

        # 1. Generate the dense query vector within a bounded budget. The
        # sparse arm is independently useful, so provider failure degrades the
        # query instead of turning the whole retrieval surface off.
        emb_str, degraded_reason = await self._embed_query(hint)

        # 2. Extract department clearance string
        depts_str = self._resolve_depts(scope)

        # 3. Execute Hybrid SQL Query inside transaction with session clearance
        pool = await self._get_pool()
        async with (
            pool.acquire() as conn,
            conn.transaction(),
        ):
            # Set transaction-local clearance setting
            await conn.execute(
                "SELECT set_config('scout.current_depts', $1, true);",
                depts_str,
            )

            candidate_k = max(20, k * 4)
            if emb_str is None:
                rows = await conn.fetch(
                    _SPARSE_QUERY,
                    path,
                    candidate_k,
                    hint,
                    k,
                    self.rrf_k,
                    self.raw_rank_penalty,
                    self.contested_rank_penalty,
                    self.corpus,
                )
            else:
                rows = await conn.fetch(
                    _HYBRID_QUERY,
                    emb_str,
                    path,
                    candidate_k,
                    hint,
                    k,
                    self.rrf_k,
                    self.raw_rank_penalty,
                    self.contested_rank_penalty,
                    self.corpus,
                )

        chunks: list[RagChunk] = []
        for row in rows:
            meta_val = row["metadata"]
            if isinstance(meta_val, str):
                try:
                    meta_dict = json.loads(meta_val)
                except Exception:
                    meta_dict = {}
            elif isinstance(meta_val, dict):
                meta_dict = meta_val
            else:
                meta_dict = {}

            loc = meta_dict.get("loc")
            chunk_meta = {key: str(value) for key, value in meta_dict.items()}
            chunk_meta["degraded"] = "true" if degraded_reason else "false"
            if degraded_reason:
                chunk_meta["degraded_reason"] = degraded_reason
            chunks.append(
                RagChunk(
                    text=row["chunk_text"],
                    file_path=row["source_uri"],
                    score=float(row["rrf_score"]),
                    loc=str(loc) if loc else None,
                    meta=chunk_meta,
                )
            )

        return chunks
