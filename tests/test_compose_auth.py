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


def test_every_gitea_dependent_service_waits_for_gitea() -> None:
    """A service that fetches from Gitea must not start before Gitea is healthy.

    host-sync fires its initial sync roughly three seconds after the container
    starts, while the `git` service declares `start_period: 40s`. Without an
    ordering constraint the first fetch races Gitea's boot and loses, and the
    failure is not transient in effect: `_perform_git_sync` records the error in
    the in-process state, nothing retries it, so `/ready` reports
    `status: degraded, last_error: "git fetch failed"` until an unrelated
    webhook happens to trigger a later sync. Observed twice in the logs, each
    time within a second of "Application startup complete", against 46
    successful syncs that all came later.

    `gitea-runner` already declares exactly this dependency; host-sync was the
    only Gitea-dependent service without it.
    """
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    for name in ("host-sync", "gitea-runner"):
        depends = services[name].get("depends_on") or {}
        assert "git" in depends, f"{name} does not wait for the git service"
        assert depends["git"]["condition"] == "service_healthy", (
            f"{name} waits for git but not on its health; "
            "starting alongside a booting Gitea is what loses the race"
        )
