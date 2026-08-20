"""Department and document-ACL policy invariants."""

from __future__ import annotations

import pytest

from scout.policy import (
    CANONICAL_DEPARTMENTS,
    PolicyValidationError,
    document_is_visible,
    validate_caller_departments,
    validate_document_acl,
)
from scout.types import Scope


def test_caller_departments_accept_only_nonempty_canonical_values() -> None:
    departments = validate_caller_departments(["infra", "ai_eng"])
    assert departments == frozenset({"infra", "ai_eng"})
    assert departments <= CANONICAL_DEPARTMENTS


@pytest.mark.parametrize(
    "value",
    [[], ["all"], ["unknown"], ["infra", "all"], "infra", [""], [1]],
)
def test_caller_departments_reject_empty_wildcard_unknown_or_malformed_values(
    value: object,
) -> None:
    with pytest.raises(PolicyValidationError):
        validate_caller_departments(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("all", "all"), ("redteam", "redteam"), ("infra", "infra")],
)
def test_document_acl_accepts_public_or_canonical_values(
    value: str, expected: str
) -> None:
    assert validate_document_acl(value) == expected


@pytest.mark.parametrize("value", ["", "unknown", ["infra"], None])
def test_document_acl_rejects_malformed_values(value: object) -> None:
    with pytest.raises(PolicyValidationError):
        validate_document_acl(value)


def test_public_document_acl_does_not_create_wildcard_caller_clearance() -> None:
    red_scope = Scope(departments=frozenset({"redteam"}))
    assert document_is_visible("all", red_scope)
    assert document_is_visible("redteam", red_scope)
    assert not document_is_visible("infra", red_scope)
    assert "all" not in red_scope.departments
