"""The W-2 propagation gate must never leave its probe pages in the vault.

The gate publishes `V3-PROP-*` pages to the private vault's `main` and deletes
them again. Before this test existed the deletion ran only on the happy path:
a probe that never became findable made `require` raise before the removal
push, and the page stayed on `main` -- and so in the served index -- for good.
Eight of them did.

These tests drive the real control flow of `_vault_change_propagates` with
the git, compose, and served-surface edges replaced, so a timeout can be
produced on demand and the cleanup observed without touching a live vault.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "v3" / "checks"))

import engine_acceptance as ea  # noqa: E402


class _Scout:
    """The served surface, answering reads with whatever was asked for."""

    async def search(self, query: str, k: int = 5) -> list[dict[str, object]]:
        raise AssertionError("tests route every search through _await_sentinel")

    async def read(self, path: str) -> dict[str, object]:
        return {"path": path, "body": path}


class _Vault:
    """Records every publish the gate makes and which probes are still on main.

    `fail_removals` makes every deletion push raise, standing in for a vault
    remote that went away mid-run.
    """

    def __init__(self, *, fail_removals: bool = False) -> None:
        self.on_main: set[str] = set()
        self.removal_attempts: list[str] = []
        self.fail_removals = fail_removals

    def publish(
        self, clone: Path, relative: str, body: str | None, message: str
    ) -> ea._PublishedChange:
        if body is None:
            self.removal_attempts.append(relative)
            if self.fail_removals:
                raise ea.GateFailure(f"git push failed removing {relative}")
            self.on_main.discard(relative)
        else:
            self.on_main.add(relative)
        return ea._PublishedChange(
            commit="0" * 40, push_started_at=0.0, push_completed_at=0.0
        )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Fake every external edge of the gate; the control flow stays real."""
    state: dict[str, object] = {"compose": []}

    def fake_git(
        *args: str, cwd: Path, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, "", "")

    def fake_compose(*args: str) -> None:
        compose = state["compose"]
        assert isinstance(compose, list)
        compose.append(args)

    monkeypatch.setattr(ea, "_Scout", _Scout)
    monkeypatch.setattr(ea, "_git", fake_git)
    monkeypatch.setattr(ea, "_compose", fake_compose)
    return state


def _install(
    monkeypatch: pytest.MonkeyPatch,
    vault: _Vault,
    *,
    findable_after_publish: bool,
    leaks_while_stopped: bool = False,
    negative_leg_error: Exception | None = None,
) -> None:
    """Answer sentinel polls from the fake vault, per leg of the gate."""
    monkeypatch.setattr(ea, "_publish", vault.publish)

    async def fake_await(
        scout: object, sentinel: str, budget: float, *, poll_seconds: float = 0.0
    ) -> str | None:
        relative = f"{sentinel}.md"
        if budget == 0.0 or relative not in vault.on_main:
            return None
        # The first probe published is the positive leg; any later one is the
        # negative control, published while the watcher is stopped.
        first = not vault.removal_attempts
        if first:
            return relative if findable_after_publish else None
        if negative_leg_error is not None:
            raise negative_leg_error
        return relative if leaks_while_stopped else None

    monkeypatch.setattr(ea, "_await_sentinel", fake_await)


def test_a_probe_that_never_propagates_is_still_removed(
    harness: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout fails the gate *and* takes the probe back off main."""
    vault = _Vault()
    _install(monkeypatch, vault, findable_after_publish=False)

    with pytest.raises(ea.GateFailure, match="never became findable"):
        ea.group_vault_change_propagates()

    assert len(vault.removal_attempts) == 1
    assert vault.on_main == set()


def test_a_failure_after_the_negative_probe_still_removes_it(
    harness: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error while the watcher is stopped leaves neither probe behind."""
    vault = _Vault()
    _install(
        monkeypatch,
        vault,
        findable_after_publish=True,
        negative_leg_error=RuntimeError("the served surface went away"),
    )

    with pytest.raises(RuntimeError, match="served surface went away"):
        ea.group_vault_change_propagates()

    # Positive probe removed on the happy path, negative control by cleanup.
    assert len(vault.removal_attempts) == 2
    assert vault.on_main == set()
    compose = harness["compose"]
    assert isinstance(compose, list)
    assert ("start", "sync-job") in compose


def test_a_cleanup_failure_is_reported_without_masking_the_original(
    harness: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The timeout is what propagates; the failed removal is said out loud."""
    vault = _Vault(fail_removals=True)
    _install(monkeypatch, vault, findable_after_publish=False)

    with pytest.raises(ea.GateFailure, match="never became findable") as caught:
        ea.group_vault_change_propagates()

    notes = "\n".join(getattr(caught.value, "__notes__", []))
    assert "V3-PROP-" in notes
    assert "still on" in notes
    assert "still on" in capsys.readouterr().err


def test_the_happy_path_leaves_nothing_on_main(
    harness: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both legs pass: two probes published, two removed, none remaining."""
    vault = _Vault()
    _install(monkeypatch, vault, findable_after_publish=True)

    assert ea.group_vault_change_propagates() == "VAULT CHANGE PROPAGATES"

    assert len(vault.removal_attempts) == 2
    assert vault.on_main == set()


# --------------------------------------------------------------------------
# Against a real Git origin
# --------------------------------------------------------------------------
#
# The fake vault above is idempotent: a second removal of the same page simply
# succeeds. The real `_publish` was not. A deletion push that failed had already
# unlinked the page and made the local deletion commit, so the finalizer's
# retry died at `git add` on a path that no longer existed and never re-pushed
# the pending commit -- the probe stayed on `main`. These tests run the real
# `_publish` and `_git` against a bare repository so that path is exercised.


def _run_git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare vault remote whose `main` holds one authored page."""
    bare = tmp_path / "origin.git"
    _run_git("init", "--bare", "--initial-branch=main", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git("init", "--initial-branch=main", cwd=seed)
    (seed / ea.VAULT_SUBDIR).mkdir()
    (seed / ea.VAULT_SUBDIR / "index.md").write_text("# index\n", encoding="utf-8")
    _run_git("add", ea.VAULT_SUBDIR, cwd=seed)
    _run_git("commit", "-m", "seed", cwd=seed)
    _run_git("push", str(bare), "HEAD:main", cwd=seed)
    return bare


def _probes_on_main(bare: Path) -> list[str]:
    listed = _run_git(
        "ls-tree", "-r", "--name-only", "main", "--", ea.VAULT_SUBDIR, cwd=bare
    )
    return [line for line in listed.splitlines() if "V3-PROP-" in line]


@pytest.mark.parametrize("failing_removal", [1, 2], ids=["positive", "negative"])
def test_a_transient_deletion_push_failure_is_retried_to_completion(
    origin: Path,
    harness: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    failing_removal: int,
) -> None:
    """One dropped deletion push must not leave the probe on origin's main.

    The network drops the n-th deletion push once; a later push would succeed.
    The gate still fails -- the push did fail -- but the finalizer's retry has
    to land the deletion rather than trip over its own half-finished attempt.
    """
    monkeypatch.setattr(ea, "VAULT_REMOTE", origin.as_uri())
    monkeypatch.setattr(ea, "VAULT_BRANCH", "main")

    real_git = subprocess.run

    def flaky_git(
        *args: str, cwd: Path, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("push",):
            subject = real_git(
                ["git", "log", "-1", "--format=%s"],
                cwd=cwd,
                capture_output=True,
                text=True,
            ).stdout
            if subject.startswith("test(w2): remove"):
                removals = int(str(harness.get("removal_pushes", 0))) + 1
                harness["removal_pushes"] = removals
                if removals == failing_removal:
                    return subprocess.CompletedProcess(
                        ["git", *args],
                        128,
                        "",
                        "fatal: unable to access origin: Connection reset by peer",
                    )
        completed = real_git(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if check:
            ea.require(
                completed.returncode == 0,
                f"git {' '.join(args)} failed: {completed.stderr.strip()[:300]}",
            )
        return completed

    monkeypatch.setattr(ea, "_git", flaky_git)

    order: list[str] = []

    async def fake_await(
        scout: object, sentinel: str, budget: float, *, poll_seconds: float = 0.0
    ) -> str | None:
        if budget == 0.0:
            return None
        order.append(sentinel)
        # The positive leg propagates; the negative control, as it should, does
        # not arrive while the watcher is stopped.
        return f"{sentinel}.md" if len(order) == 1 else None

    monkeypatch.setattr(ea, "_await_sentinel", fake_await)

    with pytest.raises(ea.GateFailure, match="Connection reset by peer"):
        ea.group_vault_change_propagates()

    assert _probes_on_main(origin) == []
    # The retry re-pushed the pending deletion rather than giving up on it.
    assert harness["removal_pushes"] == failing_removal + 1
