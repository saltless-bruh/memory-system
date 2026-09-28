"""The running secret scanner must know the credential shapes this stack mints.

`scan_secrets.py` is the only secret scanner that has ever actually run here
(gitleaks, DR-26, never has). It knew five vendor prefixes -- sk-ant, sk-, ghp_,
glpat-, xox -- and none of the credentials this deployment itself handles: the
Gitea access token that host-sync and the propagation gate use, the webhook
secret that authenticates Gitea to host-sync, JWT bearer tokens, PEM private
keys, and AWS keys. A Gitea token committed today was invisible to it.

Every candidate is assembled at runtime so this file is never itself a finding,
and the negatives are the shapes this repository legitimately carries in bulk
-- commit SHAs, environment references -- which a naive 40-hex rule would drown
in.
"""

from __future__ import annotations

import base64
import json
import random
import string

import pytest

from scripts import scan_secrets


def _hex40(seed: int = 7) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("0123456789abcdef") for _ in range(40))


def _mixed(length: int, seed: int = 11) -> str:
    rng = random.Random(seed)
    return "".join(
        rng.choice(string.ascii_letters + string.digits) for _ in range(length)
    )


def _jwt() -> str:
    def segment(value: dict[str, object]) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = segment({"alg": "HS256", "typ": "JWT"})
    claims = segment({"sub": "agent", "departments": ["ai_eng"], "exp": 4102444800})
    return ".".join((header, claims, _mixed(43)))


def _pem() -> str:
    dashes = "-" * 5
    return (
        f"{dashes}BEGIN RSA PRIVATE KEY{dashes}\n{_mixed(64)}\n"
        f"{dashes}END RSA PRIVATE KEY{dashes}\n"
    )


def _labels(content: str) -> list[str]:
    return [f.label for f in scan_secrets.find_secrets(content, "config.env")]


@pytest.mark.parametrize(
    ("content", "label"),
    [
        (f"GITEA_TOKEN={_hex40()}\n", "Gitea access token"),
        (f'  gitea_admin_token: "{_hex40()}"\n', "Gitea access token"),
        (f"Authorization: token {_hex40()}\n", "Gitea access token"),
        (
            f"url = http://snp-admin:{_hex40()}@127.0.0.1:3000/snp.git\n",
            "Gitea access token",
        ),
        (f"Authorization: Bearer {_jwt()}\n", "JSON Web Token"),
        (_pem(), "Private key block"),
        ("aws_access_key_id = " + "AKIA" + _mixed(16).upper() + "\n", "AWS access key"),
    ],
)
def test_the_stack_s_own_credential_shapes_are_found(content: str, label: str) -> None:
    assert label in _labels(content)


@pytest.mark.parametrize(
    "content",
    [
        # Commit SHAs are 40 hex and appear throughout docs, locks and ledgers.
        f"merged {_hex40()} onto main\n",
        f"git rev-parse gitea/main:wiki  # {_hex40()}\n",
        f"commit: {_hex40()}\n",
        # References to a secret are not the secret.
        "GITEA_WEBHOOK_SECRET=${GITEA_WEBHOOK_SECRET:?set it}\n",
        "GITEA_WEBHOOK_SECRET=$(openssl rand -hex 32)\n",
        "webhook_secret = os.environ['GITEA_WEBHOOK_SECRET']\n",
        "GITEA_TOKEN=${GITEA_TOKEN}\n",
        # A JWT needs three segments with a JSON header; one base64 blob is not.
        "eyJhbGciOiJIUzI1NiJ9 is the header of every HS256 token\n",
        # A public key is not a private one.
        "-----BEGIN PUBLIC KEY-----\n",
    ],
)
def test_legitimate_repository_content_is_not_a_finding(content: str) -> None:
    assert _labels(content) == []


def test_new_findings_are_still_redacted() -> None:
    token = _hex40(99)
    findings = scan_secrets.find_secrets(f"GITEA_TOKEN={token}\n", "notes.txt")

    assert findings
    diagnostic = findings[0].format()
    assert token not in diagnostic
    assert token[:8] not in diagnostic and token[-8:] not in diagnostic
