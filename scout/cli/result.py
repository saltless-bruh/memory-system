"""Exit codes, error kinds, and the value every command returns.

A command computes a result; it does not print one. The same `CommandResult`
is rendered as text for a person, as JSON for an agent, and read as an exit
code by CI — which is what keeps those three surfaces from drifting apart, and
is the reason this layer exists before the MCP server rather than after it.

Exit codes carry two different meanings and must not be confused:

* An **outcome** (`0`, `1`) means the command ran correctly and is reporting
  what it found. Address drift is not a malfunction; it is the answer.
* An **error** (`2`-`7`) means the command could not complete.

No code serves both roles, and no two outcomes share a code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class ExitCode(IntEnum):
    """Process exit statuses. See `docs/CLI_SPEC.md` §1."""

    SUCCESS = 0
    #: The check ran and found real problems: drift, unsupported claims, lint errors.
    SEMANTIC_FAILURE = 1
    #: Database, gateway, or credentials unavailable. Never triggers mutation.
    INFRASTRUCTURE = 2
    #: Malformed arguments, a path outside `raw/`, an unknown department.
    INPUT_VALIDATION = 3
    #: Missing or invalid token, or a requested scope exceeding the caller's.
    AUTH = 4
    #: A destructive action was requested without `--yes`.
    CONFIRMATION_REQUIRED = 5
    #: Interactive input was needed with no TTY attached.
    TTY_REQUIRED = 6
    #: Page already exists, protected branch, pre-staged work.
    CONFLICT = 7


class ErrorKind(StrEnum):
    """Machine-stable error names, so an agent can branch without parsing prose."""

    INFRASTRUCTURE = "infrastructure"
    INPUT_VALIDATION = "input_validation"
    AUTH = "auth"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TTY_REQUIRED = "tty_required"
    CONFLICT = "conflict"

    @property
    def exit_code(self) -> ExitCode:
        return _KIND_TO_CODE[self]


_KIND_TO_CODE: dict[ErrorKind, ExitCode] = {
    ErrorKind.INFRASTRUCTURE: ExitCode.INFRASTRUCTURE,
    ErrorKind.INPUT_VALIDATION: ExitCode.INPUT_VALIDATION,
    ErrorKind.AUTH: ExitCode.AUTH,
    ErrorKind.CONFIRMATION_REQUIRED: ExitCode.CONFIRMATION_REQUIRED,
    ErrorKind.TTY_REQUIRED: ExitCode.TTY_REQUIRED,
    ErrorKind.CONFLICT: ExitCode.CONFLICT,
}

#: Codes that must never be followed by a mutating action. `2` is an inherited
#: guarantee -- `README.md` and `ci_address_gate.py` already promise that an
#: infrastructure failure heals nothing -- and the rest are errors, so a command
#: that could not run has no business writing.
NON_MUTATING_CODES: frozenset[ExitCode] = frozenset(
    code for code in ExitCode if code >= ExitCode.INFRASTRUCTURE
)


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """A failure an agent can act on without reading English.

    Rendered as the last line of stderr in structured mode, and as a plain
    sentence in text mode. `hint` names the flag or action that would resolve
    it, which is what turns a refusal into something an agent can retry
    correctly.
    """

    kind: ErrorKind
    message: str
    hint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind.value, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        if self.retryable:
            payload["retryable"] = True
        return payload


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a command returns. Never what it prints.

    Attributes:
        exit_code: The process status. Callers branch on this, not on text.
        data: The payload. Goes to **stdout**, and only in structured mode does
            it appear verbatim; text mode renders `summary` and any table the
            renderer knows how to draw.
        summary: One human sentence for the terminal.
        messages: Progress and diagnostics. Goes to **stderr**, always, so that
            `snpmemory fetch -o json | jq` is never polluted by chatter.
        error: Present exactly when `exit_code` denotes an error.
    """

    exit_code: ExitCode = ExitCode.SUCCESS
    data: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    messages: tuple[str, ...] = ()
    error: ErrorEnvelope | None = None

    def __post_init__(self) -> None:
        is_error = self.exit_code >= ExitCode.INFRASTRUCTURE
        if is_error and self.error is None:
            raise ValueError(f"exit code {self.exit_code!r} requires an error envelope")
        if not is_error and self.error is not None:
            raise ValueError(f"exit code {self.exit_code!r} must not carry an error")
        if self.error is not None and self.error.kind.exit_code is not self.exit_code:
            raise ValueError(
                f"error kind {self.error.kind.value!r} does not match "
                f"exit code {int(self.exit_code)}"
            )

    @property
    def ok(self) -> bool:
        """True only for a clean run. A semantic failure is not ok."""
        return self.exit_code is ExitCode.SUCCESS

    @property
    def mutating_is_allowed(self) -> bool:
        """Whether a caller may act on this result by writing something."""
        return self.exit_code not in NON_MUTATING_CODES

    @classmethod
    def failure(
        cls,
        kind: ErrorKind,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        messages: tuple[str, ...] = (),
    ) -> CommandResult:
        """Build an error result with its exit code derived from `kind`."""
        return cls(
            exit_code=kind.exit_code,
            summary=message,
            messages=messages,
            error=ErrorEnvelope(
                kind=kind,
                message=message,
                hint=hint,
                details=details or {},
                retryable=retryable,
            ),
        )
