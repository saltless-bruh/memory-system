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


def test_no_service_embeds_outside_the_litellm_gateway() -> None:
    """One embedding engine, structurally.

    Was a string check on the basic-memory comment block. That service embedded
    in-process with FastEmbed at 384 dimensions against a 1024-dimension index,
    so identical queries returned different orderings -- two vector spaces, no
    error raised. V3 removed it. Asserting on the service list instead of on
    comment prose means the guard survives a rewording.
    """
    import yaml

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    assert "basic-memory" not in compose["services"]
