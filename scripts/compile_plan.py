#!/usr/bin/env python3
"""Compile an approved article plan into N grounded, cross-linked wiki pages.

This is a saga, not a transaction. Every page is prepared and judged in a
staging directory first, and only a fully prepared batch is published. If
publication fails part-way, the manifest names exactly what reached `wiki/`
and the compensation removes it. That makes ordinary failures clean; it does
not make a kill between two renames atomic, and this module does not pretend
otherwise.

Order of operations, and why:
  1. pre-flight mint every article  — fail before spending any model call
  2. collect every slug             — so article 1 may link to article 5
  3. prepare + judge into staging   — nothing touches wiki/ yet
  4. publish, then index once       — with a manifest to compensate from
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout import vault  # noqa: E402
from scout.backends.pgvector import PgVectorRlsBackend  # noqa: E402
from scout.cli.tasks import (  # noqa: E402
    TaskState,
    cancel_requested,
    clear_cancel,
    describe_plan_drift,
    finish_run,
    heartbeat,
    plan_fingerprints,
    read_run_marker,
    write_run_marker,
)
from scout.types import RagBackend  # noqa: E402
from scripts.compile_note import (  # noqa: E402
    CATEGORY_PLURALS,
    CompileNoteError,
    PreparedPage,
    _atomic_write,
    _close_backend,
    _regenerate_index,
    _restore,
    _snapshot,
    prepare_page,
)
from scripts.mint import MintStatus, mint_address  # noqa: E402

MAX_ARTICLES = 100


class CompilePlanError(RuntimeError):
    """Raised when a batch cannot be compiled safely."""


class CompilePlanCancelled(CompilePlanError):
    """Raised when a batch stopped because it was asked to.

    A subclass, so every existing handler treats it as the semantic outcome it
    is rather than as an infrastructure failure — but distinguishable, so the
    run is not then recorded as `failed`. It stopped because somebody said to.
    """


@dataclass(frozen=True, slots=True)
class PlannedArticle:
    title: str
    loc: str
    slug: str
    category: str
    department: str
    section: str = ""
    links: tuple[str, ...] = ()


def load_plan(plan_path: Path) -> tuple[str, list[PlannedArticle]]:
    """Read and validate an article plan, or raise."""
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompilePlanError(f"Could not read plan {plan_path}") from exc
    if not isinstance(payload, dict):
        raise CompilePlanError("Plan must be a JSON object")
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise CompilePlanError("Plan must name a source path")
    raw_articles = payload.get("articles")
    if not isinstance(raw_articles, list) or not raw_articles:
        raise CompilePlanError("Plan must contain a nonempty articles list")
    if len(raw_articles) > MAX_ARTICLES:
        raise CompilePlanError(f"Plan exceeds {MAX_ARTICLES} articles")

    articles: list[PlannedArticle] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_articles):
        if not isinstance(entry, dict):
            raise CompilePlanError(f"Article {index} is not an object")
        try:
            article = PlannedArticle(
                title=str(entry["title"]).strip(),
                loc=str(entry["loc"]).strip(),
                slug=str(entry["slug"]).strip(),
                category=str(entry["category"]).strip(),
                department=str(entry["department"]).strip(),
                section=str(entry.get("section", "")).strip(),
                links=tuple(str(x) for x in entry.get("links", ())),
            )
        except KeyError as exc:
            raise CompilePlanError(f"Article {index} is missing {exc}") from exc
        if article.category not in vault.VALID_TYPES:
            raise CompilePlanError(f"{article.slug}: invalid category")
        if article.department not in vault.VALID_DEPARTMENTS:
            raise CompilePlanError(f"{article.slug}: invalid department")
        if not article.title or not article.loc or not article.slug:
            raise CompilePlanError(f"Article {index} has an empty required field")
        if article.slug in seen:
            raise CompilePlanError(f"Duplicate slug in plan: {article.slug}")
        seen.add(article.slug)
        articles.append(article)
    return source, articles


async def _preflight(
    backend: RagBackend, source: str, articles: list[PlannedArticle]
) -> list[tuple[PlannedArticle, bool, str]]:
    """Try to mint each article's address from its title alone.

    This is a *sufficient* check, not a necessary one: compilation also tries a
    model-generated hint, so an article that fails here may still mint later.
    It is run first because it costs only retrieval, and catching an unmintable
    article before N generations is the whole point.
    """
    results: list[tuple[PlannedArticle, bool, str]] = []
    for article in articles:
        try:
            outcome = await mint_address(
                backend=backend,
                path=source,
                candidate_hints=(article.title,),
                department=article.department,
                loc=article.loc,
            )
        except Exception as exc:  # noqa: BLE001 — reported per article, never fatal here
            results.append((article, False, f"{type(exc).__name__}: {exc}"))
            continue
        ok = outcome.status is MintStatus.MINTED and outcome.address is not None
        results.append((article, ok, outcome.status.name))
    return results


def run_preflight(
    source: str, articles: list[PlannedArticle]
) -> list[tuple[PlannedArticle, bool, str]]:
    """Synchronous wrapper that owns the backend lifetime."""

    async def _run() -> list[tuple[PlannedArticle, bool, str]]:
        backend = PgVectorRlsBackend()
        try:
            return await _preflight(backend, source, articles)
        finally:
            await _close_backend(backend)

    return asyncio.run(_run())


def staging_dir(plan_path: Path) -> Path:
    return plan_path.with_suffix(".staging")


def _staged_path(staging: Path, article: PlannedArticle) -> Path:
    return staging / f"{article.slug}.md"


def publish_batch(prepared: list[PreparedPage], *, dry_run: bool = False) -> list[Path]:
    """Publish a fully prepared batch, compensating for a partial publish.

    Compensation is idempotent: removing a page that is already gone is a
    no-op, so a retried compensation is safe.
    """
    if dry_run:
        return [page.path for page in prepared]
    index_path = REPO_ROOT / "wiki" / "index.md"
    index_before = _snapshot(index_path)
    published: list[tuple[Path, Any]] = []
    try:
        for page in prepared:
            before = _snapshot(page.path)
            _atomic_write(page.path, page.content.encode("utf-8"))
            published.append((page.path, before))
        _regenerate_index()
    except BaseException as exc:
        failures: list[str] = []
        for path, before in reversed(published):
            try:
                _restore(path, before)
            except OSError as rollback_error:
                failures.append(f"{path}: {rollback_error}")
        try:
            _restore(index_path, index_before)
        except OSError as rollback_error:
            failures.append(f"{index_path}: {rollback_error}")
        if failures:
            raise CompilePlanError(
                "Batch failed AND compensation failed — these need human "
                "attention: " + "; ".join(failures)
            ) from exc
        if isinstance(exc, CompileNoteError | CompilePlanError):
            raise
        raise CompilePlanError("Batch publication failed and was rolled back") from exc
    return [page.path for page in prepared]


def _guard_resume(
    plan_path: Path,
    staging: Path,
    articles: list[PlannedArticle],
    fingerprint: str,
    article_fingerprints: dict[str, str],
) -> None:
    """Refuse to resume onto pages generated from a different plan.

    The plan file is hand-editable by design, and nothing prunes staging when it
    changes. Before this guard, editing a plan and re-running reused the pages
    generated from the *old* one and reported success — the batch published prose
    that no version of the plan had ever asked for.
    """
    staged_any = any(_staged_path(staging, article).exists() for article in articles)
    if not staged_any:
        return

    marker = read_run_marker(plan_path)
    recorded = str((marker or {}).get("plan_fingerprint") or "")
    if not recorded:
        # A run started before fingerprints existed. Warn rather than refuse: a
        # batch already in flight must not be stranded by an upgrade.
        print(
            "  WARNING: this staging directory predates plan fingerprinting, so "
            "it cannot be checked against the current plan. Pass --no-resume to "
            "regenerate from scratch if the plan has changed since.",
            file=sys.stderr,
        )
        return

    if recorded == fingerprint:
        return

    drift = describe_plan_drift(
        {
            str(k): str(v)
            for k, v in (marker or {}).get("article_fingerprints", {}).items()
        },
        article_fingerprints,
    )
    raise CompilePlanError(
        f"The plan changed after this batch staged pages: {drift}. Resuming "
        f"would publish pages generated from the previous plan. Re-run with "
        f"--no-resume to regenerate, or restore the plan this batch started from."
    )


def compile_plan(
    plan_path: Path,
    *,
    skip_groundedness: bool = False,
    dry_run: bool = False,
    resume: bool = True,
    allow_uncertain: bool = False,
) -> list[Path]:
    """Compile every article in an approved plan. Writes nothing until all pass."""
    source, articles = load_plan(plan_path)

    staging = staging_dir(plan_path)
    staging.mkdir(parents=True, exist_ok=True)

    fingerprint, article_fingerprints = plan_fingerprints(plan_path)
    if resume:
        _guard_resume(plan_path, staging, articles, fingerprint, article_fingerprints)

    # Record who is working and on which version of the plan **before** the
    # pre-flight, not after. A detached batch whose pre-flight refuses has
    # nobody watching it, and with no marker to record the refusal against,
    # `compile-status` could only report `not_started` — the most misleading
    # answer available, because it says nothing happened when something did.
    write_run_marker(
        plan_path,
        pid=os.getpid(),
        fingerprint=fingerprint,
        article_fingerprints=article_fingerprints,
    )
    # Starting is an intent to run. A request left over from a previous attempt
    # must not silently kill this one.
    clear_cancel(plan_path)

    preflight = run_preflight(source, articles)
    unmintable = [(a, why) for a, ok, why in preflight if not ok]
    for article, ok, why in preflight:
        print(
            f"  {'PASS' if ok else 'UNCERTAIN':10} {article.slug:45} {why}",
            file=sys.stderr,
        )
    if unmintable and not allow_uncertain:
        listed = ", ".join(a.slug for a, _why in unmintable)
        raise CompilePlanError(
            f"{len(unmintable)} article(s) could not mint from their title alone: "
            f"{listed}. Edit their titles or locs in the plan, or pass "
            "--allow-uncertain to let compilation try a model-generated hint."
        )

    # Every slug up front, so article 1 may legally link to article 5.
    all_slugs = tuple(article.slug for article in articles)

    prepared: list[PreparedPage] = []
    for index, article in enumerate(articles):
        # Between articles: staging is consistent here, so stopping leaves every
        # page that was paid for on disk and nothing half-written.
        if cancel_requested(plan_path):
            clear_cancel(plan_path)
            detail = f"cancelled after {index} of {len(articles)} article(s)"
            finish_run(
                plan_path, state=TaskState.CANCELLED, detail=detail, exit_code=130
            )
            raise CompilePlanCancelled(
                f"{detail}; staged pages are kept — re-run to resume from them"
            )
        staged = _staged_path(staging, article)
        if resume and staged.exists():
            print(f"  resume: reusing staged {article.slug}", file=sys.stderr)
            heartbeat(plan_path)
            content = staged.read_text(encoding="utf-8")
            page = vault.parse_page(staged)
            target = (
                REPO_ROOT
                / "wiki"
                / CATEGORY_PLURALS[article.category]
                / f"{article.slug}.md"
            )
            prepared.append(PreparedPage(target, page.frontmatter, content))
            continue
        print(f"  compiling {article.slug} …", file=sys.stderr)
        page_result = prepare_page(
            source,
            article.title,
            article.category,
            department=article.department,
            loc=article.loc,
            wikilinks=article.links,
            skip_groundedness=skip_groundedness,
            extra_known_slugs=all_slugs,
        )
        # Checkpoint immediately: this page cost two generations and a judge.
        _atomic_write(staged, page_result.content.encode("utf-8"))
        prepared.append(page_result)
        # One small write per article. It is what lets a `running` claim expire,
        # rather than resting forever on a pid that may have been reused.
        heartbeat(plan_path)

    published = publish_batch(prepared, dry_run=dry_run)
    if not dry_run:
        shutil.rmtree(staging, ignore_errors=True)
    return published


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-groundedness", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--allow-uncertain", action="store_true")
    args = parser.parse_args(argv)
    plan_path = Path(args.plan)
    try:
        pages = compile_plan(
            plan_path,
            skip_groundedness=args.skip_groundedness,
            dry_run=args.dry_run,
            resume=not args.no_resume,
            allow_uncertain=args.allow_uncertain,
        )
    except CompilePlanCancelled as exc:
        # Already recorded as `cancelled` at the boundary where it stopped.
        # Recording `failed` over it would misreport a deliberate stop.
        print(f"CANCELLED: {exc}", file=sys.stderr)
        return 130
    except (CompilePlanError, CompileNoteError) as exc:
        # Record the outcome where `compile-status` will find it. A batch that
        # ended by itself must not read as `stalled` forever, and an operator
        # polling a detached run has nothing else to read.
        finish_run(plan_path, state=TaskState.FAILED, detail=str(exc), exit_code=1)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        finish_run(
            plan_path,
            state=TaskState.CANCELLED,
            detail="interrupted at the terminal",
            exit_code=130,
        )
        print("ERROR: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        finish_run(
            plan_path,
            state=TaskState.FAILED,
            detail=f"{type(exc).__name__} during compilation",
            exit_code=1,
        )
        raise
    verb = "would publish" if args.dry_run else "published"
    print(f"{verb} {len(pages)} page(s)")
    for page in pages:
        print(f"  {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
