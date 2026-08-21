"""Batch progress, computed from the filesystem rather than a second store."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scout.cli.tasks import (
    TaskState,
    process_is_alive,
    read_run_marker,
    staging_dir,
    status_for,
    write_run_marker,
)


def _plan(tmp_path: Path, *slugs: str) -> Path:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/papers/x.pdf",
                "articles": [
                    {
                        "section": str(i + 1),
                        "title": s.title(),
                        "loc": f"p.{i + 1}",
                        "slug": s,
                        "category": "concept",
                        "department": "ai_eng",
                    }
                    for i, s in enumerate(slugs)
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _stage(plan: Path, *slugs: str) -> None:
    staging = staging_dir(plan)
    staging.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        (staging / f"{slug}.md").write_text("page\n", encoding="utf-8")


def test_a_missing_plan_is_not_started(tmp_path: Path) -> None:
    status = status_for(tmp_path / "nope.json")
    assert status.state is TaskState.NOT_STARTED
    assert status.total == 0


def test_nothing_staged_is_not_started(tmp_path: Path) -> None:
    status = status_for(_plan(tmp_path, "a", "b"))
    assert status.state is TaskState.NOT_STARTED
    assert (status.total, status.done) == (2, 0)


def test_partial_progress_is_reported_per_article(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "a", "b", "c", "d")
    _stage(plan, "a", "b")
    write_run_marker(plan, pid=os.getpid())

    status = status_for(plan)

    assert status.state is TaskState.RUNNING
    assert (status.total, status.done) == (4, 2)
    assert status.completed == ("a", "b")
    assert status.pending == ("c", "d")


def test_a_dead_process_reports_stalled_not_running(tmp_path: Path) -> None:
    """A crashed compile must never look like one still working."""
    plan = _plan(tmp_path, "a", "b")
    _stage(plan, "a")
    # A pid that cannot be alive: this test's own pid is taken, so use a
    # deliberately impossible one.
    write_run_marker(plan, pid=2**22)

    status = status_for(plan)

    assert status.state is TaskState.STALLED
    assert "resume" in status.detail
    assert status.pending == ("b",)


def test_all_staged_but_unpublished_is_staged_not_complete(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "a", "b")
    _stage(plan, "a", "b")
    assert status_for(plan).state is TaskState.STAGED


def test_published_pages_make_the_batch_complete(tmp_path: Path) -> None:
    """A successful publish removes staging; the vault is then the evidence."""
    plan = _plan(tmp_path, "a", "b")
    wiki = tmp_path / "wiki" / "concepts"
    wiki.mkdir(parents=True)
    (wiki / "a.md").write_text("x", encoding="utf-8")
    (wiki / "b.md").write_text("x", encoding="utf-8")

    status = status_for(plan, wiki_dir=tmp_path / "wiki")

    assert status.state is TaskState.COMPLETE
    assert set(status.published) == {"a", "b"}


def test_the_handle_is_the_plan_path(tmp_path: Path) -> None:
    """Two runs against one plan collide detectably instead of racing."""
    plan = _plan(tmp_path, "a")
    assert status_for(plan).handle == plan.as_posix()


def test_run_marker_round_trips_and_survives_corruption(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "a")
    write_run_marker(plan, pid=1234)
    assert (read_run_marker(plan) or {})["pid"] == 1234

    (staging_dir(plan) / ".run.json").write_text("{not json", encoding="utf-8")
    assert read_run_marker(plan) is None


def test_process_is_alive_rejects_nonsense_pids() -> None:
    assert process_is_alive(os.getpid()) is True
    assert process_is_alive(0) is False
    assert process_is_alive(-1) is False


def test_a_complete_batch_reports_done_equal_to_total(tmp_path: Path) -> None:
    """`complete: 0/3` reads as nothing happened.

    A successful publish deletes the staging directory, so counting staged
    files alone under-reports a finished batch to zero.
    """
    plan = _plan(tmp_path, "a", "b", "c")
    wiki = tmp_path / "wiki" / "concepts"
    wiki.mkdir(parents=True)
    for slug in ("a", "b", "c"):
        (wiki / f"{slug}.md").write_text("x", encoding="utf-8")

    status = status_for(plan, wiki_dir=tmp_path / "wiki")

    assert status.state is TaskState.COMPLETE
    assert (status.done, status.total) == (3, 3)
