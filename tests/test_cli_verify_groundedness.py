"""`snpmemory verify-groundedness` judges the caller's vault and keeps stdout clean.

Two defects shared one wrapper. The command called `impl.main()` without a page
loader, so it judged `vault.load_pages()`'s import-time default -- the package's
own `wiki/` -- whatever `WIKI_DIR` or `--root` named, and `--changed-only` asked
git about the package checkout rather than the caller's (the register #59 shape,
fixed for `verify-vault` on 2026-09-21 and still live here). And the per-page
verdicts were printed to stdout while the structured result carried only
`{scope, status}`, so `-o json` was unparseable once a page was judged and the
payload never said which pages failed.

Everything runs offline: the backend and the judge are injected, the vault is a
real tree under `tmp_path`, and `--changed-only` runs against a real git repo.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.verify import verify_groundedness  # noqa: E402
from scout.cli.config import Config  # noqa: E402
from scout.cli.registry import Prerequisite  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402
from scripts.verify_groundedness import Judgment, UnsupportedClaim  # noqa: E402

_BODY = "The cluster runs four replicas."


def _page_text(title: str) -> str:
    return (
        "---\n"
        "type: concept\n"
        f"title: {title}\n"
        "summary: One sentence.\n"
        "entities: [test]\n"
        "department: ai_eng\n"
        "sources:\n"
        "  - path: raw/notes/replicas.md\n"
        "    hint: replicas\n"
        "last_compiled: 2026-08-19\n"
        "---\n\n"
        f"# {title}\n\n{_BODY}\n"
    )


def _repo(root: Path, titles: dict[str, str], *, wiki: str = "wiki") -> Path:
    for name, title in titles.items():
        path = root / wiki / "concepts" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_page_text(title), encoding="utf-8")
    return root


def _cfg(repo: Path, **values: str) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, values=values, repo_root=repo)


class _Backend:
    async def retrieve(
        self, hint: str, *, path: str | None = None, scope: Any = None, k: int = 10
    ) -> list[Any]:
        from scout.types import RagChunk

        return [RagChunk(text=_BODY, file_path=path or "raw/x.md", score=1.0)]


def _wire(
    monkeypatch: pytest.MonkeyPatch, *, unsupported: frozenset[str] = frozenset()
) -> list[str]:
    """Inject an offline backend and a judge that records every title it sees."""
    judged: list[str] = []

    def judge(*, title: str, body: str, context: Any) -> Judgment:
        judged.append(title)
        if title in unsupported:
            return Judgment(
                unsupported=True,
                claims=(UnsupportedClaim(_BODY, "not in the source", True),),
            )
        return Judgment(unsupported=False)

    monkeypatch.setattr(
        "scout.cli.commands.verify._pgvector_backend", lambda _cfg: _Backend()
    )
    monkeypatch.setattr(
        "scripts.verify_groundedness.LiteLLMJudge.from_env",
        classmethod(lambda cls, env=None: judge),
    )
    return judged


def test_the_command_judges_the_callers_vault_not_the_package_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, {"only": "Caller Page"})
    judged = _wire(monkeypatch)

    result = verify_groundedness(config=_cfg(repo))

    assert judged == ["Caller Page"], "a page outside the caller's vault was judged"
    assert result.exit_code is ExitCode.SUCCESS


def test_wiki_dir_selects_the_tree_that_is_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two vaults in one checkout, two verdicts -- one per configured WIKI_DIR."""
    repo = _repo(tmp_path, {"a": "Default Tree"})
    _repo(repo, {"b": "Other Tree"}, wiki="vaults/other")
    judged = _wire(monkeypatch)

    verify_groundedness(config=_cfg(repo, WIKI_DIR="vaults/other"))

    assert judged == ["Other Tree"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_changed_only_asks_git_about_the_callers_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, {"kept": "Kept Page", "edited": "Edited Page"})
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "wiki")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "switch", "-q", "-c", "feature")
    edited = repo / "wiki" / "concepts" / "edited.md"
    edited.write_text(_page_text("Edited Page") + "\nMore.\n", encoding="utf-8")
    judged = _wire(monkeypatch)

    result = verify_groundedness(changed_only=True, config=_cfg(repo))

    assert judged == ["Edited Page"]
    assert result.data["scope"] == "changed"


def test_verdicts_reach_the_result_and_never_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stdout carries data only (render.py rule 1); the payload names the failures."""
    repo = _repo(tmp_path, {"good": "Good Page", "bad": "Bad Page"})
    _wire(monkeypatch, unsupported=frozenset({"Bad Page"}))

    result = verify_groundedness(config=_cfg(repo))

    assert capsys.readouterr().out == ""
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
    pages = {entry["page"]: entry for entry in result.data["pages"]}
    assert set(pages) == {"wiki/concepts/bad.md", "wiki/concepts/good.md"}
    assert pages["wiki/concepts/bad.md"]["verdict"] == "unsupported"
    assert pages["wiki/concepts/bad.md"]["claims"][0]["sentence"] == _BODY
    assert pages["wiki/concepts/good.md"]["verdict"] == "grounded"
    assert result.data["counts"]["unsupported"] == 1
    json.dumps(result.data)  # the payload is what `-o json` serialises
