"""Tests for the authoring family: `mint`, `compile`, `propose`.

Everything here writes, so most of what is asserted is refusal: that a mutation
without `--confirm` performs nothing, and that the refusal carries what would
have changed plus the command that would do it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.authoring import compile as compile_cmd  # noqa: E402
from scout.cli.commands.authoring import mint, propose  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ErrorKind, ExitCode  # noqa: E402


def _config(tmp_path: Path) -> Config:
    return Config(
        prerequisite=Prerequisite.LOCAL,
        values={
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "snp_rag",
            "POSTGRES_QUERY_USER": "rag_app_role",
            "POSTGRES_QUERY_PASSWORD": "test-password",
            "LITELLM_BASE_URL": "http://localhost:4000/v1",
            "LITELLM_MASTER_KEY": "test-key",
        },
        repo_root=tmp_path,
    )


# ── mint ──────────────────────────────────────────────────────────────────


def test_mint_refuses_a_department_a_caller_cannot_hold(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        mint(
            path="raw/x.pdf", hint="h", dept="all", loc="p.1", config=_config(tmp_path)
        )
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_mint_reports_a_working_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout.types import Address
    from scripts.mint import CandidateOutcome, MintResult, MintStatus

    async def fake_mint(backend, path, hints, *, department, loc):  # type: ignore[no-untyped-def]
        return MintResult(
            path=path,
            department=department,
            address=Address(path=path, hint=hints[0], loc=loc),
            status=MintStatus.MINTED,
            tried=((hints[0], CandidateOutcome.PASS),),
            available_locs=(loc,),
        )

    monkeypatch.setattr("scripts.mint.mint_address", fake_mint)
    monkeypatch.setattr("scout.cli.commands.authoring._backend", lambda cfg: object())

    result = mint(
        path="raw/x.pdf",
        hint="kernels",
        dept="ai_eng",
        loc="p.17",
        config=_config(tmp_path),
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "minted"
    assert result.data["loc"] == "p.17"


def test_mint_reports_no_working_hint_as_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 1 and no address — never a hint invented to make it pass (R-6.3)."""
    from scripts.mint import CandidateOutcome, MintResult, MintStatus

    async def fake_mint(backend, path, hints, *, department, loc):  # type: ignore[no-untyped-def]
        return MintResult(
            path=path,
            department=department,
            address=None,
            status=MintStatus.NO_HINT_WORKS,
            tried=((hints[0], CandidateOutcome.DRIFT),),
            available_locs=("p.3", "p.4"),
        )

    monkeypatch.setattr("scripts.mint.mint_address", fake_mint)
    monkeypatch.setattr("scout.cli.commands.authoring._backend", lambda cfg: object())

    result = mint(
        path="raw/x.pdf",
        hint="vague",
        dept="ai_eng",
        loc="p.17",
        config=_config(tmp_path),
    )
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["status"] == "no_hint_works"
    assert "hint" not in result.data
    assert result.data["available_locs"] == ["p.3", "p.4"]


# ── compile ───────────────────────────────────────────────────────────────


class _Prepared:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.frontmatter = {"sources": [{"path": "raw/x.pdf", "loc": "p.17"}]}
        self.content = "page"


def test_compile_without_confirm_writes_nothing_and_says_how(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "wiki" / "concepts" / "pooling.md"
    published: list[Path] = []

    monkeypatch.setattr(
        "scripts.compile_note.prepare_page",
        lambda *a, **k: _Prepared(target),
    )
    monkeypatch.setattr(
        "scripts.compile_note.publish_page",
        lambda prepared: published.append(prepared.path),
    )

    with pytest.raises(CliError) as caught:
        compile_cmd(
            path="raw/x.pdf",
            title="Pooling",
            category="concept",
            dept="ai_eng",
            loc="p.17",
            config=_config(tmp_path),
        )
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.CONFIRMATION_REQUIRED
    assert result.error is not None
    assert result.error.kind is ErrorKind.CONFIRMATION_REQUIRED
    # The refusal has to be actionable: what would change, and the exact re-run.
    assert result.error.details["proposed"]["path"] == "wiki/concepts/pooling.md"
    assert "--confirm" in (result.error.hint or "")
    assert published == []


def test_compile_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "wiki" / "concepts" / "pooling.md"
    published: list[Path] = []
    monkeypatch.setattr(
        "scripts.compile_note.prepare_page", lambda *a, **k: _Prepared(target)
    )
    monkeypatch.setattr(
        "scripts.compile_note.publish_page",
        lambda prepared: published.append(prepared.path),
    )

    result = compile_cmd(
        path="raw/x.pdf",
        title="Pooling",
        category="concept",
        dept="ai_eng",
        loc="p.17",
        dry_run=True,
        config=_config(tmp_path),
    )
    assert result.data["status"] == "dry_run"
    assert published == []


def test_compile_with_confirm_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "wiki" / "concepts" / "pooling.md"
    published: list[Path] = []

    def _publish(prepared: _Prepared) -> Path:
        published.append(prepared.path)
        return prepared.path

    monkeypatch.setattr(
        "scripts.compile_note.prepare_page", lambda *a, **k: _Prepared(target)
    )
    monkeypatch.setattr("scripts.compile_note.publish_page", _publish)

    result = compile_cmd(
        path="raw/x.pdf",
        title="Pooling",
        category="concept",
        dept="ai_eng",
        loc="p.17",
        confirm=True,
        config=_config(tmp_path),
    )
    assert result.data["status"] == "written"
    assert published == [target]


def test_an_existing_page_is_a_conflict_not_an_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.compile_note import CompileNoteError

    def _boom(*_a: object, **_k: object) -> None:
        raise CompileNoteError("wiki/concepts/pooling.md already exists")

    monkeypatch.setattr("scripts.compile_note.prepare_page", _boom)

    with pytest.raises(CliError) as caught:
        compile_cmd(
            path="raw/x.pdf",
            title="Pooling",
            category="concept",
            dept="ai_eng",
            loc="p.17",
            confirm=True,
            config=_config(tmp_path),
        )
    assert caught.value.to_result().exit_code == ExitCode.CONFLICT


# ── propose ───────────────────────────────────────────────────────────────


def test_propose_refuses_while_staged_paths_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Committing here would sweep up work the caller never named."""
    monkeypatch.setattr("scripts.propose_page._normalize_page", lambda p: p)
    monkeypatch.setattr("scripts.propose_page._staged_paths", lambda: {"src/other.py"})

    with pytest.raises(CliError) as caught:
        propose(page="wiki/concepts/pooling.md", confirm=True, config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.CONFLICT
    assert result.error is not None
    assert result.error.details["staged"] == ["src/other.py"]


def test_propose_without_confirm_creates_no_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.setattr("scripts.propose_page._normalize_page", lambda p: p)
    monkeypatch.setattr("scripts.propose_page._staged_paths", lambda: set())
    monkeypatch.setattr(
        "scripts.propose_page.wiki_changes",
        lambda allowed: ["wiki/concepts/pooling.md"],
    )
    monkeypatch.setattr("scripts.propose_page.current_branch", lambda: "feature/x")
    monkeypatch.setattr(
        "scripts.propose_page.main", lambda argv: calls.append(argv) or 0
    )

    with pytest.raises(CliError) as caught:
        propose(page="wiki/concepts/pooling.md", config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.CONFIRMATION_REQUIRED
    assert result.error is not None
    assert result.error.details["proposed"]["base_branch"] == "feature/x"
    assert calls == []


def test_a_page_with_no_change_is_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("scripts.propose_page._normalize_page", lambda p: p)
    monkeypatch.setattr("scripts.propose_page._staged_paths", lambda: set())
    monkeypatch.setattr("scripts.propose_page.wiki_changes", lambda allowed: [])

    result = propose(
        page="wiki/concepts/pooling.md", confirm=True, config=_config(tmp_path)
    )
    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["status"] == "no_change"


def test_propose_never_commits_to_the_branch_you_are_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R-6.4 / R-7.3: the page moves to a new branch, always.

    The delegated script cuts `wiki/<slug>-<timestamp>` before staging anything,
    so the branch recorded here is the one being left, not the one committed to.
    """
    seen: list[list[str]] = []
    monkeypatch.setattr("scripts.propose_page._normalize_page", lambda p: p)
    monkeypatch.setattr("scripts.propose_page._staged_paths", lambda: set())
    monkeypatch.setattr(
        "scripts.propose_page.wiki_changes",
        lambda allowed: ["wiki/concepts/pooling.md"],
    )
    monkeypatch.setattr("scripts.propose_page.current_branch", lambda: "main")
    monkeypatch.setattr(
        "scripts.propose_page.main", lambda argv: seen.append(argv) or 0
    )

    result = propose(
        page="wiki/concepts/pooling.md", confirm=True, config=_config(tmp_path)
    )
    assert result.data["status"] == "proposed"
    assert result.data["base_branch"] == "main"
    assert seen and "--page" in seen[0]


def test_a_page_that_does_not_exist_is_a_caller_mistake(tmp_path: Path) -> None:
    """`_normalize_page` is the script's own check; the command maps it to 3."""
    with pytest.raises(CliError) as caught:
        propose(page="wiki/concepts/never-written.md", config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION
