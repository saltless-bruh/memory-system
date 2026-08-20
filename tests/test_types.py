"""Tests for scout.types — invariants and the structural injection guard."""

from __future__ import annotations

import dataclasses

import pytest

from scout.backends.fake import FakeRagBackend
from scout.backends.pgvector import PgVectorRlsBackend
from scout.types import (
    Address,
    Citation,
    ContextPiece,
    FetchResult,
    FetchStatus,
    RagBackend,
    RagChunk,
    Scope,
)


def test_fetchresult_ok_property() -> None:
    assert FetchResult(status=FetchStatus.OK).ok is True
    assert FetchResult(status=FetchStatus.NO_SOURCE).ok is False


def test_fetchresult_defaults_are_empty() -> None:
    r = FetchResult(status=FetchStatus.NO_SOURCE)
    assert r.context == ()
    assert r.citations == ()


def test_context_piece_has_no_action_field() -> None:
    """R-8.5 injection guard is structural: quotes carry data + provenance only,
    never an executable 'action'/'command' field."""
    fields = {f.name for f in dataclasses.fields(ContextPiece)}
    assert fields == {"text", "file_path", "loc"}
    assert "action" not in fields
    assert "command" not in fields


def test_core_dataclasses_are_frozen() -> None:
    piece = ContextPiece(text="x", file_path="raw/a.pdf")
    with pytest.raises(dataclasses.FrozenInstanceError):
        piece.text = "y"  # type: ignore[misc]


def test_scope_defaults_immutable() -> None:
    s = Scope(departments=frozenset({"infra"}))
    assert s.departments == frozenset({"infra"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.departments = frozenset({"redteam"})  # type: ignore[misc]


@pytest.mark.parametrize(
    "departments",
    [frozenset(), frozenset({"all"}), frozenset({"unknown"})],
)
def test_scope_rejects_empty_wildcard_or_unknown_departments(
    departments: frozenset[str],
) -> None:
    with pytest.raises(ValueError):
        Scope(departments=departments)


def test_ragchunk_meta_defaults_independent() -> None:
    a = RagChunk(text="a", file_path="raw/a.pdf")
    b = RagChunk(text="b", file_path="raw/b.pdf")
    assert a.meta == {} and b.meta == {}
    assert a.meta is not b.meta


def test_address_and_citation_shapes() -> None:
    addr = Address(path="raw/a.pdf", hint="kerberoasting", loc="p.1")
    assert (addr.path, addr.hint, addr.loc) == ("raw/a.pdf", "kerberoasting", "p.1")
    cite = Citation(file_path="raw/a.pdf", loc="p.1", score=0.9)
    assert cite.score == 0.9


def test_fetchstatus_values() -> None:
    assert FetchStatus.OK.value == "ok"
    assert FetchStatus.NO_SOURCE.value == "no_source"


def test_backends_satisfy_protocol() -> None:
    """Both a real-shaped and a stub adapter are structural RagBackends —
    the swappability contract (R-4.8)."""
    assert isinstance(FakeRagBackend(), RagBackend)
    assert isinstance(PgVectorRlsBackend(), RagBackend)
