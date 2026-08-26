"""The webhook signing secret must never have a usable default.

This script defaulted to the literal string `dev-secret`. A webhook configured
with a guessable secret accepts forged payloads and the receiver cannot tell the
difference — and Compose already requires a real secret, so the default was not
even a convenience. 2026 guidance is that a signing secret is never hardcoded or
committed; a fallback default is the same failure with an extra step.

Every test here checks a **refusal**, and none of them prints a secret value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.setup_gitea_webhook import (  # noqa: E402
    KNOWN_DEVELOPMENT_SECRETS,
    MIN_SECRET_LENGTH,
    _reject_insecure_secret,
    main,
)


def test_an_unset_secret_is_refused() -> None:
    message = _reject_insecure_secret("", development=False)
    assert message is not None
    assert "WEBHOOK_SECRET" in message


@pytest.mark.parametrize("secret", sorted(KNOWN_DEVELOPMENT_SECRETS))
def test_every_known_placeholder_is_refused(secret: str) -> None:
    assert _reject_insecure_secret(secret, development=False) is not None
    # Case and surrounding whitespace must not be a way past it.
    assert (
        _reject_insecure_secret(f"  {secret.upper()} ", development=False) is not None
    )


def test_a_short_secret_is_refused() -> None:
    """A secret short enough to brute-force signs nothing."""
    assert _reject_insecure_secret("a" * (MIN_SECRET_LENGTH - 1), development=False)


def test_a_real_secret_is_accepted() -> None:
    assert _reject_insecure_secret("k" * 40, development=False) is None


def test_development_permits_a_placeholder_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The escape hatch exists, is explicit, and is loud."""
    with caplog.at_level("WARNING"):
        assert _reject_insecure_secret("dev-secret", development=True) is None
    assert any("development" in record.message.lower() for record in caplog.records)


def test_development_does_not_permit_an_empty_secret() -> None:
    """There is nothing to sign with. The flag is not a bypass for absence."""
    assert _reject_insecure_secret("", development=True) is not None
    assert _reject_insecure_secret("   ", development=True) is not None


def test_the_refusal_never_echoes_the_secret() -> None:
    """The one value that must not reach a log or a terminal."""
    secret = "hunter2-hunter2-hunter2"[: MIN_SECRET_LENGTH - 1]
    message = _reject_insecure_secret(secret, development=False)
    assert message is not None
    assert secret not in message


def test_main_refuses_before_contacting_gitea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must come first — never after a request is already in flight."""
    called: list[str] = []
    monkeypatch.setattr(
        "scripts.setup_gitea_webhook.create_gitea_webhook",
        lambda **_kwargs: called.append("created"),
    )
    monkeypatch.setattr(
        "scripts.setup_gitea_webhook.send_test_ping",
        lambda *_a, **_k: called.append("pinged") or True,
    )

    assert main(["--secret", "dev-secret", "--token", "t"]) == 2
    assert main(["--secret", "", "--token", "t"]) == 2
    # Even the ping path, which needs no token, must not run.
    assert main(["--secret", "dev-secret", "--test-ping"]) == 2
    assert called == []
