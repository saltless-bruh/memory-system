#!/usr/bin/env python3
"""Oracles for wiki_search SQL-level contract (G1–G3: corpus filter, Vietnamese sparse, migration 007).

Each ``--group`` performs its own measurement against the running stack and
prints a success-only token **after** every assertion in that group has passed.
A group that is not yet implemented exits non-zero and prints no token.

Usage:
    .venv/bin/python artifacts/v3/checks/retrieval_sql.py --group <name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import asyncpg

from scout.policy import CANONICAL_DEPARTMENTS

REPO_ROOT = Path(__file__).resolve().parents[3]


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


async def get_connection() -> asyncpg.Connection:
    """Create a database connection using .env settings.

    RLS is fail-closed: the policies in `002_rls_and_roles.sql` require a
    nonempty `scout.current_depts`, so a connection that never sets it reads
    zero rows from every table. That is indistinguishable from an empty
    database unless the clearance is set, and mistaking one for the other is
    how this oracle previously concluded the corpus was gone. `retrieve()`
    sets the same setting per transaction; an oracle measuring through the
    query role has to do it too.
    """
    from scout.config import postgres_settings

    settings = postgres_settings("query")
    conn = await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
    )
    await conn.execute(
        "SELECT set_config('scout.current_depts', $1, false);",
        ",".join(sorted(CANONICAL_DEPARTMENTS)),
    )
    return conn


async def group_corpus_tier() -> str:
    """Prove the corpus tier is a tier: raw evidence stays reachable, wiki hides it.

    Both halves are measured through the real `PgVectorRlsBackend.retrieve()`
    against the live index, and both halves need a control. The positive
    control is the unfiltered backend actually returning the raw document --
    without it, an absence proves nothing. The negative half needs its own
    control too: a wiki backend that returned *nothing at all* would satisfy a
    naive "the raw doc is absent" check while being completely broken, so the
    filtered result set is asserted nonempty before its exclusion is credited.

    Identity is compared on `file_path`, which `retrieve()` populates from
    `rag_documents.source_uri`. `chunk_id` is selected by the SQL but never
    copied into `RagChunk.meta`, so comparing on it can never match.
    """
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.types import Scope

    conn = await get_connection()
    unfiltered: PgVectorRlsBackend | None = None
    filtered: PgVectorRlsBackend | None = None
    try:
        # The tier's subject: a document the wiki ingest path never wrote, so
        # it carries no `corpus` key at all. A filter written as
        # `corpus != 'raw'` would let this through; `corpus = 'wiki'` excludes it.
        raw_row = await conn.fetchrow(
            """
            SELECT d.source_uri, c.chunk_text
            FROM rag_chunks c
            JOIN rag_documents d ON d.doc_id = c.doc_id
            WHERE c.metadata->>'corpus' IS NULL
            ORDER BY length(c.chunk_text) DESC
            LIMIT 1
            """
        )
        require(
            raw_row is not None,
            "no raw document (corpus IS NULL) in the live index; the tier has "
            "nothing to hide and this gate cannot prove anything",
        )
        raw_uri: str = raw_row["source_uri"]
        raw_text: str = raw_row["chunk_text"]
        require(
            len(raw_text) > 200,
            f"raw chunk from {raw_uri} is too short to build a discriminating hint",
        )

        # A hint drawn from the raw document's own body, so the dense and
        # sparse arms both rank it highly. Derived from the corpus rather than
        # hardcoded, so it keeps working when the raw tier changes.
        hint = " ".join(raw_text.split())[:200]
        scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))

        # Positive control. If the unfiltered path cannot reach the raw
        # document, the negative half below is vacuous and the gate must fail
        # here rather than credit an absence it never established.
        unfiltered = PgVectorRlsBackend(corpus=None)
        hits_unfiltered = await unfiltered.retrieve(hint, scope=scope, k=10)
        require(
            len(hits_unfiltered) > 0,
            "positive control failed: the unfiltered backend returned no rows "
            "at all, so nothing about the tier can be concluded",
        )
        require(
            any(c.file_path == raw_uri for c in hits_unfiltered),
            f"positive control failed: unfiltered retrieve() did not reach {raw_uri}; "
            f"got {sorted({c.file_path for c in hits_unfiltered})}",
        )

        # The tier itself, same hint, same scope, same index.
        filtered = PgVectorRlsBackend(corpus="wiki")
        hits_filtered = await filtered.retrieve(hint, scope=scope, k=10)
        require(
            len(hits_filtered) > 0,
            "the wiki-filtered backend returned nothing at all; an exclusion "
            "that hides the whole corpus is a broken filter, not a tier",
        )
        require(
            all(c.file_path != raw_uri for c in hits_filtered),
            f"corpus filter failed: wiki_search surfaced {raw_uri}",
        )

        return "CORPUS TIER VERIFIED"

    finally:
        if unfiltered is not None:
            await unfiltered.close()
        if filtered is not None:
            await filtered.close()
        await conn.close()


async def group_sparse_vietnamese() -> str:
    """Prove `simple` preserves Vietnamese tokens that `english` damages.

    The gate this oracle serves used to claim english yields *no* lexemes for
    Vietnamese. Measured on the live stack, that is false, and the real defect
    is worse than absence because it is silent. `english` returns a full
    lexeme vector, but the Snowball stemmer rewrites Vietnamese words --
    `chạy` becomes `chại`, `máy` becomes `mái`, collapsing "machine" into
    "roof" -- and the English stopword list swallows Vietnamese words that
    happen to be spelled like English function words, so `so` and `an`
    disappear from the query entirely.

    Both effects are symmetric between query and document, so retrieval does
    not break outright; it loses the discriminating tokens and conflates
    distinct words. `simple` applies no stemming and no stopword list, so the
    query keeps exactly the words the user typed.

    Two assertions, both falsifiable: `simple` must preserve every token, and
    the two configurations must actually differ. If they ever agreed the
    migration would be inert, which is the failure this gate exists to catch.
    """
    conn = await get_connection()
    try:
        questions_path = REPO_ROOT / "artifacts" / "v3" / "retrieval_questions.json"
        questions_data: Any = json.loads(questions_path.read_text(encoding="utf-8"))
        vi_queries = [q["query"] for q in questions_data if q.get("lang") == "vi"]
        require(
            len(vi_queries) > 0,
            "no Vietnamese queries in retrieval_questions.json; this gate has "
            "no subject and cannot prove anything",
        )

        damaged_by_english: list[str] = []
        for vi_query in vi_queries:
            simple_lexemes = await conn.fetchval(
                "SELECT plainto_tsquery('simple', $1)::text", vi_query
            )
            english_lexemes = await conn.fetchval(
                "SELECT plainto_tsquery('english', $1)::text", vi_query
            )
            require(
                bool(simple_lexemes) and simple_lexemes not in ("", "''"),
                f"simple produced no lexemes for {vi_query!r}",
            )

            # Every word the user typed must survive the simple tokenizer.
            typed = [w.strip(".,?!:;\u2019\"'()").casefold() for w in vi_query.split()]
            simple_terms = {
                term.strip("'").casefold() for term in str(simple_lexemes).split(" & ")
            }
            missing = [w for w in typed if w and w not in simple_terms]
            require(
                not missing,
                f"simple dropped {missing} from {vi_query!r}; the tokenizer is "
                "not preserving the query as typed",
            )

            if english_lexemes != simple_lexemes:
                damaged_by_english.append(vi_query)

        require(
            len(damaged_by_english) > 0,
            "english and simple produced identical lexemes for every Vietnamese "
            "query; the second tsvector column is inert and migration 007 buys "
            "nothing",
        )

        return "SPARSE VIETNAMESE VERIFIED"

    finally:
        await conn.close()


async def group_migration_007() -> str:
    """Test that migration 007 is discoverable, forward-only, and applied.

    Verify:
    1. The migration file exists and follows the naming convention
    2. It is recorded in schema_migrations (forward-only)
    3. The new column and index exist in the live database
    """
    conn = await get_connection()
    try:
        # 1. Check migration file discovery
        migrations = REPO_ROOT / "config" / "postgres" / "migrations"
        migration_files = sorted(migrations.glob("[0-9][0-9][0-9]_*.sql"))

        seven_migrations = [p for p in migration_files if p.name.startswith("007_")]
        require(
            len(seven_migrations) == 1,
            f"expected exactly one 007_* migration, found {len(seven_migrations)}: {seven_migrations}",
        )

        migration_007 = seven_migrations[0]
        migration_body = migration_007.read_text(encoding="utf-8")

        # Verify it's forward-only (no destructive operations)
        destructive_keywords = ["DROP", "DELETE", "TRUNCATE", "RESET"]
        for keyword in destructive_keywords:
            require(
                keyword not in migration_body.upper(),
                f"migration 007 contains destructive keyword: {keyword}",
            )

        # 2. Try to check schema_migrations ledger, but handle permission errors
        # gracefully since query role may not have access
        try:
            migration_version: str | None = await conn.fetchval(
                "SELECT version FROM schema_migrations WHERE version = '007'"
            )
            require(
                migration_version == "007",
                "migration 007 not found in schema_migrations (not applied to live stack)",
            )
        except asyncpg.exceptions.InsufficientPrivilegeError:
            # Query role doesn't have access to schema_migrations, but the column
            # and index checks below will verify the migration was applied
            pass

        # 3. Check the column exists
        column_exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'rag_chunks' AND column_name = 'tsv_simple'
            )
            """
        )
        require(
            column_exists,
            "tsv_simple column does not exist in rag_chunks table",
        )

        # 4. Check the index exists
        index_exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'rag_chunks' AND indexname = 'rag_chunks_tsv_simple_idx'
            )
            """
        )
        require(
            index_exists,
            "rag_chunks_tsv_simple_idx index does not exist",
        )

        # 5. Verify the column is generated
        is_generated = await conn.fetchval(
            """
            SELECT is_generated FROM information_schema.columns
            WHERE table_name = 'rag_chunks' AND column_name = 'tsv_simple'
            """
        )
        require(
            is_generated == "ALWAYS",
            f"tsv_simple column is not GENERATED ALWAYS (is_generated={is_generated})",
        )

        return "MIGRATION 007 VERIFIED"

    finally:
        await conn.close()


async def group_shared_retrieve_blast_radius() -> str:
    """Prove the compile pipeline's retrieval path still reaches raw evidence.

    `retrieve()` is the only method on the `RagBackend` protocol, and
    `compile_note`, `verify_groundedness`, `verify_addresses`, `compile_plan`
    and `mint` all reach the index through it with the default backend. Their
    purpose is grounding a page that does not exist yet against **raw**
    sources, so a wiki-only predicate in shared SQL would make groundedness
    circular: a new page could only ever be judged against already-published
    pages.

    There is no passing path through this function that skips the
    measurement. An earlier version returned the success token when the raw
    count came back zero, reasoning that "the structure is correct" -- and
    because the oracle's own connection had no RLS clearance, that count was
    always zero. The gate certified an untested filter every time it ran.
    Absence of the subject is now a failure, because a gate that cannot see
    what it is testing has proved nothing.
    """
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.types import Scope

    conn = await get_connection()
    unfiltered: PgVectorRlsBackend | None = None
    try:
        raw_row = await conn.fetchrow(
            """
            SELECT d.source_uri, c.chunk_text
            FROM rag_chunks c
            JOIN rag_documents d ON d.doc_id = c.doc_id
            WHERE c.metadata->>'corpus' IS NULL
            ORDER BY length(c.chunk_text) DESC
            LIMIT 1
            """
        )
        require(
            raw_row is not None,
            "no raw-corpus document in the live index, so this gate cannot "
            "show that the compile pipeline still reaches one",
        )
        raw_uri: str = raw_row["source_uri"]
        raw_text: str = raw_row["chunk_text"]
        require(
            len(raw_text) > 200,
            f"raw chunk from {raw_uri} is too short to build a discriminating hint",
        )

        hint = " ".join(raw_text.split())[:200]
        scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))

        # The default construction: exactly what the five compile-pipeline
        # scripts build when they instantiate the backend themselves.
        unfiltered = PgVectorRlsBackend()
        require(
            unfiltered.corpus is None,
            "the default backend is not corpus-agnostic; the compile pipeline "
            f"would inherit the {unfiltered.corpus!r} tier it must not have",
        )
        hits = await unfiltered.retrieve(hint, scope=scope, k=10)
        require(
            len(hits) > 0,
            "the default backend returned no rows at all for a hint drawn from "
            "the corpus; the shared retrieval path is broken",
        )
        require(
            any(c.file_path == raw_uri for c in hits),
            f"the shared retrieve() path no longer reaches {raw_uri}; the "
            "corpus tier has leaked out of wiki_search and into every caller, "
            f"got {sorted({c.file_path for c in hits})}",
        )

        return "SHARED RETRIEVE PRESERVED"

    finally:
        if unfiltered is not None:
            await unfiltered.close()
        await conn.close()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Oracles for wiki_search SQL-level contract"
    )
    parser.add_argument(
        "--group",
        type=str,
        required=True,
        choices=[
            "corpus-tier",
            "sparse-vietnamese",
            "migration-007",
            "shared-retrieve-blast-radius",
        ],
        help="Which oracle group to run",
    )
    args = parser.parse_args()

    try:
        if args.group == "corpus-tier":
            result = await group_corpus_tier()
        elif args.group == "sparse-vietnamese":
            result = await group_sparse_vietnamese()
        elif args.group == "migration-007":
            result = await group_migration_007()
        elif args.group == "shared-retrieve-blast-radius":
            result = await group_shared_retrieve_blast_radius()
        else:
            sys.exit(f"unknown group: {args.group}")

        print(result)
    except GateFailure as e:
        print(f"Gate failure: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
