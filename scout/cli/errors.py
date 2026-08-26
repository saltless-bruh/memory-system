"""The exception a command raises, and how a parser failure becomes one.

Command bodies raise `CliError`; the dispatcher converts it into a
`CommandResult`. That keeps error handling out of every command and guarantees
one envelope shape regardless of where the failure came from.
"""

from __future__ import annotations

from typing import Any

from scout.cli.result import CommandResult, ErrorKind


class CliError(Exception):
    """A failure with a machine-stable kind and an actionable hint."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint
        self.details = details or {}
        self.retryable = retryable

    def to_result(self) -> CommandResult:
        return CommandResult.failure(
            self.kind,
            self.message,
            hint=self.hint,
            details=self.details,
            retryable=self.retryable,
        )


def input_error(message: str, *, hint: str | None = None, **details: Any) -> CliError:
    return CliError(ErrorKind.INPUT_VALIDATION, message, hint=hint, details=details)


def infrastructure_error(
    message: str, *, hint: str | None = None, retryable: bool = True, **details: Any
) -> CliError:
    return CliError(
        ErrorKind.INFRASTRUCTURE,
        message,
        hint=hint,
        details=details,
        retryable=retryable,
    )


def auth_error(message: str, *, hint: str | None = None, **details: Any) -> CliError:
    return CliError(ErrorKind.AUTH, message, hint=hint, details=details)


def conflict_error(
    message: str, *, hint: str | None = None, **details: Any
) -> CliError:
    return CliError(ErrorKind.CONFLICT, message, hint=hint, details=details)


def confirmation_required(action: str, *, flag: str = "--yes") -> CliError:
    """Refuse a destructive action rather than performing it unasked."""
    return CliError(
        ErrorKind.CONFIRMATION_REQUIRED,
        f"{action} is destructive and was not confirmed",
        hint=f"pass {flag} to proceed",
        details={"action": action, "flag": flag},
    )


def tty_required(question: str, *, flag: str) -> CliError:
    """Interactive input was needed with no terminal to ask on."""
    return CliError(
        ErrorKind.TTY_REQUIRED,
        f"{question} requires a terminal",
        hint=f"pass {flag} instead of prompting",
        details={"flag": flag},
    )


def answer_required(question: str, *, flag: str) -> CliError:
    """An answer was needed and `--interactive` was not requested.

    A terminal is permission to *render* a prompt, never a reason to need one:
    an agent may hold a pty and would sit in front of a menu it cannot answer.
    """
    return CliError(
        ErrorKind.INPUT_VALIDATION,
        f"{question} was not supplied",
        hint=f"pass {flag}, or --interactive to be prompted",
        details={"flag": flag},
    )
