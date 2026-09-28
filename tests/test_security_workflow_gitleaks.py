"""The gitleaks step must scan the checked-out tree, not an empty directory.

The Gitea runner (act_runner) executes each step inside a job container and
hands it the host's Docker daemon. `docker run --volume "$GITHUB_WORKSPACE/..."`
from there names a path that exists only inside the job container; the daemon
resolves it on the host, finds nothing, creates an empty directory and mounts
that. gitleaks then scans no repository and no config -- a trusted, trivial
pass the first time the runner starts (DR-26). Content therefore has to reach
the scanner container through the Docker API itself (`docker cp`), which
streams from the client side and so works whichever filesystem the daemon
sees.
"""

from __future__ import annotations

import random
import re
import tomllib
from pathlib import Path

import yaml

from scripts import scan_secrets

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_WORKFLOW = REPO_ROOT / ".gitea" / "workflows" / "security.yaml"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"


def _gitleaks_step() -> str:
    workflow = yaml.safe_load(SECURITY_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["secret-scan"]["steps"]
    (step,) = [s for s in steps if s.get("name", "").startswith("Independent Gitleaks")]
    assert step.get("if") == "always()"
    run: str = step["run"]
    return run


def test_gitleaks_step_bind_mounts_nothing_from_the_job_container() -> None:
    run = _gitleaks_step()

    assert "--volume" not in run
    assert not re.search(r"(^|\s)-v\s", run)
    assert "--mount" not in run


def test_gitleaks_step_copies_the_target_and_trusted_config_in() -> None:
    run = _gitleaks_step()

    assert re.search(r'docker cp "\$GITHUB_WORKSPACE/target/?\.?" "\$\w+:/repo"', run)
    assert "trusted-security/.gitleaks.toml" in run
    assert "--config=/trusted/gitleaks.toml" in run
    assert 'docker start --attach "$' in run
    # The container is removed on every path, and gitleaks' own exit status is
    # the step's status rather than whatever cleanup returned.
    assert "trap" in run and "docker rm" in run


def test_gitleaks_step_refuses_to_scan_an_empty_copy() -> None:
    """A tree with no history is the silent-pass state; the step must say so."""
    run = _gitleaks_step()

    assert "rev-parse" in run and "exit 1" in run


def _rule(rule_id: str) -> re.Pattern[str]:
    config = tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))
    (rule,) = [r for r in config["rules"] if r["id"] == rule_id]
    return re.compile(rule["regex"])


def test_gitleaks_carries_the_gitea_token_rule_scan_secrets_has() -> None:
    """Default gitleaks rules know no Gitea token; the two scanners stay in step."""
    rng = random.Random(3)
    token = "".join(rng.choice("0123456789abcdef") for _ in range(40))
    ours = dict(scan_secrets.SECRET_PATTERNS)["Gitea access token"]
    theirs = _rule("snp-gitea-access-token")

    positives = (
        f"GITEA_TOKEN={token}",
        f"Authorization: token {token}",
        f"http://snp-admin:{token}@127.0.0.1:3000/x.git",
    )
    negatives = (f"merged {token} onto main", f"commit: {token}")
    for sample in positives:
        assert ours.search(sample) and theirs.search(sample), sample
    for sample in negatives:
        assert not ours.search(sample) and not theirs.search(sample), sample
