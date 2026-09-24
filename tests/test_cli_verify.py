"""Tests for the verification command family.

The distinction these exist to protect is part of the public result contract: a
**finding** (the vault has a problem — exit 1) is not a **failure** (the check
could not run — exit 2). Confusing them can authorize action on the strength of
a check that never ran.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.verify import (  # noqa: E402
    DEFAULT_STAGES,
    check,
    verify_secrets,
    verify_vault,
)
from scout.cli.config import Config, resolve  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import CommandResult, ErrorKind, ExitCode  # noqa: E402


def _cfg(repo: Path | None = REPO_ROOT) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, values={}, repo_root=repo)


def _stage(name: str, result: CommandResult) -> tuple[str, object]:
    return (name, lambda **_: result)


# ── outcome vs failure ───────────────────────────────────────────────────────


def _vault_copy(tmp_path: Path) -> Path:
    """A real, self-contained checkout outside the installed package.

    These tests used to monkeypatch `_pages_and_lint` -- the function that held
    register #59 -- so all four passed identically whichever tree was linted, and
    the whole suite passed with its body deleted. They now lint a genuine tree at
    `tmp_path`, which is the only way the resolution can be asserted at all.
    """
    import shutil

    shutil.copytree(REPO_ROOT / "wiki", tmp_path / "wiki")
    return tmp_path


def test_a_clean_vault_outside_the_package_passes(tmp_path: Path) -> None:
    """The lint reads the caller's checkout, not the tree beside the source."""
    repo = _vault_copy(tmp_path)
    result = verify_vault(config=_cfg(repo))
    assert result.exit_code is ExitCode.SUCCESS
    assert result.data["index_current"] is True
    assert result.data["pages"] > 0


def test_two_trees_outside_the_package_give_two_verdicts(tmp_path: Path) -> None:
    """The regression test for #59: one input, one verdict; two inputs, two.

    A linter anchored to its own package returns the same answer for both, which
    is exactly what it did before this was fixed.
    """
    clean = _vault_copy(tmp_path / "clean")
    broken = _vault_copy(tmp_path / "broken")
    page = next((broken / "wiki").rglob("*.md"))
    page.write_text("---\nnot: a valid page\n---\n\nbody\n", encoding="utf-8")

    good = verify_vault(config=_cfg(clean))
    bad = verify_vault(config=_cfg(broken))
    assert good.exit_code is ExitCode.SUCCESS
    assert bad.exit_code is ExitCode.SEMANTIC_FAILURE
    assert good.data["pages"] != bad.data["pages"] or good.messages != bad.messages


def test_lint_errors_are_a_finding_not_a_failure(tmp_path: Path) -> None:
    """Exit 1 keeps healing permitted; exit 2 would forbid it."""
    repo = _vault_copy(tmp_path)
    page = next((repo / "wiki").rglob("*.md"))
    page.write_text("---\ntype: nonsense\n---\n\n# Broken\n", encoding="utf-8")

    result = verify_vault(config=_cfg(repo))
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.mutating_is_allowed, "a finding must permit caller follow-up"
    assert result.error is None, "a finding is not an error envelope"


def test_a_stale_index_is_a_finding(tmp_path: Path) -> None:
    repo = _vault_copy(tmp_path)
    index = repo / "wiki" / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "\ndrifted\n", encoding="utf-8"
    )

    result = verify_vault(config=_cfg(repo))
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["index_current"] is False
    assert any("STALE" in m for m in result.messages)


def test_a_symlinked_vault_is_refused_before_it_is_linted(tmp_path: Path) -> None:
    """A vault root that escapes the checkout is refused, and refusal is not a finding.

    Behaviour change from resolving through `_wiki_dir`: containment now catches
    an escaping root before `load_pages` ever sees the symlink, so this raises
    INPUT_VALIDATION rather than the INFRASTRUCTURE error the old path produced.
    The invariant that matters is unchanged and asserted here -- a check that
    could not run must not authorize a caller to act.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (repo / "wiki").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(CliError) as caught:
        verify_vault(config=_cfg(repo))
    assert caught.value.kind is ErrorKind.INPUT_VALIDATION
    assert caught.value.to_result().mutating_is_allowed is False
    assert caught.value.to_result().exit_code is not ExitCode.SEMANTIC_FAILURE


def test_an_unreadable_vault_is_an_infrastructure_failure(tmp_path: Path) -> None:
    """A wiki root that cannot be read is exit 2, never a lint finding."""
    from scout.cli.commands.verify import _pages_and_lint

    inner = tmp_path / "target"
    inner.mkdir()
    link = tmp_path / "wiki"
    link.symlink_to(inner, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _pages_and_lint(link)


# ── repository requirement ───────────────────────────────────────────────────


def test_local_commands_refuse_outside_a_checkout() -> None:
    for run in (verify_vault, verify_secrets):
        with pytest.raises(CliError) as caught:
            run(config=_cfg(repo=None))
        assert caught.value.kind is ErrorKind.INPUT_VALIDATION


# ── secrets ──────────────────────────────────────────────────────────────────


def test_secret_findings_are_reported_without_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Finding.format()` is redacted at the source; nothing here may undo that."""

    class _Finding:
        def format(self) -> str:
            return "[WORKTREE] a.py:1: OpenAI / LiteLLM token [REDACTED]"

    monkeypatch.setattr(
        "scripts.scan_secrets.scan_all_current", lambda _r: [_Finding()]
    )
    result = verify_secrets(config=_cfg())
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["count"] == 1
    assert "REDACTED" in result.data["findings"][0]


def test_history_scan_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slow all-refs walk is what CI runs, not what a developer waits on."""
    called: list[str] = []
    monkeypatch.setattr("scripts.scan_secrets.scan_all_current", lambda _r: [])
    monkeypatch.setattr(
        "scripts.scan_secrets.scan_git_history",
        lambda _r: called.append("history") or [],
    )
    verify_secrets(config=_cfg())
    assert called == []
    verify_secrets(history=True, config=_cfg())
    assert called == ["history"]


# ── the aggregate ────────────────────────────────────────────────────────────


def test_check_runs_cheapest_first() -> None:
    """Ordering is the point: a lint error should not cost a model call per page.

    `extraction` is a single SQL read against the index, so it sits ahead of
    the two stages that spend embedding and model calls.
    """
    assert [name for name, _ in DEFAULT_STAGES] == [
        "vault",
        "secrets",
        "extraction",
        "addresses",
        "groundedness",
    ]


def test_check_stops_at_the_first_failure() -> None:
    ran: list[str] = []

    def _ok(name: str) -> tuple[str, object]:
        return (name, lambda **_: ran.append(name) or CommandResult(summary=name))

    def _fail(name: str) -> tuple[str, object]:
        return (
            name,
            lambda **_: (
                ran.append(name)
                or CommandResult(
                    exit_code=ExitCode.SEMANTIC_FAILURE, summary=f"{name} failed"
                )
            ),
        )

    result = check(stages=[_ok("a"), _fail("b"), _ok("c")], config=_cfg())
    assert ran == ["a", "b"], "stages after a failure must not run"
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["failed_stage"] == "b"


def test_check_passes_when_every_stage_passes() -> None:
    result = check(
        stages=[
            _stage("a", CommandResult(summary="a")),
            _stage("b", CommandResult(summary="b")),
        ],
        config=_cfg(),
    )
    assert result.exit_code is ExitCode.SUCCESS
    assert result.data["failed_stage"] is None
    assert set(result.data["stages"]) == {"a", "b"}


def test_check_propagates_an_infrastructure_failure_with_its_envelope() -> None:
    """CI must see 2, not 1, or it would treat an outage as a vault problem."""
    failure = CommandResult.failure(ErrorKind.INFRASTRUCTURE, "database unreachable")
    result = check(stages=[_stage("addresses", failure)], config=_cfg())
    assert result.exit_code is ExitCode.INFRASTRUCTURE
    assert result.error is not None
    assert result.mutating_is_allowed is False


# ── declaration matches behaviour ────────────────────────────────────────────


def test_every_verify_command_declares_the_semantic_outcome() -> None:
    """`schema` must not promise an agent something the command cannot return."""
    from scout.cli import app  # noqa: F401  (registers the commands)
    from scout.cli.registry import REGISTRY

    for name in ("verify-vault", "verify-addresses", "verify-groundedness", "check"):
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert ExitCode.SEMANTIC_FAILURE in spec.outcomes, name
        assert spec.prerequisite is Prerequisite.LOCAL, name


def test_config_gating_reaches_the_verify_commands() -> None:
    """A LOCAL command may see database keys; that is what makes it LOCAL."""
    cfg = resolve(Prerequisite.LOCAL, environ={"POSTGRES_HOST": "db"}, start=REPO_ROOT)
    assert cfg.get("POSTGRES_HOST") == "db"
    assert cfg.get("GEMINI_API_KEY") is None


def test_compile_plan_refuses_to_write_without_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutation is approved at call time, not by installing the tool."""
    from scout.cli.commands.compile import compile_plan
    from scout.cli.errors import CliError
    from scout.cli.result import ErrorKind

    called: list[int] = []
    monkeypatch.setattr(
        "scripts.compile_plan.compile_plan", lambda *_a, **_k: called.append(1) or []
    )

    class _Cfg:
        # `require_repo` returns the checkout, and `compile_plan` now uses it:
        # the plan path is bounds-checked against that root before anything is
        # spent or written. Input validation runs first by design — telling a
        # caller to "pass --confirm" for a path that would be rejected anyway
        # would send them round the loop twice.
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    with pytest.raises(CliError) as caught:
        compile_plan("some-plan.json", config=_Cfg())

    assert caught.value.to_result().exit_code == 5
    assert caught.value.to_result().error is not None
    assert caught.value.to_result().error.kind is ErrorKind.CONFIRMATION_REQUIRED
    assert called == [], "nothing may run before confirmation"


def test_compile_status_reports_a_terminal_failure_as_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 0 would tell `compile-status && publish` to proceed on a dead batch."""
    import json

    from scout.cli.commands.compile import compile_status
    from scout.cli.result import ExitCode
    from scout.cli.tasks import TaskState, finish_run, write_run_marker

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": "1",
                        "title": "A",
                        "loc": "p.1",
                        "slug": "a",
                        "category": "concept",
                        "department": "ai_eng",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_run_marker(plan, pid=2**22)
    finish_run(plan, state=TaskState.FAILED, detail="route unreachable", exit_code=1)

    class _Cfg:
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    result = compile_status("plan.json", config=_Cfg())

    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["state"] == "failed"
    assert result.data["terminal"] is True


def test_compile_status_refuses_a_handle_that_names_no_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 0 told a caller a typo'd handle was an idle batch.

    Measured 2026-09-21: `compile-status <nonexistent>` exited 0 with
    `detail: "no plan at …"`, `done: 0`, `pending: []`. A script or an agent
    reading the exit code cannot tell that from a batch waiting to begin.
    `compile-cancel` has always raised for the same condition, and CLI_SPEC §1
    assigns an unresolvable identifier to input validation.
    """
    from scout.cli.commands.compile import compile_status

    class _Cfg:
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    with pytest.raises(CliError) as caught:
        compile_status("a-handle-that-names-no-batch", config=_Cfg())

    error = caught.value.to_result()
    assert error.exit_code == ExitCode.INPUT_VALIDATION
    assert error.error is not None
    assert error.error.kind is ErrorKind.INPUT_VALIDATION
    assert "no plan at" in error.error.message


def test_compile_status_separates_an_absent_batch_from_an_unstarted_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control, and the whole point of the distinction.

    A plan that exists and has staged nothing is a real `not_started` batch: a
    **finding** (exit 1, per CLI_SPEC §3), reported with its article count. A
    handle that names no plan is an unresolvable argument (exit 3). Before the
    split both returned exit 0, so neither could be told from the other — and
    the one with a real total was the one being under-reported.
    """
    import json

    from scout.cli.commands.compile import compile_status

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": "1",
                        "title": "A",
                        "loc": "p.1",
                        "slug": "a",
                        "category": "concept",
                        "department": "ai_eng",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class _Cfg:
        def require_repo(self) -> Path:
            return tmp_path

    monkeypatch.chdir(tmp_path)
    result = compile_status("plan.json", config=_Cfg())

    # A finding, not a refusal: the batch exists and has work to do.
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["state"] == "not_started"
    assert result.data["total"] == 1
    assert result.error is None, "an existing batch is reported, never refused"


# ── `snpmemory compile-cancel` ────────────────────────────────────────────


def _batch(tmp_path: Path, *slugs: str) -> Path:
    import json

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "raw/x.pdf",
                "articles": [
                    {
                        "section": str(i + 1),
                        "title": slug.title(),
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


class _RepoCfg:
    def __init__(self, root: Path) -> None:
        self._root = root

    def require_repo(self) -> Path:
        return self._root


def test_cancelling_a_running_batch_records_the_request_without_claiming_it_stopped(
    tmp_path: Path,
) -> None:
    """R4: it stops after the current article. Saying otherwise would be a lie."""
    import os

    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode
    from scout.cli.tasks import cancel_requested, write_run_marker

    plan = _batch(tmp_path, "a", "b")
    write_run_marker(plan, pid=os.getpid())

    result = compile_cancel(str(plan), config=_RepoCfg(tmp_path))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "cancelling"
    assert result.data["state"] == "running"
    assert "will stop after the current article" in result.summary
    assert cancel_requested(plan) is True


def test_cancelling_a_terminal_batch_is_a_no_op_that_says_so(tmp_path: Path) -> None:
    """An error here would push a caller into treating a finished batch as a fault."""
    import os

    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode
    from scout.cli.tasks import (
        TaskState,
        cancel_requested,
        finish_run,
        write_run_marker,
    )

    plan = _batch(tmp_path, "a")
    write_run_marker(plan, pid=os.getpid())
    finish_run(plan, state=TaskState.CANCELLED, detail="already stopped", exit_code=130)

    result = compile_cancel(str(plan), config=_RepoCfg(tmp_path))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "no_change"
    assert cancel_requested(plan) is False, "nothing may be written for a no-op"


def test_cancelling_a_batch_with_no_live_process_changes_nothing(
    tmp_path: Path,
) -> None:
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode
    from scout.cli.tasks import cancel_requested, write_run_marker

    plan = _batch(tmp_path, "a", "b")
    write_run_marker(plan, pid=2**22)

    result = compile_cancel(str(plan), config=_RepoCfg(tmp_path))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "not_running"
    assert cancel_requested(plan) is False


def test_cancelling_an_unknown_handle_is_input_validation(tmp_path: Path) -> None:
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode

    with pytest.raises(CliError) as caught:
        compile_cancel(str(tmp_path / "nope.json"), config=_RepoCfg(tmp_path))

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_cancelling_a_handle_outside_the_checkout_is_refused(tmp_path: Path) -> None:
    from scout.cli.commands.compile import compile_cancel
    from scout.cli.result import ExitCode

    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(CliError) as caught:
        compile_cancel("/etc/plan.json", config=_RepoCfg(root))

    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


# ── verify-extraction ────────────────────────────────────────────────────────


def _index_rows(rows: list[dict[str, object]]) -> type:
    """A stand-in asyncpg connection serving `rows` from rag_documents."""

    class _Connection:
        async def execute(self, _query: str, *_args: object) -> str:
            # The command scopes the RLS departments before reading.
            return "SET"

        async def fetch(self, _query: str, *_args: object) -> list[dict[str, object]]:
            return rows

        async def close(self) -> None:
            return None

    return _Connection


def _patch_index(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]
) -> None:
    import asyncpg

    connection = _index_rows(rows)()

    async def connect(**_kwargs: object) -> object:
        return connection

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(
        "scout.config.postgres_settings",
        lambda _role, env=None: type(
            "S",
            (),
            {
                "host": "h",
                "port": 5432,
                "database": "d",
                "user": "u",
                "password": "p",
            },
        )(),
    )


def test_verify_extraction_names_the_documents_that_lost_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finding, with the document named — not a count of degraded chunks.

    The pre-existing census gate reports states and totals. An operator holding
    `{'figures_status': {'partial': 110}}` still does not know which document
    to re-ingest.
    """
    from scout.cli.commands.verify import verify_extraction

    _patch_index(
        monkeypatch,
        [
            {
                "source_uri": "raw/papers/computers-12-00091.pdf",
                "title": "Paper",
                "extraction_status": json.dumps(
                    {
                        "extractors": {"figures": "failed", "tables": "ok"},
                        "figure_count": 7,
                        "figures_described": 0,
                        "complete": False,
                        "incomplete": ["figures"],
                    }
                ),
            },
            {
                "source_uri": "wiki/Entities/OpenMontage.md",
                "title": "OpenMontage",
                "extraction_status": json.dumps({"extractors": {}, "complete": True}),
            },
        ],
    )

    result = verify_extraction(config=_cfg())

    assert result.exit_code is ExitCode.SEMANTIC_FAILURE, "a finding, not a pass"
    assert result.data["incomplete_count"] == 1
    assert result.data["complete"] == 1
    named = result.data["documents"][0]
    assert named["source_uri"] == "raw/papers/computers-12-00091.pdf"
    assert named["incomplete"] == ["figures"]
    assert named["figure_count"] == 7
    assert named["figures_described"] == 0


def test_verify_extraction_passes_when_every_document_arrived_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It must be able to say yes, or a finding means nothing."""
    from scout.cli.commands.verify import verify_extraction

    _patch_index(
        monkeypatch,
        [
            {
                "source_uri": "raw/papers/paper.pdf",
                "title": "Paper",
                "extraction_status": json.dumps(
                    {
                        "extractors": {"figures": "ok", "tables": "ok"},
                        "figure_count": 7,
                        "figures_described": 7,
                        "complete": True,
                    }
                ),
            }
        ],
    )

    result = verify_extraction(config=_cfg())
    assert result.exit_code is ExitCode.SUCCESS
    assert result.data["incomplete_count"] == 0
    assert result.data["documents"] == []


def test_verify_extraction_does_not_call_an_unrecorded_corpus_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No record is unknown, not a pass.

    Every document indexed before the outcome column existed has none. Reading
    that absence as health is the T5.1 shape: a check that could not look
    reporting that it looked and found nothing wrong.
    """
    from scout.cli.commands.verify import verify_extraction

    _patch_index(
        monkeypatch,
        [
            {"source_uri": "raw/a.pdf", "title": "A", "extraction_status": None},
            {"source_uri": "raw/b.pdf", "title": "B", "extraction_status": None},
        ],
    )

    result = verify_extraction(config=_cfg())
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["unknown_count"] == 2
    assert result.data["unknown"] == ["raw/a.pdf", "raw/b.pdf"]
    assert "answered nothing" in result.summary


def test_verify_extraction_reports_an_unreachable_index_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Could-not-run is exit 2, never exit 1 — the contract this family keeps."""
    import asyncpg

    from scout.cli.commands.verify import verify_extraction

    async def refuse(**_kwargs: object) -> object:
        raise OSError("[Errno 111] Connection refused")

    monkeypatch.setattr(asyncpg, "connect", refuse)
    monkeypatch.setattr(
        "scout.config.postgres_settings",
        lambda _role, env=None: type(
            "S",
            (),
            {"host": "h", "port": 5432, "database": "d", "user": "u", "password": "p"},
        )(),
    )

    with pytest.raises(CliError) as caught:
        verify_extraction(config=_cfg())
    assert caught.value.kind is ErrorKind.INFRASTRUCTURE


def test_verify_extraction_does_not_pass_on_an_index_it_could_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero rows is "answered nothing", never "nothing is wrong".

    Measured live 2026-09-21: the first version read `rag_documents` without
    setting `scout.current_depts`, so fail-closed RLS returned an empty table
    and the command reported `status: pass, checked: 0` over a 439-document
    index. A check that cannot see its subject is not a pass — the same shape
    as T5.1, one layer up.
    """
    from scout.cli.commands.verify import verify_extraction

    _patch_index(monkeypatch, [])

    result = verify_extraction(config=_cfg())
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    assert result.data["checked"] == 0
    assert "answered nothing" in result.summary


def test_verify_extraction_scopes_the_read_to_the_canonical_departments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope is set on the same connection, before the read."""
    from scout.cli.commands.verify import verify_extraction
    from scout.policy import CANONICAL_DEPARTMENTS

    calls: list[tuple[str, tuple[object, ...]]] = []

    class _Connection:
        async def execute(self, query: str, *args: object) -> str:
            calls.append((query, args))
            return "SET"

        async def fetch(self, _query: str, *_args: object) -> list[dict[str, object]]:
            calls.append(("fetch", ()))
            return [
                {
                    "source_uri": "raw/a.pdf",
                    "title": "A",
                    "extraction_status": json.dumps(
                        {"extractors": {}, "complete": True}
                    ),
                }
            ]

        async def close(self) -> None:
            return None

    import asyncpg

    connection = _Connection()

    async def connect(**_kwargs: object) -> object:
        return connection

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(
        "scout.config.postgres_settings",
        lambda _role, env=None: type(
            "S",
            (),
            {"host": "h", "port": 5432, "database": "d", "user": "u", "password": "p"},
        )(),
    )

    result = verify_extraction(config=_cfg())

    assert result.exit_code is ExitCode.SUCCESS
    assert calls[0][0].startswith("SELECT set_config('scout.current_depts'")
    assert calls[0][1][0] == ",".join(sorted(CANONICAL_DEPARTMENTS))
    assert calls[1][0] == "fetch", "the scope must be set before the read"
