"""Production Compose authentication wiring contracts."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_scout_static_auth_secret_is_wired_in_primary_compose() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    scout = compose["services"]["scout"]

    assert scout["environment"]["SCOUT_STATIC_TOKENS_FILE"] == (
        "/run/secrets/scout_static_tokens_json"
    )
    assert "scout_static_tokens_json" in scout["secrets"]
    assert compose["secrets"]["scout_static_tokens_json"]["file"] == (
        "./.secrets/scout_static_tokens.json"
    )


def test_basic_memory_comment_does_not_claim_gateway_embeddings() -> None:
    content = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    basic_comment = content.split("basic-memory:", 1)[0].rsplit(
        "# ── basic-memory", 1
    )[1]
    assert "Embeddings hit the LiteLLM proxy" not in basic_comment
