"""FastMCP-native authentication and request-scoped department identity."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier

from scout.policy import PolicyValidationError, validate_caller_departments
from scout.types import Scope

ASYMMETRIC_JWT_ALGORITHMS: Final[frozenset[str]] = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}
)
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})
MAX_STATIC_TOKEN_FILE_BYTES: Final[int] = 1024 * 1024


class AuthMode(StrEnum):
    JWT = "jwt"
    STATIC = "static"
    DEVELOPMENT = "development"


class AuthConfigError(ValueError):
    """Raised when authentication configuration cannot fail closed."""


class AuthenticationError(Exception):
    """Raised when a verified FastMCP access token has invalid identity claims."""


class AuthorizationError(ToolError):
    """Raised before retrieval when a caller attempts scope expansion."""


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Server-verified caller identity."""

    subject: str
    departments: frozenset[str]
    auth_mode: AuthMode

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise AuthConfigError("authenticated subject must not be empty")
        try:
            validate_caller_departments(self.departments)
        except PolicyValidationError as exc:
            raise AuthConfigError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Validated authentication mode and its FastMCP provider/identity."""

    mode: AuthMode
    provider: TokenVerifier | None
    development_identity: CallerIdentity | None = None


class StrictJWTVerifier(JWTVerifier):
    """Native JWT verifier supplemented with mandatory identity/time claims."""

    def __init__(
        self,
        *,
        department_claim: str,
        leeway_seconds: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.department_claim = department_claim
        self.leeway_seconds = leeway_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        native = await super().verify_token(token)
        if native is None:
            return None

        claims = dict(native.claims)
        now = time.time()
        exp = claims.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        if exp <= now - self.leeway_seconds:
            return None

        nbf = claims.get("nbf")
        if nbf is not None:
            if isinstance(nbf, bool) or not isinstance(nbf, (int, float)):
                return None
            if nbf > now + self.leeway_seconds:
                return None

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            return None
        try:
            departments = validate_caller_departments(
                claims.get(self.department_claim)
            )
        except PolicyValidationError:
            return None

        claims["sub"] = subject
        claims["departments"] = sorted(departments)
        claims["auth_mode"] = AuthMode.JWT.value
        return AccessToken(
            token=native.token,
            client_id=native.client_id,
            scopes=native.scopes,
            expires_at=int(exp),
            resource=native.resource,
            subject=subject,
            claims=claims,
        )


@dataclass(frozen=True, slots=True)
class _StaticIdentity:
    digest: bytes
    subject: str
    departments: frozenset[str]


class StaticTokenVerifier(TokenVerifier):
    """Static verifier that compares fixed-length SHA-256 token digests."""

    def __init__(
        self,
        identities: tuple[_StaticIdentity, ...],
        *,
        base_url: str,
    ) -> None:
        super().__init__(base_url=base_url)
        self._identities = identities

    async def verify_token(self, token: str) -> AccessToken | None:
        candidate = hashlib.sha256(token.encode("utf-8")).digest()
        matched: _StaticIdentity | None = None
        for identity in self._identities:
            if hmac.compare_digest(candidate, identity.digest):
                matched = identity
        if matched is None:
            return None

        claims: dict[str, object] = {
            "sub": matched.subject,
            "departments": sorted(matched.departments),
            "auth_mode": AuthMode.STATIC.value,
        }
        return AccessToken(
            token=token,
            client_id=matched.subject,
            subject=matched.subject,
            scopes=[],
            claims=claims,
        )


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise AuthConfigError(f"{name} is required")
    return value


def _parse_mode(environ: Mapping[str, str]) -> AuthMode:
    raw_mode = environ.get("SCOUT_AUTH_MODE", AuthMode.JWT.value).strip().lower()
    try:
        return AuthMode(raw_mode)
    except ValueError as exc:
        raise AuthConfigError("SCOUT_AUTH_MODE must be jwt, static, or development") from exc


def _load_jwt_config(environ: Mapping[str, str]) -> AuthConfig:
    base_url = _required(environ, "SCOUT_AUTH_BASE_URL")
    algorithm = environ.get("SCOUT_JWT_ALGORITHM", "RS256").strip().upper()
    if algorithm not in ASYMMETRIC_JWT_ALGORITHMS:
        raise AuthConfigError("SCOUT_JWT_ALGORITHM must name one asymmetric algorithm")

    public_key = environ.get("SCOUT_JWT_PUBLIC_KEY", "").strip()
    jwks_uri = environ.get("SCOUT_JWT_JWKS_URI", "").strip()
    if bool(public_key) == bool(jwks_uri):
        raise AuthConfigError(
            "configure exactly one of SCOUT_JWT_PUBLIC_KEY or SCOUT_JWT_JWKS_URI"
        )

    issuer = _required(environ, "SCOUT_JWT_ISSUER")
    audience = _required(environ, "SCOUT_JWT_AUDIENCE")
    department_claim = _required(environ, "SCOUT_JWT_DEPARTMENT_CLAIM")
    try:
        leeway = float(environ.get("SCOUT_JWT_LEEWAY_SECONDS", "0"))
    except ValueError as exc:
        raise AuthConfigError("SCOUT_JWT_LEEWAY_SECONDS must be numeric") from exc
    if leeway < 0 or leeway > 300:
        raise AuthConfigError("SCOUT_JWT_LEEWAY_SECONDS must be between 0 and 300")

    provider = StrictJWTVerifier(
        public_key=public_key or None,
        jwks_uri=jwks_uri or None,
        issuer=issuer,
        audience=audience,
        algorithm=algorithm,
        base_url=base_url,
        department_claim=department_claim,
        leeway_seconds=leeway,
    )
    return AuthConfig(mode=AuthMode.JWT, provider=provider)


def _read_static_token_mapping(environ: Mapping[str, str]) -> str:
    secret_file = environ.get("SCOUT_STATIC_TOKENS_FILE", "").strip()
    if not secret_file:
        return _required(environ, "SCOUT_STATIC_TOKENS")

    try:
        with Path(secret_file).open("rb") as stream:
            raw_bytes = stream.read(MAX_STATIC_TOKEN_FILE_BYTES + 1)
    except OSError as exc:
        raise AuthConfigError("SCOUT_STATIC_TOKENS_FILE cannot be read") from exc
    if len(raw_bytes) > MAX_STATIC_TOKEN_FILE_BYTES:
        raise AuthConfigError("SCOUT_STATIC_TOKENS_FILE is too large")
    if not raw_bytes:
        raise AuthConfigError("SCOUT_STATIC_TOKENS_FILE must not be empty")
    try:
        raw_mapping = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuthConfigError("SCOUT_STATIC_TOKENS_FILE must contain UTF-8 JSON") from exc
    if not raw_mapping.strip():
        raise AuthConfigError("SCOUT_STATIC_TOKENS_FILE must not be empty")
    return raw_mapping


def _load_static_config(environ: Mapping[str, str]) -> AuthConfig:
    base_url = _required(environ, "SCOUT_AUTH_BASE_URL")
    raw_mapping = _read_static_token_mapping(environ)
    try:
        parsed = json.loads(raw_mapping)
    except json.JSONDecodeError as exc:
        raise AuthConfigError("SCOUT_STATIC_TOKENS must be valid JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise AuthConfigError("SCOUT_STATIC_TOKENS must be a nonempty object")

    identities: list[_StaticIdentity] = []
    for raw_token, raw_identity in parsed.items():
        if not isinstance(raw_token, str) or not raw_token:
            raise AuthConfigError("static token values must not be empty")
        if not isinstance(raw_identity, dict):
            raise AuthConfigError("each static token must map to an identity object")
        subject = raw_identity.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthConfigError("static identity subject must not be empty")
        try:
            departments = validate_caller_departments(raw_identity.get("departments"))
        except PolicyValidationError as exc:
            raise AuthConfigError(str(exc)) from exc
        identities.append(
            _StaticIdentity(
                digest=hashlib.sha256(raw_token.encode("utf-8")).digest(),
                subject=subject,
                departments=departments,
            )
        )

    return AuthConfig(
        mode=AuthMode.STATIC,
        provider=StaticTokenVerifier(tuple(identities), base_url=base_url),
    )


def _load_development_config(
    environ: Mapping[str, str], bind_host: str | None
) -> AuthConfig:
    host = bind_host or environ.get("SCOUT_HOST", "0.0.0.0").strip()
    if host not in LOOPBACK_HOSTS:
        raise AuthConfigError("development authentication may bind only to loopback")
    raw_departments = environ.get(
        "SCOUT_DEVELOPMENT_DEPARTMENTS", "redteam,blueteam,ai_eng,infra"
    )
    try:
        departments = validate_caller_departments(
            [item.strip() for item in raw_departments.split(",") if item.strip()]
        )
    except PolicyValidationError as exc:
        raise AuthConfigError(str(exc)) from exc
    identity = CallerIdentity(
        subject="local-development",
        departments=departments,
        auth_mode=AuthMode.DEVELOPMENT,
    )
    return AuthConfig(
        mode=AuthMode.DEVELOPMENT,
        provider=None,
        development_identity=identity,
    )


def load_auth_config(
    environ: Mapping[str, str] | None = None,
    *,
    bind_host: str | None = None,
) -> AuthConfig:
    """Parse authentication configuration before the server binds a socket."""
    if environ is None:
        import os

        environ = os.environ
    mode = _parse_mode(environ)
    if mode is AuthMode.JWT:
        return _load_jwt_config(environ)
    if mode is AuthMode.STATIC:
        return _load_static_config(environ)
    return _load_development_config(environ, bind_host)


def access_token_to_identity(token: AccessToken) -> CallerIdentity:
    """Convert only verified, server-owned FastMCP token claims to identity."""
    subject = token.claims.get("sub") or token.subject
    raw_mode = token.claims.get("auth_mode")
    if not isinstance(subject, str):
        raise AuthConfigError("verified token is missing subject")
    if not isinstance(raw_mode, str):
        raise AuthConfigError("verified token is missing authentication mode")
    try:
        mode = AuthMode(raw_mode)
    except ValueError as exc:
        raise AuthConfigError("verified token is missing authentication mode") from exc
    try:
        departments = validate_caller_departments(token.claims.get("departments"))
    except PolicyValidationError as exc:
        raise AuthConfigError(str(exc)) from exc
    return CallerIdentity(subject=subject, departments=departments, auth_mode=mode)


def resolve_authorized_scope(
    identity: CallerIdentity,
    requested_departments: str | list[str] | None = None,
) -> Scope:
    """Narrow verified departments; reject every expansion or malformed request."""
    if requested_departments is None:
        return Scope(departments=identity.departments)

    raw_requested: object
    if isinstance(requested_departments, str):
        raw_requested = [requested_departments]
    else:
        raw_requested = requested_departments
    try:
        requested = validate_caller_departments(raw_requested)
    except PolicyValidationError as exc:
        raise AuthorizationError(str(exc)) from exc
    if not requested <= identity.departments:
        raise AuthorizationError("requested departments exceed authenticated scope")
    return Scope(departments=requested)
