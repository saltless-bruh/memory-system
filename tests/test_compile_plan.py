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


# ── T2.3: staging is guarded against a plan edit ──────────────────────────


def _no_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Wire the batch so it never spends a model call, and record what it did."""
    generated: list[str] = []

    def _prepare(*args: object, **_kwargs: object) -> PreparedPage:
        generated.append(str(args[1]))
        return PreparedPage(tmp_path / "out.md", {}, "---\nx: 1\n---\n\nbody\n")

    monkeypatch.setattr("scripts.compile_plan.prepare_page", _prepare)
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])
    return generated


def test_resuming_after_a_plan_edit_refuses_and_reuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before this guard the batch published prose no plan had ever asked for.

    The plan file is hand-editable by design and nothing prunes staging when it
    changes, so a resume reused the pages generated from the *old* plan and
    reported success.
    """
    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    generated = _no_model(monkeypatch, tmp_path)

    # First run stages both articles, then fails at publication so staging stays.
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )
    with pytest.raises(CompilePlanError):
        compile_plan(plan)
    assert generated == ["Alpha", "Beta"]

    payload = _plan_payload("alpha", "beta")
    payload["articles"][1]["title"] = "Beta Rewritten"  # type: ignore[index]
    _write_plan(tmp_path, payload)

    generated.clear()
    with pytest.raises(CompilePlanError, match="changed"):
        compile_plan(plan)

    assert generated == [], "nothing may be regenerated on a refused resume"


def test_the_refusal_names_which_articles_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )
    with pytest.raises(CompilePlanError):
        compile_plan(plan)

    payload = _plan_payload("alpha", "gamma")
    _write_plan(tmp_path, payload)

    with pytest.raises(CompilePlanError) as caught:
        compile_plan(plan)

    message = str(caught.value)
    assert "gamma" in message and "beta" in message
    assert "--no-resume" in message


def test_no_resume_regenerates_after_an_edit_instead_of_refusing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal must have an escape hatch, or an edited plan is stuck."""
    plan = _write_plan(tmp_path, _plan_payload("alpha"))
    generated = _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )
    with pytest.raises(CompilePlanError):
        compile_plan(plan)

    payload = _plan_payload("alpha")
    payload["articles"][0]["title"] = "Alpha Rewritten"  # type: ignore[index]
    _write_plan(tmp_path, payload)

    generated.clear()
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])
    compile_plan(plan, resume=False)

    assert generated == ["Alpha Rewritten"]


def test_an_unchanged_plan_still_resumes_and_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing resume behaviour is the regression guard for this guard."""
    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    generated = _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )
    with pytest.raises(CompilePlanError):
        compile_plan(plan)
    assert generated == ["Alpha", "Beta"]

    generated.clear()
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])
    compile_plan(plan)

    assert generated == [], "a resume must not regenerate what is already staged"


def test_a_staging_directory_from_before_fingerprints_warns_rather_than_strands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run started before the upgrade must not be stranded by it."""
    from scout.cli.tasks import staging_dir

    plan = _write_plan(tmp_path, _plan_payload("alpha"))
    generated = _no_model(monkeypatch, tmp_path)

    # An old marker: a pid and a start time, and no fingerprint at all.
    staging = staging_dir(plan)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "alpha.md").write_text("---\nx: 1\n---\n\nold\n", encoding="utf-8")
    (staging / ".run.json").write_text(
        json.dumps({"pid": 1, "started_at": 0.0, "plan": str(plan)}), encoding="utf-8"
    )

    compile_plan(plan)

    assert generated == [], "the old staged page is still reused"
    assert "predates plan fingerprinting" in capsys.readouterr().err


def test_a_heartbeat_is_written_as_each_article_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.cli.tasks import read_run_marker

    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )
    with pytest.raises(CompilePlanError):
        compile_plan(plan)

    marker = read_run_marker(plan) or {}
    assert marker["last_heartbeat"] >= marker["started_at"]
    assert marker["plan_fingerprint"]
    assert set(marker["article_fingerprints"]) == {"alpha", "beta"}


def test_a_run_that_ends_non_zero_records_why_for_a_detached_poller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2.4: a backgrounded batch has nobody watching but `compile-status`.

    Without this the caller sees `stalled` — which advises resuming a run that
    would fail the same way again — instead of the reason it stopped.
    """
    from scout.cli.tasks import TaskState, read_run_marker, status_for
    from scripts.compile_plan import main

    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )

    assert main(["--plan", str(plan)]) == 1

    marker = read_run_marker(plan) or {}
    assert marker["state"] == "failed"
    assert marker["exit_code"] == 1
    assert "publish refused" in marker["detail"]

    status = status_for(plan)
    assert status.state is TaskState.FAILED
    assert status.is_terminal
    assert status.exit_code == 1


def test_an_unexpected_exception_is_recorded_before_it_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash must leave a readable outcome, and must still be a crash."""
    from scout.cli.tasks import read_run_marker
    from scripts.compile_plan import main

    plan = _write_plan(tmp_path, _plan_payload("alpha"))
    _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(MemoryError("out of memory")),
    )

    with pytest.raises(MemoryError):
        main(["--plan", str(plan)])

    marker = read_run_marker(plan) or {}
    assert marker["state"] == "failed"
    # The class, not the message: a traceback here may carry anything.
    assert marker["detail"] == "MemoryError during compilation"


# ── T2.4: cooperative cancellation ────────────────────────────────────────


def test_a_cancelled_batch_stops_at_an_article_boundary_and_keeps_its_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cooperative: it stops where staging is consistent, not mid-generation.

    Everything already paid for stays on disk, and nothing half-written is left
    behind — which is the whole reason the request is a file and not a signal.
    """
    from scout.cli.tasks import (
        TaskState,
        request_cancel,
        staging_dir,
        status_for,
    )
    from scripts.compile_plan import CompilePlanCancelled

    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta", "gamma"))
    generated: list[str] = []

    def _prepare(*args: object, **_kwargs: object) -> PreparedPage:
        title = str(args[1])
        generated.append(title)
        if title == "Alpha":
            # Somebody asks it to stop while article 1 is being generated.
            request_cancel(plan)
        return PreparedPage(tmp_path / "out.md", {}, "---\nx: 1\n---\n\nbody\n")

    monkeypatch.setattr("scripts.compile_plan.prepare_page", _prepare)
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])

    with pytest.raises(CompilePlanCancelled, match="1 of 3"):
        compile_plan(plan)

    # Article 1 finished; articles 2 and 3 never started.
    assert generated == ["Alpha"]
    staging = staging_dir(plan)
    assert (staging / "alpha.md").is_file(), "work already paid for must be kept"
    assert not (staging / "beta.md").exists()

    status = status_for(plan)
    assert status.state is TaskState.CANCELLED
    assert status.is_terminal
    assert status.exit_code == 130
    assert status.completed == ("alpha",)


def test_a_cancellation_is_consumed_so_a_re_run_is_not_killed_by_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.cli.tasks import cancel_requested, request_cancel

    plan = _write_plan(tmp_path, _plan_payload("alpha"))
    generated = _no_model(monkeypatch, tmp_path)
    request_cancel(plan)

    compile_plan(plan)

    assert cancel_requested(plan) is False
    assert generated == ["Alpha"], "a stale request must not kill a fresh run"


def test_a_cancelled_run_reports_cancelled_and_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main` must not record `failed` over a deliberate stop."""
    from scout.cli.tasks import read_run_marker, request_cancel
    from scripts.compile_plan import main

    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))

    def _prepare(*args: object, **_kwargs: object) -> PreparedPage:
        request_cancel(plan)
        return PreparedPage(tmp_path / "out.md", {}, "---\nx: 1\n---\n\nbody\n")

    monkeypatch.setattr("scripts.compile_plan.prepare_page", _prepare)
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )

    assert main(["--plan", str(plan)]) == 130
    marker = read_run_marker(plan) or {}
    assert marker["state"] == "cancelled"


def test_a_preflight_refusal_is_recorded_not_reported_as_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most misleading answer available is `not_started` for a run that ran.

    A detached batch whose pre-flight refuses has nobody watching it, so the
    refusal has to be readable through `compile-status`.
    """
    from scout.cli.tasks import TaskState, status_for
    from scripts.compile_plan import main

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

    assert main(["--plan", str(plan)]) == 1

    status = status_for(plan)
    assert status.state is TaskState.FAILED
    assert "beta" in status.detail
    prepare.assert_not_called()


def test_resuming_staged_articles_keeps_the_heartbeat_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long resume must not look hung while it is reading staged pages."""
    from scout.cli.tasks import read_run_marker

    plan = _write_plan(tmp_path, _plan_payload("alpha", "beta"))
    _no_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
    )
    with pytest.raises(CompilePlanError):
        compile_plan(plan)
    first = (read_run_marker(plan) or {})["last_heartbeat"]

    # A second run reuses both staged pages and generates nothing at all.
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])
    compile_plan(plan)

    # Staging is removed on success, so read the marker mid-flight instead:
    # re-stage and resume without publishing.
    with pytest.raises(CompilePlanError):
        _no_model(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "scripts.compile_plan.publish_batch",
            lambda p, **_k: (_ for _ in ()).throw(CompilePlanError("publish refused")),
        )
        compile_plan(plan)
    second = (read_run_marker(plan) or {})["last_heartbeat"]
    assert second >= first
