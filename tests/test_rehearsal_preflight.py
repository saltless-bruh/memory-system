"""Offline failure and shape oracles; no test claims live provider readiness."""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dotenv
import httpx
import pytest

from scripts import rehearsal_preflight as preflight

MASTER = "sk-synthetic-master-do-not-print"
TOKEN = "synthetic.SCOUT.DO-NOT-PRINT"
HEADER = "Bearer " + TOKEN
PRIVATE = "PRIVATE-SYNTHETIC-PAGE-CONTENT-DO-NOT-PRINT"
PAGE = "concepts/private-synthetic-page.md"


def assert_sanitized(value: Any) -> None:
    text = value if isinstance(value, str) else repr(value)
    for secret in (MASTER, TOKEN, HEADER, PRIVATE, PAGE):
        assert secret not in text


@pytest.fixture
def settings() -> preflight.Settings:
    return preflight.Settings(master_key=MASTER, scout_header=HEADER)


@pytest.fixture
def config(tmp_path: Path, settings: preflight.Settings) -> preflight.Config:
    return preflight.Config(
        settings,
        {"LITELLM_MASTER_KEY": MASTER, "SCOUT_AUTH_HEADER": HEADER},
        tmp_path,
        None,
    )


def synthetic_env() -> dict[str, str]:
    return {"LITELLM_MASTER_KEY": MASTER, "SCOUT_AUTH_HEADER": HEADER}


def completed(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["test-only"], returncode, stdout, PRIVATE + HEADER
    )


def compose_rows() -> list[dict[str, Any]]:
    return [
        {
            "Service": name,
            "State": "exited" if name == "postgres-migrate" else "running",
            "Health": "" if name == "postgres-migrate" else "healthy",
            "ExitCode": 0,
            "Command": MASTER,
            "Status": PRIVATE,
            "Name": TOKEN,
        }
        for name in preflight.SERVICES
    ]


def search_envelope(*, degraded: bool = False) -> dict[str, Any]:
    return {
        "results": [
            {
                "path": PAGE,
                "type": "concept",
                "score": 0.8,
                "snippet": PRIVATE,
                "seen": False,
                "degraded": degraded,
                **({"reason": PRIVATE} if degraded else {}),
            }
        ],
        "returned": 1,
        "suppressed_as_seen": 0,
        "has_more": False,
    }


def read_envelope() -> dict[str, Any]:
    return {
        "path": PAGE,
        "title": PRIVATE,
        "type": "concept",
        "tldr": PRIVATE,
        "content_hash": "a" * 64,
    }


def mcp_result(data: dict[str, Any], *, error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        structured_content=data,
        is_error=error,
        data=SimpleNamespace(
            **data, sections=None, sources=None, outline=None, updated=None, links=None
        ),
    )


class FakeScout:
    def __init__(
        self,
        *,
        search: Any = None,
        read: Any = None,
        tools: Any = None,
        enter_error: Exception | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self.search = mcp_result(search_envelope()) if search is None else search
        self.read = mcp_result(read_envelope()) if read is None else read
        self.tools = (
            [SimpleNamespace(name=name) for name in preflight.TOOLS]
            if tools is None
            else tools
        )
        self.enter_error = enter_error
        self.read_error = read_error
        self.calls: list[Any] = []

    async def __aenter__(self) -> FakeScout:
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def list_tools(self) -> Any:
        self.calls.append(("list_tools",))
        return self.tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((name, arguments, kwargs))
        if name == "wiki_read" and self.read_error:
            raise self.read_error
        return self.search if name == "wiki_search" else self.read


def http_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET", "http://synthetic.test/", headers={"Authorization": HEADER}
    )
    response = httpx.Response(code, request=request, text=PRIVATE + MASTER)
    return httpx.HTTPStatusError(
        HEADER + MASTER + PRIVATE, request=request, response=response
    )


def request_with_response(payload: Any, *, status: int = 200) -> Any:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    def request(*args: Any, **kwargs: Any) -> Any:
        return preflight.request_json(
            *args, **kwargs, transport=httpx.MockTransport(transport)
        )

    return request


def test_no_live_does_not_load_credentials_or_invoke_any_runner(
    tmp_path, monkeypatch, capsys
):
    def forbidden(*args, **kwargs):
        raise AssertionError("default mode attempted IO")

    monkeypatch.setattr(preflight, "load_config", forbidden)
    exit_code = preflight.main(["--output", "json"], root=tmp_path, runner=forbidden)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert exit_code == report["exit_code"] == 2
    assert report["live"] is False and report["ready"] is False
    assert {row["id"] for row in report["checks"]} == set(preflight.CHECKS)
    assert all(row["reason"] == "requires_live" for row in report["checks"])
    assert output.err == ""


def test_default_text_explains_required_real_checks(tmp_path, capsys):
    assert preflight.main([], root=tmp_path, environ={}) == 2
    output = capsys.readouterr().out
    assert "Use --live" in output
    assert "snp-judge" in output and "1024" in output
    assert "Rehearsal ready: no" in output


def test_default_cli_process_needs_no_dependencies_credentials_or_network(tmp_path):
    # Disable site-packages and forbid live side effects in the child itself;
    # pytest-socket's monkeypatches do not propagate to a subprocess.
    code = """
import runpy
import sys
from pathlib import Path

script, selected = sys.argv[1:]
def guard(event, args):
    if event.startswith("socket.") or event == "subprocess.Popen":
        raise RuntimeError("Default CLI attempted a live operation")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0])
        if path.name == ".env" or ".secrets" in path.parts or str(path) == selected:
            raise RuntimeError("Default CLI attempted a credential read")
sys.addaudithook(guard)
sys.argv = [script, "--output", "json", "--env-file", selected]
runpy.run_path(script, run_name="__main__")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            code,
            str(Path(preflight.__file__).resolve()),
            str(tmp_path / "credentials.env"),
        ],
        cwd=tmp_path,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["live"] is False and report["ready"] is False
    assert all(row["reason"] == "requires_live" for row in report["checks"])
    assert result.stderr == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ["--timeout", "0"],
        ["--timeout", "-1"],
        ["--timeout", "301"],
        ["--timeout", "NaN"],
        ["--timeout", "inf"],
        ["--timeout", MASTER],
        ["--department", "all"],
        ["--department", ""],
        ["--department", MASTER],
        ["--query", " "],
        ["--query", "x" * 2001],
        ["--unknown", MASTER],
    ],
)
def test_invalid_arguments_fail_without_echo(arguments, tmp_path, capsys):
    assert preflight.main(["--output", "json", *arguments], root=tmp_path) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["ready"] is False
    assert "invalid_arguments" in output.out
    assert_sanitized(output.out + output.err)


def test_config_precedence_local_urls_and_no_environment_mutation(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LITELLM_MASTER_KEY=file-key\nSCOUT_AUTH_HEADER=Bearer file-token\n"
        "LITELLM_PORT=4200\nSCOUT_PORT=8100\nHOST_SYNC_PORT=9100\n"
        "LITELLM_BASE_URL=http://litellm:4000/v1\nCUSTOM_VALUE=file-value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PREFLIGHT_AMBIENT_SENTINEL", "unchanged")
    before = dict(os.environ)
    env = {
        **synthetic_env(),
        "LITELLM_PORT": "4300",
        "CUSTOM_VALUE": "environment-value",
    }
    result = preflight.load_config(
        root=tmp_path, environ=env, department="ai_eng", query="synthetic", timeout=9
    )
    assert result.settings.master_key == MASTER
    assert result.settings.scout_header == HEADER
    assert result.settings.gateway_url == "http://127.0.0.1:4300/v1"
    assert result.settings.scout_url == "http://127.0.0.1:8100/mcp"
    assert result.settings.host_sync_url == "http://127.0.0.1:9100/ready"
    assert result.environment["CUSTOM_VALUE"] == "environment-value"
    assert result.settings.department == "ai_eng" and result.settings.timeout == 9
    assert dict(os.environ) == before
    assert_sanitized(result)
    assert_sanitized(result.settings)


def test_actual_process_environment_overrides_selected_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / "chosen.env"
    env_file.write_text(
        "LITELLM_MASTER_KEY=from-file\nSCOUT_AUTH_HEADER=Bearer from-file\n"
    )
    for name, value in synthetic_env().items():
        monkeypatch.setenv(name, value)
    before = dict(os.environ)
    result = preflight.load_config(root=tmp_path, env_file=env_file)
    assert (
        result.settings.master_key == MASTER and result.settings.scout_header == HEADER
    )
    assert dict(os.environ) == before


def test_relative_env_file_is_resolved_before_compose_changes_directory(
    tmp_path, monkeypatch
):
    caller = tmp_path / "caller"
    caller.mkdir()
    chosen = caller / "chosen.env"
    chosen.write_text(
        "LITELLM_MASTER_KEY=file-key\nSCOUT_AUTH_HEADER=Bearer file-token\n"
    )
    monkeypatch.chdir(caller)
    config = preflight.load_config(
        root=tmp_path, env_file=Path("chosen.env"), environ={}
    )
    assert config.env_file == chosen
    assert preflight._compose(config) == [
        "docker",
        "compose",
        "--env-file",
        str(chosen),
    ]


def test_dotenv_interpolation_does_not_consume_ambient_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PREFLIGHT_PRIVATE_VALUE", MASTER)
    (tmp_path / ".env").write_text("UNRELATED=${PREFLIGHT_PRIVATE_VALUE}\n")
    config = preflight.load_config(root=tmp_path, environ=synthetic_env())
    assert config.environment["UNRELATED"] == "${PREFLIGHT_PRIVATE_VALUE}"


def test_explicit_empty_environment_overrides_file_key_and_fails(tmp_path):
    (tmp_path / ".env").write_text(
        "LITELLM_MASTER_KEY=file-key\nSCOUT_AUTH_HEADER=Bearer file-token\n"
    )
    with pytest.raises(preflight.ProbeError, match="missing_configuration") as error:
        preflight.load_config(root=tmp_path, environ={"LITELLM_MASTER_KEY": ""})
    assert error.value.fields == ["LITELLM_MASTER_KEY"]


def test_token_fallback_reads_only_client_token_not_identity_map(tmp_path, monkeypatch):
    secrets = tmp_path / ".secrets"
    secrets.mkdir()
    token_file = secrets / "scout_test_token"
    token_file.write_text(TOKEN + "\n")
    (secrets / "scout_static_tokens_json").write_text(MASTER)
    original_open = Path.open
    reads = []

    def guarded(path, *args, **kwargs):
        reads.append(path)
        assert path in {tmp_path / ".env", token_file}
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = preflight.load_config(
        root=tmp_path, environ={"LITELLM_MASTER_KEY": MASTER}
    )
    assert result.settings.scout_header == HEADER
    assert token_file in reads


def test_explicit_header_avoids_fallback_read(tmp_path, monkeypatch):
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name == ".env"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert (
        preflight.load_config(
            root=tmp_path, environ=synthetic_env()
        ).settings.scout_header
        == HEADER
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("LITELLM_PORT", "0"),
        ("SCOUT_PORT", "65536"),
        ("HOST_SYNC_PORT", MASTER),
        ("SCOUT_AUTH_HEADER", TOKEN),
        ("SCOUT_AUTH_HEADER", HEADER + "\nInjected: data"),
        ("LITELLM_MASTER_KEY", MASTER + "\nInjected: data"),
        ("SCOUT_URL", "http://user:password@localhost:8080/mcp"),
        ("SCOUT_URL", "http://localhost:8080/mcp?token=" + TOKEN),
        ("SCOUT_URL", "file:///vault/page.md"),
        ("SCOUT_URL", "http://localhost:invalid"),
    ],
)
def test_invalid_config_values_are_never_echoed(key, value, tmp_path):
    with pytest.raises(preflight.ProbeError) as error:
        preflight.load_config(root=tmp_path, environ={**synthetic_env(), key: value})
    assert error.value.reason == "invalid_configuration"
    assert error.value.fields == [key]
    assert_sanitized(error.value)


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("SCOUT_URL", "http://localhost:8181", "http://localhost:8181/mcp"),
        (
            "SCOUT_BASE_URL",
            "https://scout.example.test/prefix/",
            "https://scout.example.test/prefix/mcp",
        ),
        ("SCOUT_URL", "http://127.0.0.1:8080/mcp", "http://127.0.0.1:8080/mcp"),
    ],
)
def test_scout_configured_base_url(name, value, expected, tmp_path):
    result = preflight.load_config(
        root=tmp_path, environ={**synthetic_env(), name: value}
    )
    assert result.settings.scout_url == expected


@pytest.mark.parametrize(
    "contents", ["SCOUT_AUTH_HEADER='unterminated\n", "not a valid dotenv line\n"]
)
def test_malformed_env_file_fails_without_printing_value(contents, tmp_path, capsys):
    (tmp_path / ".env").write_text(contents + PRIVATE)
    assert (
        preflight.main(
            ["--live", "--output", "json"], root=tmp_path, environ=synthetic_env()
        )
        == 2
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["checks"][0]["reason"] == "env_file_invalid"
    assert_sanitized(output.out + output.err)


def test_missing_explicit_env_file_is_configuration_failure(tmp_path):
    with pytest.raises(preflight.ProbeError, match="env_file_unreadable"):
        preflight.load_config(
            root=tmp_path, env_file=tmp_path / "missing", environ=synthetic_env()
        )


@pytest.mark.parametrize("ndjson", [False, True])
def test_compose_accepts_array_and_ndjson_and_reduces_states(config, ndjson):
    rows = compose_rows()
    text = "\n".join(map(json.dumps, rows)) if ndjson else json.dumps(rows)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(text)

    result = preflight.probe_compose(config, runner)
    assert result.status == "pass"
    assert calls[0][0] == ["docker", "compose", "ps", "--all", "--format", "json"]
    assert calls[0][1]["env"] == config.environment
    assert calls[0][1]["timeout"] == config.settings.timeout
    assert calls[0][1]["capture_output"] is True
    assert result.data["services"][0] == {
        "service": "postgres-migrate",
        "state": "exited",
        "health": "none",
        "exit_code": 0,
    }
    assert_sanitized(result)


@pytest.mark.parametrize(
    "service,field,value",
    [
        ("postgres-migrate", "ExitCode", 1),
        ("postgres-migrate", "ExitCode", False),
        ("postgres-migrate", "State", "running"),
        ("postgres", "Health", ""),
        ("scout", "Health", "unhealthy"),
        ("litellm", "Health", "starting"),
        ("host-sync", "State", "exited"),
        ("sync-job", "Health", "unhealthy"),
        ("git", "State", PRIVATE),
        ("git", "Health", {"secret": MASTER}),
    ],
)
def test_compose_requires_every_service_and_migration_success(
    config, service, field, value
):
    rows = compose_rows()
    next(row for row in rows if row["Service"] == service)[field] = value
    result = preflight.probe_compose(
        config, lambda *a, **kw: completed(json.dumps(rows))
    )
    assert result.status == "fail" and result.reason == "services_not_ready"
    assert_sanitized(result)


def test_missing_migration_is_not_assumed_success(config):
    rows = [row for row in compose_rows() if row["Service"] != "postgres-migrate"]
    result = preflight.probe_compose(
        config, lambda *a, **kw: completed(json.dumps(rows))
    )
    assert result.status == "fail"
    assert result.data["services"][0]["state"] == "missing"


@pytest.mark.parametrize(
    "stdout,returncode,reason",
    [
        (PRIVATE, 1, "compose_failed"),
        (PRIVATE, 0, "invalid_response"),
        ("null", 0, "invalid_response"),
        ("[]", 0, "services_not_ready"),
    ],
)
def test_compose_errors_are_sanitized(config, stdout, returncode, reason):
    result = preflight.probe_compose(
        config, lambda *a, **kw: completed(stdout, returncode)
    )
    assert result.reason == reason
    assert_sanitized(result)


@pytest.mark.parametrize(
    "returncode,reason",
    [(0, "verified"), (1, "marker_missing"), (125, "compose_failed")],
)
def test_sync_readiness_uses_only_in_container_marker(
    config, monkeypatch, returncode, reason
):
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Host filesystem is not container readiness evidence")

    def runner(command, **kwargs):
        calls.append(command)
        return completed(PRIVATE, returncode)

    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "is_file", forbidden)
    result = preflight.probe_sync_ready(config, runner)
    assert calls == [
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "sync-job",
            "test",
            "-f",
            "/tmp/snp-sync-job/ready",
        ]
    ]
    assert result.reason == reason
    assert_sanitized(result)


def test_embedding_request_uses_synthetic_input_and_exact_route(settings):
    seen = []

    def request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return {"data": [{"embedding": [0.1] * 1024}], "untrusted": PRIVATE}

    result = preflight.probe_embedding(settings, request)
    assert result.status == "pass"
    assert result.data == {"vectors": 1, "dimensions": 1024, "finite": True}
    method, url, kwargs = seen[0]
    assert method == "POST" and url == settings.gateway_url + "/embeddings"
    assert kwargs["payload"] == {
        "model": "snp-embed",
        "input": ["SNP synthetic rehearsal probe."],
        "dimensions": 1024,
    }
    assert kwargs["headers"] == {"Authorization": "Bearer " + MASTER}
    assert_sanitized(result)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": []},
        {"data": [None]},
        {"data": [{"embedding": []}]},
        {"data": [{"embedding": [0] * 1023}]},
        {"data": [{"embedding": [0] * 1025}]},
        {"data": [{"embedding": [0] * 1024}, {"embedding": [0] * 1024}]},
        *[
            {"data": [{"embedding": [value] + [0] * 1023}]}
            for value in (
                True,
                False,
                "0.1",
                None,
                float("nan"),
                float("inf"),
                -float("inf"),
                10**400,
            )
        ],
    ],
)
def test_malformed_empty_and_nonfinite_vectors_fail(settings, payload):
    result = preflight.probe_embedding(settings, lambda *a, **kw: payload)
    assert result.status == "fail" and result.reason == "invalid_embedding"


def test_generation_calls_snp_llm_and_does_not_echo_output(settings):
    def request(method, url, **kwargs):
        assert method == "POST" and url.endswith("/chat/completions")
        assert kwargs["payload"]["model"] == "snp-llm"
        assert kwargs["payload"]["messages"] == [
            {"role": "user", "content": "Reply only OK"}
        ]
        # The configured Gemini route can consume a small limit before it
        # emits text, returning null even though the provider is healthy.
        assert kwargs["payload"]["max_tokens"] >= 512
        return {"choices": [{"message": {"content": "OK"}}], "debug": MASTER}

    result = preflight.probe_generation(settings, request)
    assert result.status == "pass" and result.data == {"responded_ok": True}
    assert_sanitized(result)


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"choices": []}, "invalid_generation"),
        ({"choices": [{}]}, "invalid_generation"),
        ({"choices": [{"message": {"content": ""}}]}, "invalid_generation"),
        ({"choices": [{"message": {"content": None}}]}, "invalid_generation"),
        ({"choices": [{"message": {"content": []}}]}, "invalid_generation"),
        ({"choices": [{"message": {"content": PRIVATE}}]}, "unexpected_generation"),
    ],
)
def test_generation_rejects_missing_or_unexpected_answer(settings, payload, reason):
    result = preflight.probe_generation(settings, lambda *a, **kw: payload)
    assert result.reason == reason and result.status == "fail"
    assert_sanitized(result)


@pytest.mark.parametrize(
    "code,reason",
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (429, "rate_limited"),
        (503, "upstream_error"),
        (404, "http_error"),
    ],
)
def test_http_failure_does_not_expose_error_body_headers_or_exception(
    settings, code, reason
):
    request = request_with_response({"error": MASTER + HEADER + PRIVATE}, status=code)
    result = preflight.probe_embedding(settings, request)
    assert result.reason == reason and result.status == "fail"
    assert_sanitized(result)


def test_http_transport_rejects_redirects_and_malformed_json(settings):
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"Location": "https://unexpected.test/"}, request=request
        )

    with pytest.raises(httpx.HTTPStatusError):
        preflight.request_json(
            "GET",
            settings.host_sync_url,
            timeout=1,
            transport=httpx.MockTransport(redirect),
        )
    assert len(calls) == 1
    with pytest.raises(json.JSONDecodeError):
        preflight.request_json(
            "GET",
            settings.host_sync_url,
            timeout=1,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=PRIVATE, request=req)
            ),
        )


@pytest.mark.parametrize(
    "override,passed",
    [
        ({}, True),
        ({"published_commit": None}, False),
        ({"published_commit": PRIVATE}, False),
        ({"status": "degraded", "last_error": PRIVATE}, False),
        ({"status": "not_ready"}, False),
        ({"last_error": MASTER}, False),
    ],
)
def test_host_ready_requires_published_commit_and_no_error(settings, override, passed):
    payload = {
        "status": "ready",
        "published_commit": "b" * 40,
        "last_error": None,
        **override,
    }
    result = preflight.probe_host_sync(settings, request_with_response(payload))
    assert (result.status == "pass") is passed
    assert_sanitized(result)


def test_host_ready_missing_last_error_field_is_unverified(settings):
    result = preflight.probe_host_sync(
        settings,
        request_with_response({"status": "ready", "published_commit": "b" * 40}),
    )
    assert result.status == "fail" and result.data["no_error"] is False


@pytest.fixture
def groundedness(monkeypatch):
    # The legacy module loads root/.env at import time; keep these offline tests
    # from reading any actual credential, while exercising its real typed parser.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    return importlib.import_module("scripts.verify_groundedness")


def test_real_judge_adapter_consumes_actual_typed_judgment(
    settings, groundedness, monkeypatch
):
    calls = []

    def reply(request, *, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        assert payload["model"] == "snp-judge"
        assert timeout == settings.timeout
        assert preflight.SYNTHETIC_FACT in payload["messages"][1]["content"]
        assert "synthetic-control" in payload["messages"][1]["content"]
        assert_sanitized(payload)
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"verdict": "supported", "unsupported_claims": []}
                            )
                        }
                    }
                ]
            }
        ).encode()

    monkeypatch.setattr(groundedness, "urlopen_with_retry", reply)
    result = preflight.probe_judge(settings)
    assert result.status == "pass"
    assert result.data == {"typed_result": True, "supported": True}
    assert len(calls) == 1
    assert_sanitized(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "unsupported", "unsupported_claims": []},
        {
            "verdict": "supported",
            "unsupported_claims": [{"sentence": PRIVATE, "reason": MASTER}],
        },
    ],
)
def test_judge_negative_control_rejects_unsupported_and_contradictory_results(
    settings, groundedness, monkeypatch, payload
):
    def reply(*args, **kwargs):
        return json.dumps(
            {"choices": [{"message": {"content": json.dumps(payload)}}]}
        ).encode()

    monkeypatch.setattr(groundedness, "urlopen_with_retry", reply)
    result = preflight.probe_judge(settings)
    assert result.status == "fail" and result.reason == "judge_rejected_control"
    assert_sanitized(result)


@pytest.mark.parametrize(
    "result",
    [
        None,
        SimpleNamespace(verdict="supported"),
        SimpleNamespace(unsupported=False, claims=()),
    ],
)
def test_untyped_or_missing_judge_verdict_never_passes(settings, groundedness, result):
    check = preflight.probe_judge(settings, lambda **kw: lambda **call: result)
    assert check.status == "fail" and check.reason == "invalid_judge"


def test_judge_gateway_failure_is_sanitized_and_classified(
    settings, groundedness, monkeypatch
):
    def fail(*args, **kwargs):
        raise http_error(401)

    monkeypatch.setattr(groundedness, "urlopen_with_retry", fail)
    result = preflight.probe_judge(settings)
    assert result.reason == "unauthorized" and result.status == "fail"
    assert_sanitized(result)


@pytest.mark.parametrize("claims", [None, [], ""])
def test_judge_typed_result_still_requires_valid_claims_field(
    settings, groundedness, claims
):
    result = groundedness.Judgment(unsupported=False, claims=claims)
    check = preflight.probe_judge(settings, lambda **kw: lambda **call: result)
    assert check.status == "fail" and check.reason == "invalid_judge"


def test_scout_transport_uses_auth_header_without_redirects_or_ambient_proxy(
    settings, tmp_path, monkeypatch
):
    # FastMCP settings otherwise read cwd/.env at import time. Use no real
    # credentials when constructing the installed SDK's transport offline.
    monkeypatch.setenv("FASTMCP_ENV_FILE", str(tmp_path / "absent.env"))
    client = preflight.scout_client(settings)
    transport = client.transport
    assert transport.url == settings.scout_url
    assert transport.headers == {"Authorization": HEADER}
    assert transport.auth is None
    observed = {}

    def factory(**kwargs):
        observed.update(kwargs)
        return None

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    transport.httpx_client_factory(headers=transport.headers, follow_redirects=True)
    assert observed == {
        "headers": {"Authorization": HEADER},
        "follow_redirects": False,
        "timeout": settings.timeout,
        "trust_env": False,
    }


async def test_scout_search_before_same_scope_tldr_uses_raw_envelope(settings):
    client = FakeScout()
    result = await preflight.probe_scout(settings, lambda config: client)
    assert [check.status for check in result] == ["pass", "pass", "pass"]
    assert client.calls == [
        ("list_tools",),
        (
            "wiki_search",
            {"query": "OpenShift", "department": "infra", "k": 5, "seen": []},
            {"raise_on_error": False},
        ),
        (
            "wiki_read",
            {"path": PAGE, "department": "infra", "mode": "tldr"},
            {"raise_on_error": False},
        ),
    ]
    assert result[-1].data == {"canonical_valid": True}
    assert_sanitized(result)


async def test_scope_override_is_identical_in_search_and_read(settings):
    client = FakeScout()
    result = await preflight.probe_scout(
        replace(settings, department="ai_eng"), lambda config: client
    )
    assert all(check.status == "pass" for check in result)
    assert (
        client.calls[1][1]["department"] == client.calls[2][1]["department"] == "ai_eng"
    )


@pytest.mark.parametrize("code", [401, 403])
async def test_unauthorized_scout_stops_before_retrieval(settings, code):
    client = FakeScout(enter_error=ExceptionGroup(PRIVATE, [http_error(code)]))
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[0].reason == "unauthorized"
    assert [check.status for check in result] == ["fail", "skip", "skip"]
    assert client.calls == []
    assert_sanitized(result)


async def test_tools_must_be_exact_scout_pair(settings):
    client = FakeScout(
        tools=[SimpleNamespace(name="wiki_search"), SimpleNamespace(name=MASTER)]
    )
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[0].reason == "unexpected_tools"
    assert client.calls == [("list_tools",)]
    assert_sanitized(result)


async def test_degraded_search_reports_failure_but_validates_canonical_read(settings):
    client = FakeScout(search=mcp_result(search_envelope(degraded=True)))
    result = await preflight.probe_scout(settings, lambda config: client)
    assert [check.status for check in result] == ["pass", "fail", "pass"]
    assert (
        result[1].reason == "degraded_search" and result[1].data["degraded_count"] == 1
    )
    assert client.calls[-1][0] == "wiki_read"
    assert_sanitized(result)


async def test_empty_search_cannot_certify_readiness(settings):
    client = FakeScout(
        search=mcp_result(
            {"results": [], "returned": 0, "suppressed_as_seen": 0, "has_more": False}
        )
    )
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[1].reason == "no_results" and result[2].status == "skip"
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "override",
    [
        {"returned": True},
        {"returned": 2},
        {"suppressed_as_seen": True},
        {"suppressed_as_seen": 1},
        {"has_more": True},
        {"has_more": None},
        {"results": None},
    ],
)
async def test_malformed_search_envelope_stops_read(settings, override):
    client = FakeScout(search=mcp_result({**search_envelope(), **override}))
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[1].reason == "invalid_search" and result[2].status == "skip"
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "key,value",
    [
        ("score", float("nan")),
        ("score", True),
        ("path", "../page.md"),
        ("path", "/vault/page.md"),
        ("path", ""),
        ("degraded", "false"),
        ("seen", True),
        ("snippet", None),
    ],
)
async def test_bad_search_hit_is_not_a_canonical_read_route(settings, key, value):
    data = search_envelope()
    data["results"][0][key] = value
    client = FakeScout(search=mcp_result(data))
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[1].reason == "invalid_search" and result[2].status == "skip"


async def test_scout_tool_error_does_not_echo_untrusted_error_content(settings):
    client = FakeScout(search=mcp_result({"error": PRIVATE + HEADER}, error=True))
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[1].reason == "tool_error" and result[2].status == "skip"
    assert_sanitized(result)


async def test_missing_structured_content_is_not_replaced_by_snippet_or_sdk_data(
    settings,
):
    client = FakeScout(
        search=SimpleNamespace(
            is_error=False, structured_content=None, data=search_envelope()
        )
    )
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[1].reason == "invalid_response" and result[2].status == "skip"


@pytest.mark.parametrize(
    "override",
    [
        {"path": "concepts/other.md"},
        {"title": ""},
        {"type": None},
        {"tldr": ""},
        {"content_hash": ""},
        {"content_hash": "fake-hash"},
        {"sections": None},
    ],
)
async def test_read_rejects_wrong_path_empty_or_malformed_canonical_envelope(
    settings, override
):
    data = {**read_envelope(), **override}
    # Populate SDK data separately: the raw envelope is what must be checked.
    client = FakeScout(
        read=SimpleNamespace(
            is_error=False, structured_content=data, data=SimpleNamespace()
        )
    )
    result = await preflight.probe_scout(settings, lambda config: client)
    assert result[2].reason == "invalid_read" and result[2].status == "fail"
    assert_sanitized(result)


async def test_read_auth_failure_is_not_reported_as_a_content_finding(settings):
    result = await preflight.probe_scout(
        settings, lambda config: FakeScout(read_error=http_error(403))
    )
    assert result[2].reason == "unauthorized"
    assert_sanitized(result)


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            subprocess.TimeoutExpired(["hidden"], 30, output=MASTER, stderr=HEADER),
            "timeout",
        ),
        (TimeoutError(PRIVATE), "timeout"),
        (httpx.ConnectError(MASTER), "connection_error"),
        (FileNotFoundError(MASTER), "dependency_unavailable"),
        (RuntimeError(PRIVATE), "probe_failed"),
    ],
)
def test_worker_failures_classified_without_strings(config, error, reason):
    def runner(*args, **kwargs):
        raise error

    result = preflight.run_network_probe(config, "judge", runner)
    assert result[0].reason == reason and result[0].status == "fail"
    assert_sanitized(result)


def test_worker_keeps_secrets_off_argv_and_bounds_runtime(config):
    def runner(command, **kwargs):
        assert_sanitized(command)
        assert kwargs["timeout"] == 30
        payload = json.loads(kwargs["input"])
        assert payload["live"] is True
        assert payload["settings"] == {
            "gateway_url": config.settings.gateway_url,
            "master_key": MASTER,
            "timeout": 30,
        }
        assert kwargs["capture_output"] is True
        return completed(
            json.dumps(
                [
                    asdict(
                        preflight.Check(
                            "judge",
                            "pass",
                            "verified",
                            {"typed_result": True, "supported": True},
                        )
                    )
                ]
            )
        )

    assert preflight.run_network_probe(config, "judge", runner)[0].status == "pass"


@pytest.mark.parametrize(
    "bad",
    [
        [{"id": "judge", "status": "pass", "reason": MASTER, "data": {}}],
        [
            {
                "id": "judge",
                "status": "pass",
                "reason": "verified",
                "data": {"trace": MASTER},
            }
        ],
        [
            {
                "id": "judge",
                "status": "pass",
                "reason": "verified",
                "data": {"supported": MASTER},
            }
        ],
        [
            {
                "id": "judge",
                "status": "pass",
                "reason": "judge_rejected_control",
                "data": {},
            }
        ],
    ],
)
def test_worker_output_whitelist_rejects_leaks_and_false_positive(config, bad):
    result = preflight.run_network_probe(
        config, "judge", lambda *a, **kw: completed(json.dumps(bad))
    )
    assert result[0].status == "fail" and result[0].reason == "invalid_worker_response"
    assert_sanitized(result)


def test_worker_library_log_and_stderr_are_not_forwarded(config):
    result = preflight.run_network_probe(
        config, "judge", lambda *a, **kw: completed(PRIVATE + MASTER)
    )
    assert result[0].status == "fail"
    assert_sanitized(result)


def test_missing_httpx_dependency_still_emits_sanitized_json(
    tmp_path, monkeypatch, capsys
):
    def missing(*args, **kwargs):
        raise ImportError(MASTER + PRIVATE)

    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.setattr(preflight, "load_config", missing)
    assert preflight.main(["--live", "--output", "json"], root=tmp_path) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["checks"][0]["reason"] == "dependency_unavailable"
    assert output.err == ""
    assert_sanitized(output.out)


def test_internal_worker_requires_explicit_live_request(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Worker without live opt-in must not invoke a probe")

    monkeypatch.setattr(preflight, "probe_judge", forbidden)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"live": False})))
    assert preflight._network_worker("judge") == 2
    assert capsys.readouterr().out == ""


def test_internal_worker_emits_only_structured_probe_summary(
    settings, monkeypatch, capsys
):
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"live": True, "settings": asdict(settings)})),
    )
    monkeypatch.setattr(
        preflight,
        "probe_judge",
        lambda value: preflight.Check(
            "judge", "pass", "verified", {"typed_result": True, "supported": True}
        ),
    )
    assert preflight._network_worker("judge") == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == [
        {
            "id": "judge",
            "status": "pass",
            "reason": "verified",
            "data": {"typed_result": True, "supported": True},
        }
    ]
    assert output.err == ""
    assert_sanitized(output.out)


class OfflineRunner:
    """A supplied fixture, never evidence about a live stack."""

    def __init__(
        self, *, marker_exit: int = 0, failure_kind: str | None = None
    ) -> None:
        self.marker_exit = marker_exit
        self.failure_kind = failure_kind
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[:2] == ["docker", "compose"]:
            if "ps" in command:
                return completed(json.dumps(compose_rows()))
            return completed(returncode=self.marker_exit)
        kind = next(
            kind
            for kind in preflight.NETWORK_CHECKS
            if f"_network_worker({kind!r})" in command[-1]
        )
        checks = [
            preflight.Check(name, "pass", "verified")
            for name in preflight.NETWORK_CHECKS[kind]
        ]
        if kind == self.failure_kind:
            checks[0] = preflight.Check(checks[0].id, "fail", "unauthorized")
        return completed(json.dumps([asdict(check) for check in checks]))


@pytest.mark.parametrize(
    "failure_kind", [None, "embedding", "generation", "judge", "scout", "host_sync"]
)
def test_cli_exit_and_ready_conjunction_of_all_required_checks(
    tmp_path, capsys, failure_kind
):
    runner = OfflineRunner(failure_kind=failure_kind)
    before = dict(os.environ)
    result = preflight.main(
        ["--live", "--output", "json"],
        root=tmp_path,
        environ=synthetic_env(),
        runner=runner,
    )
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert result == report["exit_code"] == (0 if failure_kind is None else 1)
    assert report["ready"] is (failure_kind is None)
    assert report["live"] is True
    assert len(runner.calls) == 7
    assert {row["id"] for row in report["checks"]} == {
        "configuration",
        *preflight.CHECKS,
    }
    assert dict(os.environ) == before
    assert_sanitized(output.out + output.err)


def test_live_marker_negative_control_cannot_pass_with_mock_healthy_services(
    tmp_path, capsys
):
    result = preflight.main(
        ["--live", "--output", "json"],
        root=tmp_path,
        environ=synthetic_env(),
        runner=OfflineRunner(marker_exit=1),
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 1 and report["ready"] is False
    assert (
        next(row for row in report["checks"] if row["id"] == "sync_ready")["reason"]
        == "marker_missing"
    )


def test_missing_credentials_exits_before_any_docker_or_network(tmp_path, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing configuration must not run a live probe")

    assert (
        preflight.main(
            ["--live", "--output", "json"], root=tmp_path, environ={}, runner=forbidden
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    assert report["checks"][0]["data"]["fields"] == [
        "LITELLM_MASTER_KEY",
        "SCOUT_AUTH_HEADER",
    ]


def test_secret_detection_oracle_fails_on_known_leak_before_trusting_absence():
    with pytest.raises(AssertionError):
        assert_sanitized({"error": MASTER})
    assert_sanitized(preflight.Check("judge", "fail", "probe_failed"))
