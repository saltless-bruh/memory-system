"""Regression tests for the compile lane: CLI launcher, task markers, publish.

Each test names the audit item it pins (medium-briefs, 2026-09-26). They are
kept in one module because the lane's defects cross the CLI, the task state
machine and the two scripts, and a fix in one is only honest if the others
agree with it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "snp-agent" / "skills"


def _plan(root: Path, *slugs: str, name: str = "plan.json") -> Path:
    plan = root / name
    plan.write_text(
        json.dumps(
            {
                "source": "raw/reports/acme.md",
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
        ),
        encoding="utf-8",
    )
    return plan


class _Cfg:
    """The two members of `Config` the compile commands read."""

    def __init__(self, root: Path, values: dict[str, str] | None = None) -> None:
        self._root = root
        self._values = values or {}

    def require_repo(self) -> Path:
        return self._root

    def get(self, key: str) -> str | None:
        return self._values.get(key)


class _FakePopen:
    launched: list[list[str]] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        type(self).launched.append(list(argv))
        self.kwargs = kwargs
        self.pid = 4242


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> type[_FakePopen]:
    _FakePopen.launched = []
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    return _FakePopen


# ── compile-dry-run-background-bypasses-confirm ───────────────────────────


def test_a_background_dry_run_launches_a_dry_run_child(
    tmp_path: Path, popen: type[_FakePopen]
) -> None:
    """`--dry-run --background` without `--confirm` must never publish.

    The confirmation guard exempts a dry run, so the child it launches has to
    be a dry run too — otherwise the one flag that waived confirmation is the
    one flag the child never hears about.
    """
    from scout.cli.commands.compile import compile_plan

    plan = _plan(tmp_path, "alpha")
    (tmp_path / "wiki").mkdir()

    result = compile_plan(
        str(plan), dry_run=True, background=True, config=_Cfg(tmp_path)
    )

    assert result.exit_code == 0
    assert len(popen.launched) == 1
    assert "--dry-run" in popen.launched[0]


def test_a_background_run_forwards_no_resume(
    tmp_path: Path, popen: type[_FakePopen]
) -> None:
    """The same defect class: a flag the caller set, silently dropped."""
    from scout.cli.commands.compile import compile_plan

    plan = _plan(tmp_path, "alpha")
    (tmp_path / "wiki").mkdir()

    compile_plan(
        str(plan),
        confirm=True,
        background=True,
        no_resume=True,
        config=_Cfg(tmp_path),
    )

    assert "--no-resume" in popen.launched[0]


# ── compile-cancel-refuses-hung ───────────────────────────────────────────


def test_cancelling_a_hung_batch_with_a_live_process_writes_the_request(
    tmp_path: Path,
) -> None:
    """A live pid with a stale heartbeat is the batch most in need of stopping.

    Refusing with "no process is working on this batch" contradicted the status
    detail ("process N is alive") and left a hung batch free to wake and publish.
    """
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.tasks import (
        HEARTBEAT_TTL_SECONDS,
        cancel_requested,
        staging_dir,
        write_run_marker,
    )

    plan = _plan(tmp_path, "a", "b")
    write_run_marker(plan, pid=os.getpid())
    marker = staging_dir(plan) / ".run.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["last_heartbeat"] = time.time() - HEARTBEAT_TTL_SECONDS - 60
    marker.write_text(json.dumps(payload), encoding="utf-8")

    result = compile_cancel(str(plan), config=_Cfg(tmp_path))

    assert result.exit_code == 0
    assert result.data["status"] == "cancelling"
    assert result.data["state"] == "stalled"
    assert cancel_requested(plan) is True
    assert "no process is working" not in result.summary
    assert str(os.getpid()) in result.summary


# ── tasks-marker-robustness ───────────────────────────────────────────────


@pytest.mark.parametrize("payload", [[1, 2], "a plan", 7])
def test_a_plan_that_is_not_an_object_reads_unknown(
    tmp_path: Path, payload: object
) -> None:
    from scout.cli.tasks import TaskState, status_for

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(payload), encoding="utf-8")

    status = status_for(plan)

    assert status.state is TaskState.UNKNOWN
    assert "plan could not be read" in status.detail


@pytest.mark.parametrize(
    "field,value", [("pid", "abc"), ("started_at", "noon"), ("last_heartbeat", [])]
)
def test_a_hand_edited_marker_reads_unknown_instead_of_crashing(
    tmp_path: Path, field: str, value: object
) -> None:
    from scout.cli.tasks import TaskState, staging_dir, status_for, write_run_marker

    plan = _plan(tmp_path, "a")
    write_run_marker(plan, pid=os.getpid())
    marker = staging_dir(plan) / ".run.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload[field] = value
    marker.write_text(json.dumps(payload), encoding="utf-8")

    status = status_for(plan)

    assert status.state is TaskState.UNKNOWN
    assert "run marker" in status.detail


def test_a_marker_that_is_not_json_reads_unknown_not_not_started(
    tmp_path: Path,
) -> None:
    """`not_started` for a batch that left a marker is the answer that invites
    starting it a second time."""
    from scout.cli.tasks import TaskState, staging_dir, status_for

    plan = _plan(tmp_path, "a")
    staging_dir(plan).mkdir()
    (staging_dir(plan) / ".run.json").write_text('{"pid": 12', encoding="utf-8")

    status = status_for(plan)

    assert status.state is TaskState.UNKNOWN
    assert "run marker" in status.detail


def test_a_failed_marker_write_leaves_the_previous_marker_readable(
    tmp_path: Path,
) -> None:
    """A torn write must never be what a concurrent poller reads.

    The fault is real rather than mocked: a child process runs `heartbeat`
    under a file-size limit smaller than the marker, the way a full disk would
    stop it part-way. Whatever primitive the write uses, the marker a poller
    reads afterwards must still be the previous, whole one.
    """
    import subprocess
    import sys

    from scout.cli.tasks import read_run_marker, write_run_marker

    plan = _plan(tmp_path, "a")
    write_run_marker(plan, pid=os.getpid())
    before = read_run_marker(plan)

    script = (
        "import resource, signal, sys\n"
        "from pathlib import Path\n"
        "from scout.cli.tasks import heartbeat\n"
        "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
        "resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64))\n"
        "try:\n"
        "    heartbeat(Path(sys.argv[1]))\n"
        "except OSError:\n"
        "    sys.exit(3)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script, str(plan)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert child.returncode == 3, child.stderr
    assert read_run_marker(plan) == before
    assert [p.name for p in plan.with_suffix(".staging").iterdir()] == [".run.json"]


def test_a_second_run_refuses_while_another_live_process_holds_the_batch(
    tmp_path: Path,
) -> None:
    """Two writers on one staging directory must collide detectably."""
    from scout.cli.tasks import BatchAlreadyRunning, read_run_marker, write_run_marker

    plan = _plan(tmp_path, "a")
    other = os.getppid()  # alive for as long as this test runs
    write_run_marker(plan, pid=other)

    with pytest.raises(BatchAlreadyRunning, match=str(other)):
        write_run_marker(plan, pid=os.getpid())

    assert (read_run_marker(plan) or {}).get("pid") == other


def test_a_finished_or_dead_holder_does_not_block_a_new_run(tmp_path: Path) -> None:
    """The control: only a batch that reads `running` is protected."""
    from scout.cli.tasks import TaskState, finish_run, read_run_marker, write_run_marker

    dead = _plan(tmp_path, "a", name="dead.json")
    write_run_marker(dead, pid=2**22)
    write_run_marker(dead, pid=os.getpid())
    assert (read_run_marker(dead) or {}).get("pid") == os.getpid()

    finished = _plan(tmp_path, "a", name="finished.json")
    write_run_marker(finished, pid=os.getppid())
    finish_run(finished, state=TaskState.FAILED, detail="x", exit_code=1)
    write_run_marker(finished, pid=os.getpid())
    assert (read_run_marker(finished) or {}).get("pid") == os.getpid()


def test_a_long_preflight_does_not_make_a_working_batch_read_stalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-flight of a large plan can outlast the TTL on its own."""
    from scout.cli import tasks
    from scout.cli.tasks import TaskState, status_for
    from scripts.compile_note import PreparedPage
    from scripts.compile_plan import compile_plan

    clock = [time.time()]
    monkeypatch.setattr(tasks.time, "time", lambda: clock[0])

    def _slow_preflight(_source: str, articles: list[Any]) -> list[Any]:
        clock[0] += tasks.HEARTBEAT_TTL_SECONDS + 100
        return [(a, True, "MINTED") for a in articles]

    observed: list[TaskState] = []

    def _prepare(*_a: object, **_k: object) -> PreparedPage:
        observed.append(status_for(plan).state)
        return PreparedPage(tmp_path / "out.md", {}, "---\nx: 1\n---\n\nbody\n")

    plan = _plan(tmp_path, "alpha")
    monkeypatch.setattr("scripts.compile_plan.run_preflight", _slow_preflight)
    monkeypatch.setattr("scripts.compile_plan.prepare_page", _prepare)
    monkeypatch.setattr("scripts.compile_plan.publish_batch", lambda p, **_k: [])

    compile_plan(plan)

    assert observed == [TaskState.RUNNING]


def test_the_background_launcher_does_not_relaunch_a_published_batch(
    tmp_path: Path, popen: type[_FakePopen]
) -> None:
    """Without the vault, a batch whose staging was removed reads `not_started`."""
    from scout.cli.commands.compile import compile_plan

    plan = _plan(tmp_path, "alpha")
    concepts = tmp_path / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "alpha.md").write_text("published\n", encoding="utf-8")

    result = compile_plan(
        str(plan), confirm=True, background=True, config=_Cfg(tmp_path)
    )

    assert popen.launched == []
    assert result.data["state"] == "complete"


# ── compile-plan-resume-overwrites-target ─────────────────────────────────


def _resume_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    from scripts.compile_plan import staging_dir

    plan = _plan(tmp_path, "alpha")
    staged = staging_dir(plan) / "alpha.md"
    staged.parent.mkdir()
    staged.write_text(
        "---\ntype: concept\ntitle: Alpha\n---\n\nstaged draft\n", encoding="utf-8"
    )
    target = tmp_path / "wiki" / "concepts" / "alpha.md"
    target.parent.mkdir(parents=True)
    target.write_text("written by a human after staging\n", encoding="utf-8")
    monkeypatch.setattr("scripts.compile_plan.REPO_ROOT", tmp_path)
    monkeypatch.setattr("scripts.compile_note.REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )
    monkeypatch.setattr(
        "scripts.compile_plan._regenerate_index", lambda *_a, **_k: None
    )
    return plan, target


def test_resume_refuses_a_page_that_appeared_at_the_target_after_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.compile_plan import CompilePlanError, compile_plan

    plan, target = _resume_fixture(tmp_path, monkeypatch)

    with pytest.raises(CompilePlanError, match="already exists"):
        compile_plan(plan)

    assert target.read_text(encoding="utf-8") == "written by a human after staging\n"


def test_publish_refuses_to_overwrite_any_existing_target(tmp_path: Path) -> None:
    """The last line of defence, whatever path a page took to get here."""
    from scripts.compile_note import PreparedPage
    from scripts.compile_plan import CompilePlanError, publish_batch

    wiki = tmp_path / "wiki"
    fresh = wiki / "concepts" / "fresh.md"
    taken = wiki / "concepts" / "taken.md"
    taken.parent.mkdir(parents=True)
    taken.write_text("human\n", encoding="utf-8")

    with pytest.raises(CompilePlanError, match="already exists"):
        publish_batch(
            [PreparedPage(fresh, {}, "new\n"), PreparedPage(taken, {}, "new\n")],
            wiki_dir=wiki,
        )

    assert taken.read_text(encoding="utf-8") == "human\n"
    assert not fresh.exists(), "nothing is written when any target is taken"


# ── compile-note-repo-root-not-cfg ────────────────────────────────────────


def test_the_cli_hands_the_pinned_checkout_and_vault_to_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.cli.commands.compile import compile_plan

    captured: dict[str, Any] = {}

    def _fake(plan_path: Path, **kwargs: Any) -> list[Path]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr("scripts.compile_plan.compile_plan", _fake)
    plan = _plan(tmp_path, "alpha")
    (tmp_path / "vault").mkdir()

    compile_plan(str(plan), confirm=True, config=_Cfg(tmp_path, {"WIKI_DIR": "vault"}))

    assert captured["repo_root"] == tmp_path.resolve()
    assert captured["wiki_dir"] == (tmp_path / "vault").resolve()


def test_the_background_child_is_told_which_tree_to_write(
    tmp_path: Path, popen: type[_FakePopen]
) -> None:
    from scout.cli.commands.compile import compile_plan

    plan = _plan(tmp_path, "alpha")
    (tmp_path / "vault").mkdir()

    compile_plan(
        str(plan),
        confirm=True,
        background=True,
        config=_Cfg(tmp_path, {"WIKI_DIR": "vault"}),
    )

    argv = popen.launched[0]
    assert argv[argv.index("--repo-root") + 1] == str(tmp_path.resolve())
    assert argv[argv.index("--wiki-dir") + 1] == str((tmp_path / "vault").resolve())


def test_a_resumed_page_targets_the_vault_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.compile_note import PreparedPage
    from scripts.compile_plan import compile_plan, staging_dir

    plan = _plan(tmp_path, "alpha")
    staged = staging_dir(plan) / "alpha.md"
    staged.parent.mkdir()
    staged.write_text("---\ntype: concept\n---\n\nbody\n", encoding="utf-8")
    vault_dir = tmp_path / "elsewhere" / "wiki"
    published: list[PreparedPage] = []
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )
    monkeypatch.setattr(
        "scripts.compile_plan.publish_batch",
        lambda prepared, **_k: published.extend(prepared) or [],
    )

    compile_plan(plan, repo_root=tmp_path, wiki_dir=vault_dir)

    assert published[0].path == vault_dir / "concepts" / "alpha.md"


def test_prepare_page_reads_raw_and_wiki_from_the_checkout_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With REPO_ROOT left at the package, the given checkout must still win."""
    from scout.types import Address
    from scripts.compile_note import GeneratedMetadata, prepare_page
    from scripts.mint import MintResult, MintStatus
    from tests.test_compile_note import _wire_body_seams

    raw = tmp_path / "raw" / "reports"
    raw.mkdir(parents=True)
    (raw / "acme.md").write_text("# Acme\n\nSource facts.\n", encoding="utf-8")
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    branches: list[Any] = []

    def _branch(repo: Path) -> str:
        branches.append(repo)
        return "feature/wiki"

    async def _mint(*_a: object, path: str, department: str, loc: str, **_k: object):
        return MintResult(
            path=path,
            department=department,
            address=Address(path=path, hint="Acme source facts", loc=loc),
            status=MintStatus.MINTED,
            tried=(),
        )

    monkeypatch.setattr("scripts.compile_note._current_branch", _branch)
    monkeypatch.setattr(
        "scripts.compile_note.generate_model_data",
        lambda *_a: GeneratedMetadata(entities=("acme",), hint="Acme source facts"),
    )
    monkeypatch.setattr("scripts.compile_note.mint_address", _mint)
    monkeypatch.setattr("scripts.compile_note.PgVectorRlsBackend", MagicMock())
    _wire_body_seams(monkeypatch)

    prepared = prepare_page(
        "raw/reports/acme.md",
        "Acme Capability",
        "concept",
        department="blueteam",
        loc="Section Acme",
        repo_root=tmp_path,
        wiki_dir=wiki,
    )

    assert prepared.path == (wiki / "concepts" / "acme-capability.md").resolve()
    assert branches == [tmp_path]


# ── compile-plan-publish-dead (OWNER RULING: draft-only, honest refusal) ──


def _authored_vault(root: Path) -> Path:
    """A vault shaped like the reference one: no `summary:`, authored index."""
    wiki = root / "wiki"
    concepts = wiki / "concepts"
    concepts.mkdir(parents=True)
    for slug in ("one", "two", "three"):
        (concepts / f"{slug}.md").write_text(
            f"---\ntype: concept\ntitle: {slug.title()}\n---\n\n## TL;DR\n\nx\n",
            encoding="utf-8",
        )
    (wiki / "index.md").write_text(
        "# Index\n\n- [One](concepts/one.md) — written by hand\n", encoding="utf-8"
    )
    return wiki


def test_publishing_into_an_authored_vault_is_refused_as_draft_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.compile_note import CompileNoteError, PreparedPage, publish_page

    wiki = _authored_vault(tmp_path)
    index_before = (wiki / "index.md").read_bytes()
    monkeypatch.setattr("scripts.compile_note.REPO_ROOT", tmp_path)
    page = wiki / "concepts" / "new-page.md"
    content = "---\ntype: concept\ntitle: New Page\nsummary: s\n---\n\n## TL;DR\n\ns\n"

    with pytest.raises(CompileNoteError) as caught:
        publish_page(PreparedPage(page, {}, content))

    message = str(caught.value)
    assert "draft-only" in message
    assert "summary" in message, "the refusal names the guard's actual reason"
    assert not page.exists()
    assert (wiki / "index.md").read_bytes() == index_before


def test_a_refused_batch_publish_says_where_its_drafts_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.compile_note import PreparedPage
    from scripts.compile_plan import CompilePlanError, compile_plan, staging_dir

    wiki = _authored_vault(tmp_path)
    plan = _plan(tmp_path, "alpha")
    content = "---\ntype: concept\ntitle: Alpha\nsummary: s\n---\n\n## TL;DR\n\ns\n"
    monkeypatch.setattr(
        "scripts.compile_plan.run_preflight",
        lambda _s, articles: [(a, True, "MINTED") for a in articles],
    )
    monkeypatch.setattr(
        "scripts.compile_plan.prepare_page",
        lambda *_a, **_k: PreparedPage(wiki / "concepts" / "alpha.md", {}, content),
    )

    with pytest.raises(CompilePlanError) as caught:
        compile_plan(plan, repo_root=tmp_path, wiki_dir=wiki)

    assert "draft-only" in str(caught.value)
    assert str(staging_dir(plan)) in str(caught.value)
    assert (staging_dir(plan) / "alpha.md").is_file(), "the drafts are kept"
    assert not (wiki / "concepts" / "alpha.md").exists()


def _generated_vault(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A lint-clean vault whose index is generated: every page has `summary:`.

    Pages are rendered by the compiler itself, so they carry exactly the shape
    the lane produces. `scout.vault.RAW_DIR` is pointed at this tree's `raw/`
    because `gen_index.collect_lint` checks `sources[]` against it -- the
    package-path assumption this lane cannot fix from its own files.
    """
    from scout import vault

    (root / "raw").mkdir(parents=True)
    (root / "raw" / "a.md").write_text("# A\n\nfacts\n", encoding="utf-8")
    monkeypatch.setattr("scout.vault.RAW_DIR", root / "raw")
    wiki = root / "wiki"
    for entry in vault.REQUIRED_TREE:
        if not entry.endswith(".md"):
            (wiki / entry).mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    for name in ("archive", "log"):
        (wiki / f"{name}.md").write_text(_page(name.title()), encoding="utf-8")
    return wiki


def _page(title: str) -> str:
    from scripts.compile_note import GeneratedBody, GeneratedMetadata, _render_page

    _frontmatter, content = _render_page(
        title=title,
        category="concept",
        department="ai_eng",
        source_path="raw/a.md",
        source_loc="p.1",
        source_hint="facts",
        metadata=GeneratedMetadata(entities=("a",), hint="facts"),
        body=GeneratedBody(f"The {title.lower()} summary.", ("A claim.",)),
        wikilinks=(),
    )
    return content


def test_a_generated_index_vault_still_publishes_and_regenerates_its_own_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the lane works where the index guard allows it, and the
    index it regenerates is the one in the vault it was given."""
    from scripts.compile_note import PreparedPage, publish_page

    wiki = _generated_vault(tmp_path / "elsewhere", monkeypatch)
    page = wiki / "concepts" / "alpha.md"

    written = publish_page(PreparedPage(page, {}, _page("Alpha")), wiki_dir=wiki)

    assert written == page
    assert "The alpha summary." in (wiki / "index.md").read_text(encoding="utf-8")


# ── render-stdout-pollution-json (compile_note only) ──────────────────────


def test_publish_page_keeps_stdout_for_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.compile_note import PreparedPage, publish_page

    wiki = _generated_vault(tmp_path, monkeypatch)
    page = wiki / "concepts" / "a.md"

    publish_page(PreparedPage(page, {}, _page("A")), wiki_dir=wiki)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "a.md" in captured.err


# ── runner-skills-fail-outside-checkout ───────────────────────────────────


@pytest.mark.parametrize(
    "skill", sorted(p.parent.parent.name for p in PACKAGE.glob("*/scripts/_runner.py"))
)
def test_every_skill_that_needs_a_checkout_documents_how_to_name_one(
    skill: str,
) -> None:
    """The runner's only remedy outside a checkout must be in the skill itself."""
    text = (PACKAGE / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "SNP_REPO_ROOT" in text


def test_the_batch_skill_does_not_promise_publishing() -> None:
    """OWNER RULING 2026-09-26: the compile lane is draft-only for now."""
    text = (PACKAGE / "snp-compile-batch" / "SKILL.md").read_text(encoding="utf-8")
    assert "draft-only" in text
    assert "staging" in text
