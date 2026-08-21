"""Tests for batch compilation: pre-flight, two-pass slugs, staged publish."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.compile_note import PreparedPage
from scripts.compile_plan import (
    CompilePlanError,
    compile_plan,
    load_plan,
    publish_batch,
)


def _plan_payload(*slugs: str) -> dict[str, object]:
    return {
        "source": "raw/papers/x.pdf",
        "articles": [
            {
                "section": str(i + 1),
                "title": slug.replace("-", " ").title(),
                "loc": f"p.{i + 1}",
                "slug": slug,
                "category": "concept",
                "department": "ai_eng",
            }
            for i, slug in enumerate(slugs)
        ],
    }


def _write_plan(tmp_path: Path, payload: object) -> Path:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(payload), encoding="utf-8")
    return plan


def test_load_plan_reads_articles(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    source, articles = load_plan(plan)
    assert source == "raw/papers/x.pdf"
    assert [a.slug for a in articles] == ["alpha", "beta"]
    assert articles[0].department == "ai_eng"


@pytest.mark.parametrize(
    "payload",
    [
        {"articles": []},
        {"source": "raw/x.pdf"},
        {"source": "raw/x.pdf", "articles": []},
        {"source": "", "articles": [{"title": "T"}]},
    ],
)
def test_malformed_plans_are_rejected(tmp_path: Path, payload: object) -> None:
    with pytest.raises(CompilePlanError):
        load_plan(_write_plan(tmp_path, payload))


def test_duplicate_slugs_in_a_plan_are_rejected(tmp_path: Path) -> None:
    payload = _plan_payload("alpha", "alpha")
    with pytest.raises(CompilePlanError, match="Duplicate slug"):
        load_plan(_write_plan(tmp_path, payload))


def test_invalid_department_or_category_is_rejected(tmp_path: Path) -> None:
    payload = _plan_payload("alpha")
    payload["articles"][0]["department"] = "all"  # type: ignore[index]
    with pytest.raises(CompilePlanError, match="department"):
        load_plan(_write_plan(tmp_path, payload))


def test_unmintable_article_stops_the_batch_before_any_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P4-batch: discovering this after articles 1-3 are on disk is the failure."""
    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    prepare = MagicMock()
    monkeypatch.setattr("scripts.compile_plan.prepare_page", prepare)
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [
            (articles[0], True, "MINTED"),
            (articles[1], False, "NO_CANDIDATE"),
        ],
    )

    with pytest.raises(CompilePlanError, match="beta"):
        compile_plan(plan)

    prepare.assert_not_called()


def test_every_slug_is_known_before_any_body_is_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P3 two-pass: article 1 must be able to link to article 5."""
    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta", "gamma"))
    seen: list[tuple[str, ...]] = []

    def _prepare(*_a: object, **kwargs: object) -> PreparedPage:
        seen.append(tuple(kwargs["extra_known_slugs"]))  # type: ignore[arg-type]
        return PreparedPage(tmp_path / "out.md", {}, "---\nx: 1\n---\n\nbody\n")

    monkeypatch.setattr("scripts.compile_plan.prepare_page", _prepare)
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])

    compile_plan(plan, resume=False)

    assert seen, "prepare_page should have been called"
    for slugs in seen:
        assert slugs == ("alpha", "beta", "gamma")


def test_failed_publish_compensates_and_leaves_no_pages(tmp_path: Path) -> None:
    """P5: a batch that fails half-way must leave nothing behind."""
    target_a = tmp_path / "a.md"
    target_b = tmp_path / "b.md"
    prepared = [
        PreparedPage(target_a, {}, "page a\n"),
        PreparedPage(target_b, {}, "page b\n"),
    ]

    import scripts.compile_plan as mod

    original = mod._regenerate_index
    mod._regenerate_index = lambda: (_ for _ in ()).throw(RuntimeError("index blew up"))
    try:
        with pytest.raises(CompilePlanError, match="rolled back"):
            publish_batch(prepared)
    finally:
        mod._regenerate_index = original

    assert not target_a.exists()
    assert not target_b.exists()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "a.md"
    paths = publish_batch([PreparedPage(target, {}, "body\n")], dry_run=True)
    assert paths == [target]
    assert not target.exists()
