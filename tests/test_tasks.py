"""Batch progress, computed from the filesystem rather than a second store."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from scout.cli.errors import CliError
from scout.cli.result import ExitCode
from scout.cli.tasks import (
    HEARTBEAT_TTL_SECONDS,
    POLL_INTERVAL_SECONDS,
    TaskState,
    describe_plan_drift,
    finish_run,
    heartbeat,
    plan_fingerprints,
    process_is_alive,
    read_run_marker,
    resolve_plan_path,
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


# ── T2.2: a handle means the same thing everywhere ────────────────────────


def test_a_handle_means_the_same_thing_from_another_directory(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """An agent may call `compile_plan` and `compile_status` from two places.

    Deriving the staging directory from a cwd-relative plan path made the second
    call report `not_started` for a batch that was happily in progress — the
    worst possible answer, because it invites starting the run a second time.
    The handle is absolute, so the second call names the same batch wherever it
    is made.
    """
    root = tmp_path / "repo"
    root.mkdir()
    plan = _plan(root, "a", "b", "c")
    _stage(plan, "a", "b")
    write_run_marker(plan, pid=os.getpid())

    monkeypatch.chdir(root)
    handle = status_for(Path("plan.json"), root=root).handle

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    status = status_for(Path(handle), root=root)

    assert status.state is TaskState.RUNNING
    assert (status.done, status.total) == (2, 3)
    assert status.handle == handle


def test_a_handle_is_absolute_so_it_survives_being_passed_around(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan(tmp_path, "a")
    monkeypatch.chdir(tmp_path)
    resolved = plan.resolve().as_posix()

    # However the caller writes it, one batch has one name.
    assert status_for(Path("plan.json"), root=tmp_path).handle == resolved
    assert status_for(plan, root=tmp_path).handle == resolved
    assert status_for(Path("./plan.json"), root=tmp_path).handle == resolved


def test_a_relative_path_follows_the_shell_not_the_root(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """`root` is a boundary, not an anchor.

    Anchoring to the root would make `cd docs && ... ../artifacts/plan.json`
    refuse a path that plainly is under the root, and would make
    `cd docs && ... plan.json` silently read a different file from the one the
    caller is looking at.
    """
    root = tmp_path / "repo"
    nested = root / "nested"
    nested.mkdir(parents=True)
    plan = _plan(nested, "a")
    monkeypatch.chdir(nested)

    assert status_for(Path("plan.json"), root=root).handle == plan.resolve().as_posix()
    assert resolve_plan_path(Path("../nested/plan.json"), root) == plan.resolve()


def test_a_plan_path_escaping_the_root_is_refused(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Also bounds where a tool call can write: staging follows the plan path."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)

    with pytest.raises(CliError) as caught:
        resolve_plan_path(Path("../../etc/plan.json"), root)

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION
    assert str(root) in (caught.value.hint or "")


def test_a_symlink_escaping_the_root_is_refused(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    monkeypatch.chdir(root)

    with pytest.raises(CliError):
        resolve_plan_path(Path("link/plan.json"), root)


def test_an_absolute_path_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(CliError):
        resolve_plan_path(Path("/etc/plan.json"), root)


def test_the_root_itself_is_not_a_plan(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        resolve_plan_path(tmp_path, tmp_path)


def test_a_plan_inside_the_root_is_accepted_however_it_is_written(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    (root / "nested").mkdir(parents=True)
    expected = (root / "nested" / "plan.json").resolve()
    monkeypatch.chdir(root)

    assert resolve_plan_path(Path("nested/plan.json"), root) == expected
    assert resolve_plan_path(root / "nested" / "plan.json", root) == expected
    assert resolve_plan_path(Path("nested/../nested/plan.json"), root) == expected


# ── T2.3 / T2.4: honest terminal states, staleness, and the plan guard ─────


def test_a_failed_run_reports_failed_not_stalled(tmp_path: Path) -> None:
    """A batch that ended by itself must stop reading as `stalled` forever.

    `stalled` says "re-run to resume". For a run that failed on a bad plan, that
    is advice to repeat the failure.
    """
    plan = _plan(tmp_path, "a", "b")
    _stage(plan, "a")
    write_run_marker(plan, pid=2**22)
    finish_run(plan, state=TaskState.FAILED, detail="preflight refused b", exit_code=1)

    status = status_for(plan)

    assert status.state is TaskState.FAILED
    assert status.is_terminal
    assert status.exit_code == 1
    assert "preflight refused b" in status.detail
    assert "1 article(s) unfinished" in status.detail


def test_a_cancelled_run_reports_cancelled(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "a", "b")
    _stage(plan, "a")
    write_run_marker(plan, pid=2**22)
    finish_run(plan, state=TaskState.CANCELLED, detail="stopped after a", exit_code=130)

    status = status_for(plan)
    assert status.state is TaskState.CANCELLED
    assert status.is_terminal


def test_a_running_claim_expires_when_no_article_finishes(tmp_path: Path) -> None:
    """A live pid says a process exists, not that it is working — and pids are reused."""
    plan = _plan(tmp_path, "a", "b")
    write_run_marker(plan, pid=os.getpid())
    marker = staging_dir(plan) / ".run.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["last_heartbeat"] = time.time() - (HEARTBEAT_TTL_SECONDS + 60)
    marker.write_text(json.dumps(payload), encoding="utf-8")

    status = status_for(plan)

    assert status.state is TaskState.STALLED
    assert "may be hung" in status.detail
    assert not status.is_terminal


def test_a_heartbeat_renews_the_running_claim(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "a", "b")
    write_run_marker(plan, pid=os.getpid())
    marker = staging_dir(plan) / ".run.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["last_heartbeat"] = time.time() - (HEARTBEAT_TTL_SECONDS + 60)
    marker.write_text(json.dumps(payload), encoding="utf-8")
    assert status_for(plan).state is TaskState.STALLED

    assert heartbeat(plan) is True
    assert status_for(plan).state is TaskState.RUNNING


def test_the_status_tells_a_caller_how_often_to_poll_and_when_to_stop(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, "a")
    write_run_marker(plan, pid=os.getpid())

    payload = status_for(plan).to_dict()

    assert payload["poll_interval"] == POLL_INTERVAL_SECONDS
    assert payload["ttl"] == HEARTBEAT_TTL_SECONDS
    assert payload["terminal"] is False
    assert payload["last_heartbeat"] is not None


def test_a_heartbeat_without_a_marker_is_a_no_op(tmp_path: Path) -> None:
    assert heartbeat(_plan(tmp_path, "a")) is False


# ── plan fingerprints ─────────────────────────────────────────────────────


def _rewrite(plan: Path, **changes: object) -> None:
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload.update(changes)
    plan.write_text(json.dumps(payload), encoding="utf-8")


def test_reformatting_a_plan_does_not_change_its_fingerprint(tmp_path: Path) -> None:
    """R3: whitespace and key order must not invalidate a running batch."""
    plan = _plan(tmp_path, "a", "b")
    before, _ = plan_fingerprints(plan)

    payload = json.loads(plan.read_text(encoding="utf-8"))
    plan.write_text(
        json.dumps(payload, indent=4, sort_keys=True) + "\n\n", encoding="utf-8"
    )

    assert plan_fingerprints(plan)[0] == before


def test_renumbering_sections_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    """`section` is a display ordinal and never reaches generation."""
    plan = _plan(tmp_path, "a", "b")
    before, _ = plan_fingerprints(plan)

    payload = json.loads(plan.read_text(encoding="utf-8"))
    for index, article in enumerate(payload["articles"]):
        article["section"] = f"9.{index}"
    plan.write_text(json.dumps(payload), encoding="utf-8")

    assert plan_fingerprints(plan)[0] == before


def test_editing_a_title_changes_that_article_and_the_whole_plan(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, "a", "b")
    before, per_article = plan_fingerprints(plan)

    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["articles"][0]["title"] = "Something Else Entirely"
    plan.write_text(json.dumps(payload), encoding="utf-8")

    after, after_per_article = plan_fingerprints(plan)
    assert after != before
    assert after_per_article["a"] != per_article["a"]
    assert after_per_article["b"] == per_article["b"]


def test_changing_the_source_changes_the_fingerprint(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "a")
    before, _ = plan_fingerprints(plan)
    _rewrite(plan, source="raw/papers/different.pdf")
    assert plan_fingerprints(plan)[0] != before


def test_an_unreadable_plan_yields_no_fingerprint(tmp_path: Path) -> None:
    """A fingerprint that could not be computed must never look like a match."""
    missing = tmp_path / "absent.json"
    assert plan_fingerprints(missing) == ("", {})

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert plan_fingerprints(broken) == ("", {})


def test_drift_is_described_by_slug_not_as_a_bare_mismatch(tmp_path: Path) -> None:
    """An operator cannot diff against a plan version they no longer have."""
    described = describe_plan_drift(
        {"a": "1", "b": "2", "gone": "3"}, {"a": "1", "b": "changed", "new": "4"}
    )
    assert "1 changed (b)" in described
    assert "1 added (new)" in described
    assert "1 removed (gone)" in described
