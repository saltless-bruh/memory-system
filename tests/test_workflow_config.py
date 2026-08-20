"""Structural tests for security-sensitive automation workflows."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_WORKFLOW = REPO_ROOT / ".gitea" / "workflows" / "security.yaml"
HEALER_WORKFLOW = REPO_ROOT / ".gitea" / "workflows" / "auto-healer.yaml"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
INTEGRATION_COMPOSE = REPO_ROOT / "docker-compose.integration.yml"


def _security_workflow() -> str:
    return SECURITY_WORKFLOW.read_text(encoding="utf-8")


def test_security_workflow_checks_out_complete_history_read_only() -> None:
    content = _security_workflow()

    assert "fetch-depth: 0" in content
    assert "persist-credentials: false" in content
    assert re.search(r"permissions:\n\s+contents: read", content)
    assert "contents: write" not in content


def test_security_workflow_scans_all_current_state_before_history_scanner() -> None:
    content = _security_workflow()
    custom_scan = "scan_secrets.py --all-current --history"
    gitleaks_scan = 'git "--log-opts=--all --root" --redact --no-banner .'

    assert custom_scan in content
    assert gitleaks_scan in content
    assert content.index(custom_scan) < content.index(gitleaks_scan)


def test_security_workflow_pins_gitleaks_by_version_and_digest() -> None:
    content = _security_workflow()

    assert re.search(
        r"ghcr\.io/gitleaks/gitleaks:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}",
        content,
    )
    assert '"--log-opts=--all --root"' in content
    assert "--redact" in content


def test_gitleaks_has_no_broad_password_or_fixture_allowlist() -> None:
    content = GITLEAKS_CONFIG.read_text(encoding="utf-8")

    forbidden_exemptions = (
        "rag_app_secret",
        "postgres_master_secret",
        "tests/fixtures",
    )
    assert all(exemption not in content for exemption in forbidden_exemptions)
    assert 'condition = "AND"' in content
    assert "^\\.env\\.example$" in content
    assert "^docker-compose\\.yml$" in content


def test_healer_workflow_uses_closed_loop_gate_for_both_modes() -> None:
    content = HEALER_WORKFLOW.read_text(encoding="utf-8")
    assert "ci_address_gate.py --mode pr" in content
    assert "ci_address_gate.py --mode scheduled --branch" in content
    assert "if ! uv run python scripts/verify_addresses.py" not in content
    assert "scout/healer.py --ci" not in content
    assert "scout/healer.py --push" not in content
    assert "DRIFT_DETECTED" not in content


def test_pr_healer_executes_only_immutable_trusted_base_code() -> None:
    content = HEALER_WORKFLOW.read_text(encoding="utf-8")
    pr_job = content[content.index("  pr-heal:") : content.index("  scheduled-sweep:")]

    assert "ref: ${{ github.event.pull_request.base.sha }}" in pr_job
    assert "path: trusted" in pr_job
    assert "ref: ${{ github.event.pull_request.head.sha }}" in pr_job
    assert "path: pr-source" in pr_job
    assert "materialize_pr_wiki.py import" in pr_job
    assert "--base-sha ${{ github.event.pull_request.base.sha }}" in pr_job
    assert "--head-sha ${{ github.event.pull_request.head.sha }}" in pr_job
    assert "working-directory: trusted" in pr_job
    assert "uv sync --frozen --extra dev" in pr_job
    assert "working-directory: pr-source" in pr_job

    # PR-controlled Python, dependency metadata, actions, and shell scripts are
    # never invoked. The PR checkout is used only as untrusted Git data and as
    # the final destination for reviewed Markdown bytes.
    assert "working-directory: pr-source\n        run: uv" not in pr_job
    assert "working-directory: pr-source\n        run: python" not in pr_job
    assert "ref: ${{ github.head_ref }}" not in pr_job
    assert "uv sync --extra dev" not in pr_job
    assert "curl" not in pr_job
    assert 'git push origin "HEAD:${PR_HEAD_REF}"' in pr_job
    assert 'git push origin "HEAD:${{ github.head_ref }}"' not in pr_job


def test_healer_workflow_uses_query_role_without_superuser_fallback() -> None:
    content = HEALER_WORKFLOW.read_text(encoding="utf-8")
    assert content.count("POSTGRES_QUERY_USER: rag_app_role") == 2
    assert content.count("POSTGRES_QUERY_PASSWORD:") == 2
    assert "postgres_master_secret" not in content
    assert "POSTGRES_USER:" not in content
    assert "POSTGRES_PASSWORD:" not in content


def test_scheduled_workflow_opens_pr_only_after_tested_gate() -> None:
    content = HEALER_WORKFLOW.read_text(encoding="utf-8")
    gate = content.index("ci_address_gate.py --mode scheduled")
    push_is_inside_gate = "git push" not in content[gate:]
    pull_request = content.index("/pulls", gate)
    assert push_is_inside_gate
    assert gate < pull_request
    assert "if: env.HEAL_CREATED == 'true'" in content


def test_basic_memory_has_no_model_gateway_secret_or_host_network() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["basic-memory"]

    assert "network_mode" not in service
    assert service["ports"] == ["127.0.0.1:${BASIC_MEMORY_PORT:-8765}:8765"]
    assert "environment" not in service
    assert service["volumes"][0] == "vault-replica:/vault-replica:ro"


def test_agent_facing_services_have_bounded_readiness_checks() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for service_name in ("scout", "basic-memory"):
        healthcheck = compose["services"][service_name]["healthcheck"]
        assert healthcheck["timeout"] == "3s"
        assert healthcheck["retries"] >= 6
        assert healthcheck["start_period"]


def test_integration_host_sync_reads_local_seed_but_publishes_to_replica() -> None:
    compose = yaml.safe_load(INTEGRATION_COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["host-sync"]

    assert service["environment"]["GIT_SYNC_URL"] == "file:///source-repo"
    assert service["environment"]["GIT_CONFIG_GLOBAL"] == (
        "/etc/snp/integration.gitconfig"
    )
    assert service["volumes"] == [
        "./:/source-repo:ro",
        "./config/host-sync.integration.gitconfig:/etc/snp/integration.gitconfig:ro",
    ]
    assert compose["services"]["postgres"]["ports"] == [
        "127.0.0.1:${SNP_INTEGRATION_POSTGRES_PORT:-55432}:5432"
    ]
