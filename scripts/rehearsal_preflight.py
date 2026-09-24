#!/usr/bin/env python3
"""Check the local rehearsal with real synthetic requests, only with --live.

Exit 0 means every required check passed, 1 means a live check failed, and 2
means configuration is invalid or live checks were not requested. This is an
infrastructure/retrieval preflight, not a certification of wiki content.

Network probes run in bounded subprocesses: third-party logs never reach the
operator, and importing the existing judge cannot mutate this CLI's environment.
Neither provider replies nor retrieved page text are included in the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEPARTMENTS = ("infra", "ai_eng", "redteam", "blueteam")
SERVICES = (
    "postgres-migrate",
    "postgres",
    "litellm",
    "scout",
    "host-sync",
    "git",
    "sync-job",
)
TOOLS = ("wiki_search", "wiki_read")
NETWORK_CHECKS = {
    "host_sync": ("host_sync_ready",),
    "embedding": ("embedding",),
    "generation": ("generation",),
    "judge": ("judge",),
    "scout": ("scout_tools", "scout_search", "scout_read"),
}
CHECKS = ("compose", "sync_ready", *sum(NETWORK_CHECKS.values(), ()))
DESCRIPTIONS = {
    "compose": "All required containers healthy; migration exited successfully",
    "sync_ready": "In-container /tmp/snp-sync-job/ready marker exists",
    "host_sync_ready": "Host-sync /ready reports a published commit and no error",
    "embedding": "Real snp-embed request returns one finite 1024-number vector",
    "generation": "Real snp-llm request responds OK to a synthetic prompt",
    "judge": "Real snp-judge request accepts a synthetic supported claim",
    "scout_tools": "Authenticated Scout advertises wiki_search and wiki_read",
    "scout_search": "Scoped wiki_search returns nondegraded page results",
    "scout_read": "Same-scope wiki_read returns a canonical TL;DR envelope",
}
REASONS = frozenset(
    {
        "verified",
        "requires_live",
        "invalid_arguments",
        "invalid_configuration",
        "missing_configuration",
        "env_file_unreadable",
        "env_file_invalid",
        "token_file_unreadable",
        "dependency_unavailable",
        "timeout",
        "unauthorized",
        "rate_limited",
        "upstream_error",
        "http_error",
        "connection_error",
        "invalid_response",
        "probe_failed",
        "compose_failed",
        "services_not_ready",
        "marker_missing",
        "host_sync_not_ready",
        "invalid_embedding",
        "invalid_generation",
        "unexpected_generation",
        "invalid_judge",
        "judge_rejected_control",
        "unexpected_tools",
        "tool_error",
        "invalid_search",
        "no_results",
        "degraded_search",
        "dependency_failed",
        "invalid_read",
        "invalid_worker_response",
    }
)
STATES = frozenset(
    {
        "created",
        "running",
        "paused",
        "restarting",
        "removing",
        "exited",
        "dead",
        "unknown",
        "missing",
    }
)
HEALTH = frozenset({"healthy", "unhealthy", "starting", "none", "unknown"})
CONFIG_FIELDS = frozenset(
    {
        "LITELLM_MASTER_KEY",
        "SCOUT_AUTH_HEADER",
        "LITELLM_PORT",
        "SCOUT_PORT",
        "HOST_SYNC_PORT",
        "SCOUT_URL",
        "SCOUT_BASE_URL",
    }
)
MAX_RESPONSE_BYTES = 1_048_576
SYNTHETIC_FACT = "The rehearsal fixture contains exactly three blue squares."


class ProbeError(RuntimeError):
    """Only constant classification codes may cross a diagnostic boundary."""

    def __init__(self, reason: str, fields: Sequence[str] = ()) -> None:
        self.reason = reason if reason in REASONS else "probe_failed"
        self.fields = [name for name in fields if name in CONFIG_FIELDS]
        super().__init__(self.reason)


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    reason: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, repr=False)
class Settings:
    """Secrets and connection strings deliberately have no generated repr."""

    gateway_url: str = "http://127.0.0.1:4000/v1"
    master_key: str = ""
    scout_url: str = "http://127.0.0.1:8080/mcp"
    scout_header: str = ""
    host_sync_url: str = "http://127.0.0.1:9000/ready"
    department: str = "infra"
    query: str = "OpenShift"
    timeout: float = 30


@dataclass(frozen=True, repr=False)
class Config:
    settings: Settings
    environment: Mapping[str, str]
    root: Path
    env_file: Path | None


def _timeout(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise ProbeError("invalid_arguments") from None
    if not math.isfinite(result) or not 0 < result <= 300:
        raise ProbeError("invalid_arguments")
    return result


def _port(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip() or str(default)
    if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 65535:
        raise ProbeError("invalid_configuration", [name])
    return int(raw)


def _scout_url(env: Mapping[str, str]) -> str:
    name = "SCOUT_URL" if env.get("SCOUT_URL", "").strip() else "SCOUT_BASE_URL"
    url = env.get(name, "").strip()
    if not url:
        return f"http://127.0.0.1:{_port(env, 'SCOUT_PORT', 8080)}/mcp"
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in url)
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise ProbeError("invalid_configuration", [name]) from None
    path = parsed.path.rstrip("/")
    if not path.endswith("/mcp"):
        path += "/mcp"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def load_config(
    *,
    root: Path = ROOT,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    department: str = "infra",
    query: str = "OpenShift",
    timeout: float = 30,
) -> Config:
    """Parse dotenv without interpolation/global writes, then apply overrides."""
    from dotenv.parser import parse_stream

    if department not in DEPARTMENTS or not query.strip() or len(query) > 2000:
        raise ProbeError("invalid_arguments")
    _timeout(str(timeout))
    # Compose runs from the repository, which may differ from the caller's cwd.
    candidate = (env_file if env_file is not None else root / ".env").absolute()
    # Reported as the file actually loaded, so it is cleared when the
    # default .env is simply absent and the environment supplies the values.
    selected: Path | None = candidate
    values: dict[str, str] = {}
    try:
        with candidate.open(encoding="utf-8") as stream:
            for binding in parse_stream(stream):
                if binding.error:
                    raise ProbeError("env_file_invalid")
                if binding.key is not None and binding.value is not None:
                    values[binding.key] = binding.value
    except FileNotFoundError:
        if env_file is not None:
            raise ProbeError("env_file_unreadable") from None
        selected = None
    except (OSError, UnicodeError):
        raise ProbeError("env_file_unreadable") from None
    values.update(os.environ if environ is None else environ)
    master = values.get("LITELLM_MASTER_KEY", "").strip()
    header = values.get("SCOUT_AUTH_HEADER", "").strip()
    if not header:
        try:
            with (root / ".secrets" / "scout_test_token").open(encoding="utf-8") as f:
                token = f.read(16_385).strip()
            if token:
                header = "Bearer " + token
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            raise ProbeError("token_file_unreadable") from None
    missing = [
        name
        for name, value in (
            ("LITELLM_MASTER_KEY", master),
            ("SCOUT_AUTH_HEADER", header),
        )
        if not value
    ]
    if missing:
        raise ProbeError("missing_configuration", missing)
    invalid = []
    if not re.fullmatch(r"[\x21-\x7e]{1,16384}", master):
        invalid.append("LITELLM_MASTER_KEY")
    if not re.fullmatch(r"(?i:Bearer) [A-Za-z0-9._~+/=-]{1,16384}", header):
        invalid.append("SCOUT_AUTH_HEADER")
    if invalid:
        raise ProbeError("invalid_configuration", invalid)
    settings = Settings(
        gateway_url=f"http://127.0.0.1:{_port(values, 'LITELLM_PORT', 4000)}/v1",
        master_key=master,
        scout_url=_scout_url(values),
        scout_header=header,
        host_sync_url=f"http://127.0.0.1:{_port(values, 'HOST_SYNC_PORT', 9000)}/ready",
        department=department,
        query=query,
        timeout=timeout,
    )
    return Config(settings, values, root, selected)


def classify_error(error: BaseException) -> str:
    """Inspect types and HTTP codes, never exception strings or response bodies."""
    # Error reporting must still work when the selected interpreter lacks the
    # repository's runtime dependencies.
    try:
        import httpx
    except ImportError:
        http_status: tuple[type[BaseException], ...] = ()
        http_timeout: tuple[type[BaseException], ...] = ()
        http_request: tuple[type[BaseException], ...] = ()
    else:
        http_status = (httpx.HTTPStatusError,)
        http_timeout = (httpx.TimeoutException,)
        http_request = (httpx.RequestError,)

    pending = [error]
    visited: set[int] = set()
    fallback = "probe_failed"
    while pending and len(visited) < 32:
        exc = pending.pop()
        if id(exc) in visited:
            continue
        visited.add(id(exc))
        if isinstance(exc, ProbeError):
            return exc.reason
        code = None
        if isinstance(exc, http_status):
            # `http_status` is built at runtime, so the attribute is read
            # defensively rather than through an unprovable narrowing.
            code = getattr(getattr(exc, "response", None), "status_code", None)
        elif isinstance(exc, urllib.error.HTTPError):
            code = exc.code
        if code is not None:
            if code in {401, 403}:
                return "unauthorized"
            if code == 429:
                return "rate_limited"
            return "upstream_error" if 500 <= code <= 599 else "http_error"
        if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired, *http_timeout)):
            return "timeout"
        if isinstance(exc, (ImportError, FileNotFoundError)):
            fallback = "dependency_unavailable"
        elif isinstance(exc, (OSError, *http_request)):
            fallback = "connection_error"
        elif isinstance(exc, (json.JSONDecodeError, UnicodeError)):
            fallback = "invalid_response"
        if isinstance(exc, BaseExceptionGroup):
            pending.extend(exc.exceptions)
        pending.extend(x for x in (exc.__cause__, exc.__context__) if x is not None)
    return fallback


def _failed(check_id: str, error: BaseException) -> Check:
    return Check(check_id, "fail", classify_error(error))


def request_json(
    method: str,
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    transport: Any = None,
) -> Any:
    import httpx

    with (
        httpx.Client(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client,
        client.stream(method, url, headers=headers, json=payload) as response,
    ):
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise ProbeError("invalid_response")
    return json.loads(chunks)


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def probe_embedding(
    settings: Settings, request: Callable[..., Any] = request_json
) -> Check:
    try:
        response = request(
            "POST",
            settings.gateway_url + "/embeddings",
            timeout=settings.timeout,
            headers={"Authorization": "Bearer " + settings.master_key},
            payload={
                "model": "snp-embed",
                "input": ["SNP synthetic rehearsal probe."],
                "dimensions": 1024,
            },
        )
        rows = response.get("data") if isinstance(response, dict) else None
        vector = (
            rows[0].get("embedding")
            if (isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict))
            else None
        )
        if (
            not isinstance(vector, list)
            or len(vector) != 1024
            or not all(map(_number, vector))
        ):
            raise ProbeError("invalid_embedding")
        return Check(
            "embedding",
            "pass",
            "verified",
            {"vectors": 1, "dimensions": 1024, "finite": True},
        )
    except Exception as exc:
        return _failed("embedding", exc)


def probe_generation(
    settings: Settings, request: Callable[..., Any] = request_json
) -> Check:
    try:
        response = request(
            "POST",
            settings.gateway_url + "/chat/completions",
            timeout=settings.timeout,
            headers={"Authorization": "Bearer " + settings.master_key},
            payload={
                "model": "snp-llm",
                "messages": [{"role": "user", "content": "Reply only OK"}],
                "temperature": 0,
                "max_tokens": 512,
            },
        )
        choices = response.get("choices") if isinstance(response, dict) else None
        message = (
            choices[0].get("message")
            if (
                isinstance(choices, list)
                and len(choices) == 1
                and isinstance(choices[0], dict)
            )
            else None
        )
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProbeError("invalid_generation")
        if content.strip() != "OK":
            raise ProbeError("unexpected_generation")
        return Check("generation", "pass", "verified", {"responded_ok": True})
    except Exception as exc:
        return _failed("generation", exc)


def probe_host_sync(
    settings: Settings, request: Callable[..., Any] = request_json
) -> Check:
    try:
        response = request("GET", settings.host_sync_url, timeout=settings.timeout)
        if not isinstance(response, dict):
            raise ProbeError("invalid_response")
        published = response.get("published_commit")
        has_commit = (
            isinstance(published, str)
            and re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", published) is not None
        )
        no_error = "last_error" in response and response["last_error"] in (None, "")
        valid = response.get("status") == "ready" and has_commit and no_error
        return Check(
            "host_sync_ready",
            "pass" if valid else "fail",
            "verified" if valid else "host_sync_not_ready",
            {"published_commit_present": has_commit, "no_error": no_error},
        )
    except Exception as exc:
        return _failed("host_sync_ready", exc)


def probe_judge(
    settings: Settings, judge_factory: Callable[..., Any] | None = None
) -> Check:
    # This legacy module loads dotenv at import time. Production invokes this
    # function only in the isolated worker, with explicit constructor settings.
    try:
        from scripts.verify_groundedness import Judgment, LiteLLMJudge, SourceContext

        judge = (judge_factory or LiteLLMJudge)(
            base_url=settings.gateway_url,
            api_key=settings.master_key,
            model="snp-judge",
            timeout=settings.timeout,
        )
        result = judge(
            title="Synthetic rehearsal control",
            body=SYNTHETIC_FACT,
            context=[
                SourceContext(path="synthetic-control", loc=None, text=SYNTHETIC_FACT)
            ],
        )
        if (
            not isinstance(result, Judgment)
            or type(result.unsupported) is not bool
            or not isinstance(result.claims, tuple)
        ):
            raise ProbeError("invalid_judge")
        if result.unsupported or result.claims:
            return Check(
                "judge",
                "fail",
                "judge_rejected_control",
                {"typed_result": True, "supported": False},
            )
        return Check(
            "judge", "pass", "verified", {"typed_result": True, "supported": True}
        )
    except Exception as exc:
        return _failed("judge", exc)


def _envelope(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        raise ProbeError("tool_error")
    # SDK .data materializes absent optional schema fields as None. Validate the
    # actual wire envelope to avoid falsely rejecting a correct mode=tldr read.
    data = getattr(result, "structured_content", None)
    if not isinstance(data, dict):
        raise ProbeError("invalid_response")
    return data


def _page_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 2048
        and not any(ord(c) < 32 for c in value)
        and "\\" not in value
        and not PurePosixPath(value).is_absolute()
        and ".." not in PurePosixPath(value).parts
        and value.endswith(".md")
    )


def validate_search(data: dict[str, Any]) -> tuple[str | None, Check]:
    rows = data.get("results")
    returned = data.get("returned")
    if (
        not isinstance(rows, list)
        or type(returned) is not int
        or returned != len(rows)
        or not 0 <= returned <= 5
        or type(data.get("suppressed_as_seen")) is not int
        or data["suppressed_as_seen"] != 0
        or type(data.get("has_more")) is not bool
        or data["has_more"] != (returned == 5)
    ):
        raise ProbeError("invalid_search")
    paths: set[str] = set()
    degraded = 0
    for row in rows:
        if (
            not isinstance(row, dict)
            or not _page_path(row.get("path"))
            or row["path"] in paths
            or row.get("seen") is not False
            or type(row.get("degraded")) is not bool
            or not _number(row.get("score"))
            or not isinstance(row.get("type"), str)
            or not row["type"].strip()
            or not isinstance(row.get("snippet"), str)
        ):
            raise ProbeError("invalid_search")
        paths.add(row["path"])
        degraded += int(row["degraded"])
    reason = (
        "no_results" if not returned else "degraded_search" if degraded else "verified"
    )
    return (rows[0]["path"] if rows else None), Check(
        "scout_search",
        "pass" if reason == "verified" else "fail",
        reason,
        {
            "returned": returned,
            "suppressed_as_seen": 0,
            "has_more": data["has_more"],
            "degraded": bool(degraded),
            "degraded_count": degraded,
        },
    )


def validate_read(data: dict[str, Any], path: str) -> Check:
    required = {"path", "title", "type", "tldr", "content_hash"}
    if (
        set(data) != required
        or data.get("path") != path
        or any(
            not isinstance(data.get(key), str) or not data[key].strip()
            for key in required
        )
        or re.fullmatch(r"[a-fA-F0-9]{64}", data["content_hash"]) is None
    ):
        raise ProbeError("invalid_read")
    return Check("scout_read", "pass", "verified", {"canonical_valid": True})


def scout_client(settings: Settings) -> Any:
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    def http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        # The three named parameters satisfy McpHttpClientFactory; **kwargs stays
        # so that anything else a caller supplies is absorbed and then overridden
        # rather than reaching httpx. The probe's own budget governs the timeout,
        # and redirects and ambient proxy trust are forced off unconditionally.
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        kwargs.update(
            timeout=settings.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        return httpx.AsyncClient(**kwargs)

    transport = StreamableHttpTransport(
        settings.scout_url,
        headers={"Authorization": settings.scout_header},
        httpx_client_factory=http_client,
    )
    return Client(transport, timeout=settings.timeout)


async def probe_scout(
    settings: Settings, client_factory: Callable[..., Any] = scout_client
) -> list[Check]:
    checks: list[Check] = []
    stage = "scout_tools"
    try:
        async with (
            asyncio.timeout(settings.timeout),
            client_factory(settings) as client,
        ):
            tools = await client.list_tools()
            names = [getattr(tool, "name", None) for tool in tools]
            if len(names) != 2 or set(names) != set(TOOLS):
                raise ProbeError("unexpected_tools")
            checks.append(
                Check(
                    stage,
                    "pass",
                    "verified",
                    {"required_tools": list(TOOLS), "tool_count": 2},
                )
            )
            stage = "scout_search"
            result = await client.call_tool(
                "wiki_search",
                {
                    "query": settings.query,
                    "department": settings.department,
                    "k": 5,
                    "seen": [],
                },
                raise_on_error=False,
            )
            path, search = validate_search(_envelope(result))
            checks.append(search)
            stage = "scout_read"
            if path is not None:
                result = await client.call_tool(
                    "wiki_read",
                    {
                        "path": path,
                        "department": settings.department,
                        "mode": "tldr",
                    },
                    raise_on_error=False,
                )
                checks.append(validate_read(_envelope(result), path))
    except Exception as exc:
        # A session-close failure still invalidates the last operation.
        checks = [check for check in checks if check.id != stage]
        checks.append(_failed(stage, exc))
    present = {check.id for check in checks}
    checks.extend(
        Check(name, "skip", "dependency_failed")
        for name in NETWORK_CHECKS["scout"]
        if name not in present
    )
    return checks


def _compose(config: Config) -> list[str]:
    command = ["docker", "compose"]
    if config.env_file is not None:
        command.extend(["--env-file", str(config.env_file)])
    return command


def _run(
    config: Config, runner: Callable[..., Any], command: list[str], **kwargs: Any
) -> Any:
    return runner(
        command,
        cwd=config.root,
        env=dict(config.environment),
        capture_output=True,
        text=True,
        timeout=config.settings.timeout,
        check=False,
        **kwargs,
    )


def probe_compose(config: Config, runner: Callable[..., Any] = subprocess.run) -> Check:
    try:
        completed = _run(
            config, runner, [*_compose(config), "ps", "--all", "--format", "json"]
        )
        if completed.returncode:
            raise ProbeError("compose_failed")
        raw = completed.stdout.strip()
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ProbeError("invalid_response")
        reduced: list[dict[str, object]] = []
        ready = True
        for name in SERVICES:
            found = [row for row in rows if row.get("Service") == name]
            if not found:
                reduced.append(
                    {
                        "service": name,
                        "state": "missing",
                        "health": "unknown",
                        "exit_code": None,
                    }
                )
                ready = False
            for row in found:
                state = row.get("State")
                health = row.get("Health") or "none"
                state = (
                    state if isinstance(state, str) and state in STATES else "unknown"
                )
                health = (
                    health
                    if isinstance(health, str) and health in HEALTH
                    else "unknown"
                )
                code = row.get("ExitCode")
                code = code if type(code) is int and 0 <= code <= 255 else None
                reduced.append(
                    {
                        "service": name,
                        "state": state,
                        "health": health,
                        "exit_code": code,
                    }
                )
                ready &= (
                    (state == "exited" and code == 0)
                    if name == "postgres-migrate"
                    else (state == "running" and health == "healthy")
                )
        return Check(
            "compose",
            "pass" if ready else "fail",
            "verified" if ready else "services_not_ready",
            {"services": reduced},
        )
    except Exception as exc:
        return _failed("compose", exc)


def probe_sync_ready(
    config: Config, runner: Callable[..., Any] = subprocess.run
) -> Check:
    try:
        result = _run(
            config,
            runner,
            [
                *_compose(config),
                "exec",
                "-T",
                "sync-job",
                "test",
                "-f",
                "/tmp/snp-sync-job/ready",
            ],
        )
        if result.returncode:
            raise ProbeError(
                "marker_missing" if result.returncode == 1 else "compose_failed"
            )
        return Check("sync_ready", "pass", "verified", {"marker_present": True})
    except Exception as exc:
        return _failed("sync_ready", exc)


_DATA_KEYS = frozenset(
    {
        "services",
        "service",
        "state",
        "health",
        "exit_code",
        "marker_present",
        "vectors",
        "dimensions",
        "finite",
        "responded_ok",
        "typed_result",
        "supported",
        "published_commit_present",
        "no_error",
        "required_tools",
        "tool_count",
        "returned",
        "suppressed_as_seen",
        "has_more",
        "degraded",
        "degraded_count",
        "canonical_valid",
    }
)


def _safe_worker_data(value: Any, depth: int = 0) -> bool:
    if depth > 5:
        return False
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return 0 <= value <= 1_000_000
    if isinstance(value, str):
        return value in STATES | HEALTH | set(TOOLS) | set(SERVICES)
    if isinstance(value, list):
        return len(value) <= 100 and all(
            _safe_worker_data(item, depth + 1) for item in value
        )
    if isinstance(value, dict):
        return set(value) <= _DATA_KEYS and all(
            _safe_worker_data(item, depth + 1) for item in value.values()
        )
    return False


def run_network_probe(
    config: Config, kind: str, runner: Callable[..., Any] = subprocess.run
) -> list[Check]:
    """Keep secrets out of argv and enforce a wall-clock deadline per probe."""
    expected = NETWORK_CHECKS[kind]
    fields = {"timeout"}
    if kind in {"embedding", "generation", "judge"}:
        fields |= {"gateway_url", "master_key"}
    elif kind == "scout":
        fields |= {"scout_url", "scout_header", "department", "query"}
    else:
        fields.add("host_sync_url")
    payload = {
        name: value for name, value in asdict(config.settings).items() if name in fields
    }
    try:
        result = _run(
            config,
            runner,
            [
                sys.executable,
                "-c",
                "from scripts.rehearsal_preflight import _network_worker; "
                f"raise SystemExit(_network_worker({kind!r}))",
            ],
            input=json.dumps({"live": True, "settings": payload}),
        )
        if result.returncode:
            raise ProbeError("probe_failed")
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ProbeError("invalid_worker_response")
        checks = []
        for row, name in zip(rows, expected, strict=True):
            if (
                not isinstance(row, dict)
                or set(row) != {"id", "status", "reason", "data"}
                or row["id"] != name
                or row["status"] not in {"pass", "fail", "skip"}
                or row["reason"] not in REASONS
                or not isinstance(row["data"], dict)
                or not _safe_worker_data(row["data"])
                or (row["status"] == "pass") != (row["reason"] == "verified")
            ):
                raise ProbeError("invalid_worker_response")
            checks.append(Check(**row))
        return checks
    except Exception as exc:
        return [
            _failed(expected[0], exc),
            *(Check(name, "skip", "dependency_failed") for name in expected[1:]),
        ]


def _network_worker(kind: str) -> int:
    """Internal subprocess entry point; never read a vault or use its prose."""
    try:
        payload = json.loads(sys.stdin.read(65_536))
        if payload.get("live") is not True or kind not in NETWORK_CHECKS:
            return 2
        settings = Settings(**payload["settings"])
        if kind == "scout":
            checks = asyncio.run(probe_scout(settings))
        else:
            probe = {
                "embedding": probe_embedding,
                "generation": probe_generation,
                "judge": probe_judge,
                "host_sync": probe_host_sync,
            }[kind]
            checks = [probe(settings)]
        print(json.dumps([asdict(check) for check in checks], allow_nan=False))
        return 0
    except Exception:
        # Parent discards captured stdout/stderr on any nonzero worker exit.
        return 2


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse normally repeats user input, which can contain a credential.
        raise ProbeError("invalid_arguments")


def _report(checks: list[Check], *, live: bool, code: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "live": live,
        "ready": code == 0,
        "exit_code": code,
        "checks": [asdict(check) for check in checks],
    }


def _print_report(report: dict[str, Any], output: str) -> None:
    if output == "json":
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return
    print("Rehearsal ready: " + ("yes" if report["ready"] else "no"))
    for row in report["checks"]:
        description = DESCRIPTIONS.get(row["id"], "Configuration and command arguments")
        print(f"{row['status'].upper()} {row['id']}: {row['reason']} — {description}")
        if row["data"]:
            print("  " + json.dumps(row["data"], sort_keys=True))
    if not report["live"]:
        print(
            "Use --live to run Docker, HTTP, synthetic provider, and scoped Scout checks."
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = ROOT,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    parser = _Parser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run real synthetic provider, Scout, readiness and Docker checks",
    )
    parser.add_argument("--output", choices=("text", "json"), default="text")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Configuration file (default: repository .env); environment overrides it",
    )
    parser.add_argument("--department", choices=DEPARTMENTS, default="infra")
    parser.add_argument(
        "--query",
        default="OpenShift",
        help="Scout search query (default: OpenShift); never printed",
    )
    parser.add_argument(
        "--timeout",
        type=_timeout,
        default=30.0,
        help="Maximum seconds per live probe, including child startup (0 < seconds <= 300; default: 30)",
    )
    arguments = list(sys.argv[1:] if argv is None else argv)
    output = (
        "json"
        if "--output=json" in arguments
        or any(
            first == "--output" and second == "json"
            for first, second in zip(arguments, arguments[1:], strict=False)
        )
        else "text"
    )
    live = False
    try:
        args = parser.parse_args(arguments)
        live, output = args.live, args.output
        if not args.query.strip() or len(args.query) > 2000:
            raise ProbeError("invalid_arguments")
        if not live:
            checks = [Check(name, "skip", "requires_live") for name in CHECKS]
            code = 2
        else:
            config = load_config(
                root=root,
                env_file=args.env_file,
                environ=environ,
                department=args.department,
                query=args.query,
                timeout=args.timeout,
            )
            checks = [
                Check("configuration", "pass", "verified"),
                probe_compose(config, runner),
                probe_sync_ready(config, runner),
            ]
            for kind in NETWORK_CHECKS:
                checks.extend(run_network_probe(config, kind, runner))
            code = 0 if all(check.status == "pass" for check in checks) else 1
    except ProbeError as exc:
        checks = [Check("configuration", "fail", exc.reason, {"fields": exc.fields})]
        checks.extend(Check(name, "skip", "dependency_failed") for name in CHECKS)
        code = 2
    except Exception as exc:
        checks = [_failed("configuration", exc)]
        code = 2
    _print_report(_report(checks, live=live, code=code), output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
