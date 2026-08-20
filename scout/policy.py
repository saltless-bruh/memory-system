"""Canonical department and document access-control policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeGuard

CANONICAL_DEPARTMENTS: frozenset[str] = frozenset(
    {"redteam", "blueteam", "ai_eng", "infra"}
)
PUBLIC_DOCUMENT_ACL = "all"


class PolicyValidationError(ValueError):
    """Raised when caller scope or document ACL metadata is malformed."""


class _DepartmentScope(Protocol):
    departments: frozenset[str]


def is_canonical_department(value: object) -> TypeGuard[str]:
    """Return whether a value is one canonical department name."""
    return isinstance(value, str) and value in CANONICAL_DEPARTMENTS


def validate_caller_departments(value: object) -> frozenset[str]:
    """Validate authenticated caller departments without wildcard authority."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise PolicyValidationError("caller departments must be a nonempty array")

    raw_values = list(value)
    if not raw_values:
        raise PolicyValidationError("caller departments must not be empty")
    if not all(isinstance(item, str) for item in raw_values):
        raise PolicyValidationError("caller departments must contain only strings")

    departments = frozenset(raw_values)
    invalid = departments - CANONICAL_DEPARTMENTS
    if invalid:
        raise PolicyValidationError("caller departments contain non-canonical values")
    return departments


def validate_document_acl(value: object) -> str:
    """Validate one stored document ACL value.

    ``all`` is document metadata meaning visible to every authenticated caller. It
    is deliberately not accepted by :func:`validate_caller_departments`.
    """
    if value == PUBLIC_DOCUMENT_ACL:
        return PUBLIC_DOCUMENT_ACL
    if is_canonical_department(value):
        return value
    raise PolicyValidationError("document ACL must be 'all' or a canonical department")


def document_is_visible(document_acl: object, scope: _DepartmentScope) -> bool:
    """Return whether a validated nonempty caller scope can see a document."""
    acl = validate_document_acl(document_acl)
    departments = validate_caller_departments(scope.departments)
    return acl == PUBLIC_DOCUMENT_ACL or acl in departments
