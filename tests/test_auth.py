"""Authentication configuration and native FastMCP verifier tests."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp.server.auth import AccessToken

from scout.auth import (
    AuthConfigError,
    AuthMode,
    StaticTokenVerifier,
    StrictJWTVerifier,
    access_token_to_identity,
    load_auth_config,
)


@pytest.fixture
def rsa_keys() -> Iterator[tuple[str, str]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    yield private_pem, public_pem


def _jwt_env(public_key: str) -> dict[str, str]:
    return {
        "SCOUT_AUTH_MODE": "jwt",
        "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
        "SCOUT_JWT_ALGORITHM": "RS256",
        "SCOUT_JWT_PUBLIC_KEY": public_key,
        "SCOUT_JWT_ISSUER": "https://issuer.example.test",
        "SCOUT_JWT_AUDIENCE": "scout",
        "SCOUT_JWT_DEPARTMENT_CLAIM": "departments",
    }


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "agent-7",
        "iss": "https://issuer.example.test",
        "aud": "scout",
        "exp": now + 300,
        "departments": ["redteam", "infra"],
    }
    claims.update(overrides)
    return claims


def test_auth_defaults_to_jwt_and_rejects_incomplete_configuration() -> None:
    with pytest.raises(AuthConfigError, match="SCOUT_AUTH_BASE_URL"):
        load_auth_config({})


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512", "none"])
def test_jwt_configuration_rejects_symmetric_or_unknown_algorithms(
    algorithm: str, rsa_keys: tuple[str, str]
) -> None:
    env = _jwt_env(rsa_keys[1])
    env["SCOUT_JWT_ALGORITHM"] = algorithm
    with pytest.raises(AuthConfigError, match="asymmetric"):
        load_auth_config(env)


def test_jwt_configuration_requires_exactly_one_key_source(
    rsa_keys: tuple[str, str],
) -> None:
    env = _jwt_env(rsa_keys[1])
    env["SCOUT_JWT_JWKS_URI"] = "https://issuer.example.test/jwks.json"
    with pytest.raises(AuthConfigError, match="exactly one"):
        load_auth_config(env)


@pytest.mark.parametrize(
    "required_name",
    [
        "SCOUT_AUTH_BASE_URL",
        "SCOUT_JWT_ISSUER",
        "SCOUT_JWT_AUDIENCE",
        "SCOUT_JWT_DEPARTMENT_CLAIM",
    ],
)
def test_jwt_configuration_requires_identity_and_resource_settings(
    required_name: str, rsa_keys: tuple[str, str]
) -> None:
    env = _jwt_env(rsa_keys[1])
    del env[required_name]
    with pytest.raises(AuthConfigError, match=required_name):
        load_auth_config(env)


@pytest.mark.asyncio
async def test_native_jwt_verifier_accepts_valid_multi_department_token(
    rsa_keys: tuple[str, str],
) -> None:
    private_key, public_key = rsa_keys
    config = load_auth_config(_jwt_env(public_key))
    encoded = jwt.encode(_claims(), private_key, algorithm="RS256")

    assert config.provider is not None
    token = await config.provider.verify_token(encoded)
    assert token is not None
    identity = access_token_to_identity(token)
    assert identity.subject == "agent-7"
    assert identity.departments == frozenset({"redteam", "infra"})
    assert identity.auth_mode is AuthMode.JWT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": None},
        {"exp": int(time.time()) - 5},
        {"exp": "tomorrow"},
        {"nbf": int(time.time()) + 300},
        {"nbf": "later"},
        {"sub": ""},
        {"iss": "wrong"},
        {"aud": "wrong"},
        {"departments": []},
        {"departments": ["all"]},
        {"departments": ["unknown"]},
        {"departments": "infra"},
    ],
)
async def test_native_jwt_verifier_rejects_invalid_claims(
    rsa_keys: tuple[str, str], claim_overrides: dict[str, object]
) -> None:
    private_key, public_key = rsa_keys
    config = load_auth_config(_jwt_env(public_key))
    encoded = jwt.encode(_claims(**claim_overrides), private_key, algorithm="RS256")

    assert config.provider is not None
    assert await config.provider.verify_token(encoded) is None


@pytest.mark.asyncio
async def test_native_jwt_verifier_rejects_wrong_signature_and_algorithm(
    rsa_keys: tuple[str, str],
) -> None:
    _, public_key = rsa_keys
    config = load_auth_config(_jwt_env(public_key))
    assert config.provider is not None

    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_pem = wrong_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    wrong_signature = jwt.encode(_claims(), wrong_pem, algorithm="RS256")
    assert await config.provider.verify_token(wrong_signature) is None

    # A token header cannot select an algorithm other than the configured RS256.
    hs_token = jwt.encode(_claims(), "x" * 32, algorithm="HS256")
    assert await config.provider.verify_token(hs_token) is None


@pytest.mark.asyncio
async def test_native_jwks_verifier_rejects_unknown_key_id(
    rsa_keys: tuple[str, str],
) -> None:
    private_key, public_key = rsa_keys
    public_key_object = serialization.load_pem_public_key(public_key.encode())
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key_object))
    jwk.update({"kid": "known-key", "alg": "RS256", "use": "sig"})

    def serve_jwks(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://issuer.example.test/jwks.json"
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve_jwks)) as client:
        provider = StrictJWTVerifier(
            jwks_uri="https://issuer.example.test/jwks.json",
            issuer="https://issuer.example.test",
            audience="scout",
            algorithm="RS256",
            base_url="https://scout.example.test",
            department_claim="departments",
            leeway_seconds=0,
            http_client=client,
        )
        encoded = jwt.encode(
            _claims(), private_key, algorithm="RS256", headers={"kid": "missing-key"}
        )
        assert await provider.verify_token(encoded) is None


def test_server_entrypoint_validates_auth_before_constructing_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCOUT_AUTH_MODE", "jwt")
    for name in (
        "SCOUT_AUTH_BASE_URL",
        "SCOUT_JWT_PUBLIC_KEY",
        "SCOUT_JWT_JWKS_URI",
        "SCOUT_JWT_ISSUER",
        "SCOUT_JWT_AUDIENCE",
        "SCOUT_JWT_DEPARTMENT_CLAIM",
    ):
        monkeypatch.delenv(name, raising=False)
    from scout.serve import main

    with pytest.raises(AuthConfigError, match="SCOUT_AUTH_BASE_URL"):
        main()


def _static_env(token: str) -> dict[str, str]:
    return {
        "SCOUT_AUTH_MODE": "static",
        "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
        "SCOUT_STATIC_TOKENS": json.dumps(
            {
                token: {
                    "subject": "automation-1",
                    "departments": ["ai_eng", "infra"],
                }
            }
        ),
    }


@pytest.mark.asyncio
async def test_static_token_file_is_preferred_over_local_environment_fallback(
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "scout_static_tokens"
    secret_file.write_text(
        json.dumps(
            {
                "file-token": {
                    "subject": "file-identity",
                    "departments": ["infra"],
                }
            }
        ),
        encoding="utf-8",
    )
    env = _static_env("environment-token")
    env["SCOUT_STATIC_TOKENS_FILE"] = str(secret_file)
    config = load_auth_config(env)
    assert isinstance(config.provider, StaticTokenVerifier)
    file_identity = await config.provider.verify_token("file-token")
    assert file_identity is not None
    assert file_identity.subject == "file-identity"
    assert await config.provider.verify_token("environment-token") is None


@pytest.mark.parametrize("condition", ["missing", "empty", "invalid_utf8", "malformed"])
def test_static_token_file_fails_closed_on_invalid_secret_file(
    tmp_path: Path, condition: str
) -> None:
    secret_file = tmp_path / "scout_static_tokens"
    if condition == "empty":
        secret_file.write_bytes(b"")
    elif condition == "invalid_utf8":
        secret_file.write_bytes(b"\xff\xfe")
    elif condition == "malformed":
        secret_file.write_text("not-json", encoding="utf-8")
    env = {
        "SCOUT_AUTH_MODE": "static",
        "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
        "SCOUT_STATIC_TOKENS_FILE": str(secret_file),
    }
    with pytest.raises(AuthConfigError):
        load_auth_config(env)


def test_static_token_file_read_is_bounded(tmp_path: Path) -> None:
    secret_file = tmp_path / "scout_static_tokens"
    secret_file.write_bytes(b"x" * (1024 * 1024 + 1))
    env = {
        "SCOUT_AUTH_MODE": "static",
        "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
        "SCOUT_STATIC_TOKENS_FILE": str(secret_file),
    }
    with pytest.raises(AuthConfigError, match="too large"):
        load_auth_config(env)


def test_static_configuration_errors_never_echo_token_content(tmp_path: Path) -> None:
    secret = "sensitive-static-token-material"
    secret_file = tmp_path / "scout_static_tokens"
    secret_file.write_text(
        json.dumps(
            {secret: {"subject": "bad", "departments": ["all"]}}
        ),
        encoding="utf-8",
    )
    env = {
        "SCOUT_AUTH_MODE": "static",
        "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
        "SCOUT_STATIC_TOKENS_FILE": str(secret_file),
    }
    with pytest.raises(AuthConfigError) as error:
        load_auth_config(env)
    assert secret not in str(error.value)


@pytest.mark.asyncio
async def test_static_verifier_uses_server_owned_identity_and_digest_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "unit-test-static-token-value"
    config = load_auth_config(_static_env(secret))
    assert isinstance(config.provider, StaticTokenVerifier)

    comparisons: list[tuple[int, int]] = []
    import scout.auth as auth_module

    original = auth_module.hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        comparisons.append((len(left), len(right)))
        return original(left, right)

    monkeypatch.setattr(auth_module.hmac, "compare_digest", recording_compare)
    access_token = await config.provider.verify_token(secret)
    assert access_token is not None
    assert comparisons == [(32, 32)]
    identity = access_token_to_identity(access_token)
    assert identity.subject == "automation-1"
    assert identity.departments == frozenset({"ai_eng", "infra"})
    assert await config.provider.verify_token("invalid") is None


@pytest.mark.parametrize(
    "entry",
    [
        {"subject": "x", "departments": []},
        {"subject": "x", "departments": ["all"]},
        {"subject": "x", "departments": ["unknown"]},
        {"subject": "", "departments": ["infra"]},
    ],
)
def test_static_configuration_rejects_invalid_identity(entry: object) -> None:
    env = _static_env("token")
    env["SCOUT_STATIC_TOKENS"] = json.dumps({"token": entry})
    with pytest.raises(AuthConfigError):
        load_auth_config(env)


def test_development_mode_requires_loopback_and_has_server_owned_identity() -> None:
    env = {
        "SCOUT_AUTH_MODE": "development",
        "SCOUT_DEVELOPMENT_DEPARTMENTS": "redteam,infra",
    }
    with pytest.raises(AuthConfigError, match="loopback"):
        load_auth_config(env, bind_host="0.0.0.0")

    config = load_auth_config(env, bind_host="127.0.0.1")
    assert config.provider is None
    assert config.development_identity is not None
    assert config.development_identity.departments == frozenset({"redteam", "infra"})


@pytest.mark.asyncio
async def test_static_token_value_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "never-log-this-static-token"
    config = load_auth_config(_static_env(secret))
    assert config.provider is not None
    with caplog.at_level(logging.DEBUG):
        await config.provider.verify_token(secret)
        await config.provider.verify_token("invalid")
    assert secret not in caplog.text
    assert "invalid" not in caplog.text


def test_access_token_identity_rejects_malformed_departments() -> None:
    token = AccessToken(
        token="opaque",
        client_id="client",
        subject="subject",
        scopes=[],
        claims={"sub": "subject", "departments": ["all"], "auth_mode": "jwt"},
    )
    with pytest.raises(AuthConfigError):
        access_token_to_identity(token)
