"""Tests for `snpmemory ingest`.

Two invariants carry almost all the weight here, and both are refusals rather
than features:

**Nothing outside `raw/` is indexed (R-3.1).** The corpus root is a security
boundary, not a convenience default, so the check has to survive `..` and a
symlink — a string comparison against the unresolved path passes both.

**No ACL, no ingestion.** A document ACL map that cannot be read is a conflict,
not a reason to fall back to a default. There is no default: defaulting is
precisely how a private document becomes world-readable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.ingest import ingest  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402

_ACL = """version: 1
rules:
  - path: "**"
    departments: [ai_eng]
"""


def _repo(tmp_path: Path) -> Path:
    """A checkout with a corpus root, one source file, and a valid ACL map."""
    repo = tmp_path.resolve()
    raw = repo / "raw"
    (raw / "papers").mkdir(parents=True)
    (raw / "papers" / "x.md").write_text("# Title\n\nbody\n", encoding="utf-8")
    (raw / ".acl.yaml").write_text(_ACL, encoding="utf-8")
    return repo


def _config(repo: Path, **values: str) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, values=values, repo_root=repo)


class _Spy:
    """Stands in for `ingest_directory` and records that it was reached."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = rows if rows is not None else []

    async def __call__(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self._rows


def _spy(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]] | None = None
) -> _Spy:
    spy = _Spy(rows)
    monkeypatch.setattr("scout.ingest.ingest_directory", spy)
    return spy


def _kind(caught: pytest.ExceptionInfo[CliError]) -> ExitCode:
    return caught.value.to_result().exit_code


# ── the corpus boundary ───────────────────────────────────────────────────


def test_a_symlink_pointing_out_of_raw_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case a `startswith` check on the unresolved path lets through.

    `raw/escape.md` is *lexically* inside the corpus root and points anywhere
    the attacker likes. Resolution before comparison is the only check that
    catches it, which is why this test is written first.
    """
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    (repo / "raw" / "escape.md").symlink_to(outside)
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(path="raw/escape.md", confirm=True, config=_config(repo))

    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert "outside the corpus root" in caught.value.message
    assert spy.calls == []


def test_a_traversal_out_of_raw_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    (tmp_path / "passwd").write_text("root:x:0:0\n", encoding="utf-8")
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(
            path=str(repo / "raw" / ".." / "passwd"), confirm=True, config=_config(repo)
        )

    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert spy.calls == []


def test_a_plain_outside_path_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("x\n", encoding="utf-8")
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(path=str(elsewhere), confirm=True, config=_config(repo))

    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert spy.calls == []


def test_a_relative_target_is_anchored_to_the_checkout_not_the_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: `--dir raw/papers` names one tree, from any directory.

    Resolving it against the process cwd made the command refuse its own corpus
    from every directory except the repository root — with "outside the corpus
    root", which reads as a boundary violation rather than as the bug it was.
    """
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spy = _spy(monkeypatch, [{"source_uri": "raw/papers/x.md", "chunks_count": 2}])

    result = ingest(dir="raw/papers", confirm=True, config=_config(repo))

    assert result.exit_code == ExitCode.SUCCESS
    assert spy.calls[0]["dir_path"] == repo / "raw" / "papers"


def test_a_path_under_raw_that_does_not_exist_is_input_validation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(CliError) as caught:
        ingest(path="raw/papers/absent.md", confirm=True, config=_config(repo))
    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert "does not exist" in caught.value.message


# ── --path and --dir are exclusive, and each names one kind of thing ───────


def test_neither_path_nor_dir_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        ingest(confirm=True, config=_config(_repo(tmp_path)))
    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert "exactly one" in caught.value.message


def test_both_path_and_dir_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(CliError) as caught:
        ingest(
            path="raw/papers/x.md",
            dir="raw/papers",
            confirm=True,
            config=_config(repo),
        )
    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert "exactly one" in caught.value.message


def test_path_naming_a_directory_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(path="raw/papers", confirm=True, config=_config(repo))

    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert "--dir" in (caught.value.hint or "")
    assert spy.calls == []


def test_dir_naming_a_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(dir="raw/papers/x.md", confirm=True, config=_config(repo))

    assert _kind(caught) == ExitCode.INPUT_VALIDATION
    assert "--path" in (caught.value.hint or "")
    assert spy.calls == []


# ── confirmation ──────────────────────────────────────────────────────────


def test_without_confirm_or_dry_run_nothing_is_indexed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Indexing spends embedding calls and replaces rows. It is not implicit."""
    repo = _repo(tmp_path)
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(dir="raw/papers", config=_config(repo))

    assert _kind(caught) == ExitCode.CONFIRMATION_REQUIRED
    assert caught.value.details["target"] == "raw/papers"
    assert spy.calls == []


def test_dry_run_needs_no_confirmation_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    spy = _spy(monkeypatch, [{"source_uri": "raw/papers/x.md", "chunks_count": 3}])

    result = ingest(dir="raw/papers", dry_run=True, config=_config(repo))

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["status"] == "dry_run"
    assert spy.calls[0]["dry_run"] is True


# ── the ACL map is the only authority ─────────────────────────────────────


def test_an_unreadable_acl_map_is_a_conflict_and_indexes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    (repo / "raw" / ".acl.yaml").unlink()
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(dir="raw/papers", confirm=True, config=_config(repo))

    assert _kind(caught) == ExitCode.CONFLICT
    assert spy.calls == []


def test_a_malformed_acl_map_is_a_conflict_and_indexes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A map that parses but declares nothing usable must not become a default."""
    repo = _repo(tmp_path)
    (repo / "raw" / ".acl.yaml").write_text("version: 1\nrules: []\n", encoding="utf-8")
    spy = _spy(monkeypatch)

    with pytest.raises(CliError) as caught:
        ingest(dir="raw/papers", confirm=True, config=_config(repo))

    assert _kind(caught) == ExitCode.CONFLICT
    assert spy.calls == []


# ── what a successful run reports ─────────────────────────────────────────


def test_a_directory_run_reconciles_and_a_single_file_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reconciliation deletes rows for files no longer present in the tree.

    Doing that from a single-file run would delete every sibling's rows, so the
    flag must follow the target's kind rather than the caller's intent.
    """
    repo = _repo(tmp_path)
    rows = [
        {"source_uri": "raw/papers/x.md", "chunks_count": 3},
        {"source_uri": "raw/papers/y.md", "chunks_count": 4},
    ]

    spy = _spy(monkeypatch, rows)
    ingest(dir="raw/papers", confirm=True, config=_config(repo))
    assert spy.calls[0]["reconcile"] is True

    spy = _spy(monkeypatch, rows)
    result = ingest(path="raw/papers/x.md", confirm=True, config=_config(repo))
    assert spy.calls[0]["reconcile"] is False
    # A single-file run reports on that file, not on everything the directory
    # pass happened to return.
    assert result.data["indexed"] == 1
    assert [row["source_uri"] for row in result.data["documents"]] == [
        "raw/papers/x.md"
    ]


def test_a_source_that_yields_no_text_is_reported_as_no_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 1 is an outcome: the file was examined and produced nothing."""
    repo = _repo(tmp_path)
    _spy(monkeypatch, [{"source_uri": "raw/papers/x.md", "chunks_count": 0}])

    result = ingest(path="raw/papers/x.md", confirm=True, config=_config(repo))

    assert result.exit_code == ExitCode.SEMANTIC_FAILURE
    assert result.data["no_evidence"] == ["raw/papers/x.md"]


def test_a_backend_failure_is_infrastructure_and_never_leaks_its_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A driver trace can carry a DSN; only the exception class is reported."""
    repo = _repo(tmp_path)

    async def _boom(**_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("postgresql://ingest:hunter2@db:5432/snp")

    monkeypatch.setattr("scout.ingest.ingest_directory", _boom)

    with pytest.raises(CliError) as caught:
        ingest(dir="raw/papers", confirm=True, config=_config(repo))

    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INFRASTRUCTURE
    assert "hunter2" not in repr(result)


def test_raw_dir_may_be_relocated_by_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`RAW_DIR` moves the boundary; it does not remove it."""
    repo = _repo(tmp_path)
    corpus = repo / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("x\n", encoding="utf-8")
    (corpus / ".acl.yaml").write_text(_ACL, encoding="utf-8")
    cfg = _config(repo, RAW_DIR="corpus")
    spy = _spy(monkeypatch, [{"source_uri": "corpus/a.md", "chunks_count": 1}])

    result = ingest(path="corpus/a.md", confirm=True, config=cfg)
    assert result.exit_code == ExitCode.SUCCESS
    assert spy.calls

    with pytest.raises(CliError) as caught:
        ingest(path="raw/papers/x.md", confirm=True, config=cfg)
    assert _kind(caught) == ExitCode.INPUT_VALIDATION


def test_ingest_uses_the_config_the_dispatcher_already_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: `snpmemory ingest` needed a hand-exported `.env`.

    The dispatcher resolves the project `.env` for a LOCAL command and hands it
    to the command as `Config`. The connection and the embedder then read
    `os.environ` instead, so an operator who had not exported `.env` in their
    shell got `ConfigError: POSTGRES_HOST is missing` — for configuration the
    tool had already loaded. Same defect as the Tier 1 `fetch` bug, in the two
    places it was missed.
    """
    repo = _repo(tmp_path)
    seen: dict[str, object] = {}

    async def _spy(**kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs)
        return [{"source_uri": "raw/papers/x.md", "chunks_count": 1}]

    monkeypatch.setattr("scout.ingest.ingest_directory", _spy)
    # An environment with nothing in it: only the resolved Config carries values.
    for name in ("POSTGRES_HOST", "LITELLM_BASE_URL", "LITELLM_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)

    cfg = _config(
        repo,
        POSTGRES_HOST="db.example",
        LITELLM_BASE_URL="http://gateway:4000/v1",
        LITELLM_MASTER_KEY="k",
    )
    ingest(dir="raw/papers", confirm=True, config=cfg)

    assert seen["env"] is cfg.values, (
        "the resolved configuration must reach the connection and the embedder"
    )
