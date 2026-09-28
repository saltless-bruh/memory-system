"""The find-read-cite gate must measure the served surface, not this machine.

It built `ScoutDiyEngine.from_vault(~/Documents/memo-project/Obsidian Vault)`
in process: search hit the live index, but every read came from the host's
Obsidian folder, never from `/vault-replica/current` inside the scout
container, and never through MCP. So it could pass while the replica was stale
or missing a page, and fail when only the host copy lagged -- the "index finds,
vault answers" coherence an agent depends on was not what it measured.

These tests drive `group_find_read_cite` with the served MCP surface (`_Scout`)
replaced by an in-memory one, and with local engine construction made fatal,
so a pass can only have come from the surface an agent speaks to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "v3" / "checks"))

import engine_acceptance as ea  # noqa: E402

_PAGE = {
    "title": "A page",
    "type": "concept",
    "tldr": "What it says.",
    "content_hash": "0" * 64,
    "outline": [{"heading": "TL;DR"}, {"heading": "Details"}],
    "sections": {"TL;DR": "What it says.", "Details": "The detail."},
}


class _ServedSurface:
    """Answers like the scout MCP server: search envelopes and canonical reads."""

    def __init__(
        self,
        *,
        missing: frozenset[str] = frozenset(),
        broken_outline: frozenset[str] = frozenset(),
        control_found: bool = False,
    ) -> None:
        measurable, controls = ea.load_questions()
        self._expect = {q.query: q.expect for q in measurable}
        self._controls = {q.query: q.expect for q in controls}
        self._missing = missing
        self._broken_outline = broken_outline
        self._control_found = control_found
        self.reads: list[tuple[str, str | None]] = []

    async def search(self, query: str, k: int = 5) -> list[dict[str, object]]:
        if query in self._controls:
            path = self._controls[query] if self._control_found else "other.md"
            return [{"path": path, "snippet": "s", "seen": False}]
        return [
            {"path": self._expect[query], "snippet": "s", "seen": False},
            {"path": "other.md", "snippet": "s", "seen": False},
        ][:k]

    async def read(self, path: str, *, section: str | None = None) -> dict[str, object]:
        self.reads.append((path, section))
        if path in self._missing:
            raise RuntimeError(f"no such wiki page: {path}")
        page = {**_PAGE, "path": path}
        if path in self._broken_outline:
            page["outline"] = [{"heading": "TL;DR"}, {"heading": "Vanished"}]
        if section is not None:
            sections = page["sections"]
            assert isinstance(sections, dict)
            if section not in sections:
                raise RuntimeError(f"no such wiki section: {section}")
            return {**page, "sections": {section: sections[section]}}
        return page


@pytest.fixture
def no_local_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to answer from this machine's copy of the vault is fatal."""

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("find-read-cite built a local engine over a host vault")

    monkeypatch.setattr("scout.diy_engine.ScoutDiyEngine.from_vault", refuse)


def _serve(monkeypatch: pytest.MonkeyPatch, surface: _ServedSurface) -> None:
    monkeypatch.setattr(ea, "_Scout", lambda: surface)


def test_the_gate_answers_from_the_served_surface_only(
    monkeypatch: pytest.MonkeyPatch, no_local_engine: None
) -> None:
    surface = _ServedSurface()
    _serve(monkeypatch, surface)

    assert ea.group_find_read_cite() == "FIND READ CITE VERIFIED"
    read_paths = {path for path, _ in surface.reads}
    assert "other.md" in read_paths, "every page search offered must be read back"


def test_a_page_the_index_finds_but_the_replica_lacks_fails(
    monkeypatch: pytest.MonkeyPatch, no_local_engine: None
) -> None:
    """The stale-replica state the host-vault read could never see."""
    _serve(monkeypatch, _ServedSurface(missing=frozenset({"other.md"})))

    with pytest.raises(ea.GateFailure, match="could not be read"):
        ea.group_find_read_cite()


def test_an_outline_heading_no_section_read_resolves_is_uncitable(
    monkeypatch: pytest.MonkeyPatch, no_local_engine: None
) -> None:
    surface = _ServedSurface(broken_outline=frozenset({"other.md"}))
    _serve(monkeypatch, surface)

    with pytest.raises(ea.GateFailure, match="cited by heading"):
        ea.group_find_read_cite()
    # The heading was not in the full read, so the gate asked for it the way an
    # agent escalating to one section would, before calling it uncitable.
    assert ("other.md", "Vanished") in surface.reads


def test_the_absent_page_control_still_decides(
    monkeypatch: pytest.MonkeyPatch, no_local_engine: None
) -> None:
    _serve(monkeypatch, _ServedSurface(control_found=True))

    with pytest.raises(ea.GateFailure, match="absent-page control"):
        ea.group_find_read_cite()
