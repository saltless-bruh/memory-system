"""Configuration resolution, gated by what a command actually needs.

Two properties this module exists to guarantee, both of which the scripts it
replaces get wrong:

**Nothing is loaded that the command did not declare.** Importing
`scripts/verify_addresses.py` today calls `dotenv.load_dotenv()` at module
scope, which puts **thirty** variables into `os.environ` — `GEMINI_API_KEY`
among them — as a side effect of an `import` statement, where every subprocess
inherits them. Here a command declares a `Prerequisite` and receives exactly the
keys that class allows: `NONE` receives nothing at all.

**`os.environ` is never mutated.** Values are parsed with
`dotenv.dotenv_values`, which returns a mapping, and handed back as a value.
Configuration that arrives by mutating global state cannot be reasoned about,
tested in isolation, or kept out of a subprocess.

Precedence, matching the rule that an explicit answer always beats an ambient
one:

    1. explicit override   (a flag, passed by the dispatcher)
    2. process environment (already exported by the caller or by CI)
    3. project .env        (LOCAL commands only, and only inside a repository)

A user-level config file is deliberately absent; that belongs with the
installer discussion, which decides where a per-machine connection lives.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from scout.cli.errors import input_error
from scout.cli.registry import Prerequisite

#: Keys a REMOTE command may see: how to reach the servers, and who it is.
#: Deliberately excludes every provider credential and database password -- a
#: command that only calls `rag_fetch` has no business holding them.
CONNECTION_KEYS: frozenset[str] = frozenset(
    {
        "SCOUT_URL",
        "SCOUT_PORT",
        "SCOUT_AUTH_TOKEN",
        "SCOUT_AUTH_TOKEN_FILE",
        "BASIC_MEMORY_URL",
        "BASIC_MEMORY_PORT",
    }
)

#: Additional keys a LOCAL command may see: it runs against the stack itself.
REPO_KEYS: frozenset[str] = CONNECTION_KEYS | frozenset(
    {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_QUERY_USER",
        "POSTGRES_QUERY_PASSWORD",
        "POSTGRES_QUERY_PASSWORD_FILE",
        "POSTGRES_INGEST_USER",
        "POSTGRES_INGEST_PASSWORD",
        "POSTGRES_INGEST_PASSWORD_FILE",
        "POSTGRES_MIGRATION_USER",
        "POSTGRES_MIGRATION_PASSWORD",
        "POSTGRES_MIGRATION_PASSWORD_FILE",
        "LITELLM_BASE_URL",
        "LITELLM_MASTER_KEY",
        "LITELLM_EMBED_MODEL",
        "LITELLM_LLM_MODEL",
        "LITELLM_VLM_MODEL",
        "LITELLM_JUDGE_MODEL",
        "RAW_DIR",
        "RAW_ACL_FILE",
        "WIKI_DIR",
        "HEALER_BACKEND",
        "RAG_BACKEND",
    }
)

#: Substrings marking a value that must never be rendered, logged, or repr'd.
_SECRET_MARKERS = ("TOKEN", "PASSWORD", "KEY", "SECRET")

#: Files whose presence identifies a repository checkout.
_REPO_MARKERS = ("pyproject.toml", "AGENTS.md")


def _is_secret(key: str) -> bool:
    return any(marker in key.upper() for marker in _SECRET_MARKERS)


def allowed_keys(prerequisite: Prerequisite) -> frozenset[str]:
    """The keys a command of this class may receive."""
    if prerequisite is Prerequisite.NONE:
        return frozenset()
    if prerequisite is Prerequisite.REMOTE:
        return CONNECTION_KEYS
    return REPO_KEYS


def find_repo_root(start: Path | None = None) -> Path | None:
    """Locate the enclosing repository checkout, if there is one."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if all((candidate / marker).exists() for marker in _REPO_MARKERS):
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved configuration for one command.

    Holds only the keys its `prerequisite` allows. `repo_root` is `None` when
    the command was run outside a checkout, which is legitimate for a REMOTE
    command and fatal for a LOCAL one.
    """

    prerequisite: Prerequisite
    values: Mapping[str, str] = field(default_factory=dict)
    repo_root: Path | None = None

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def require(self, key: str) -> str:
        """Return `key`, or refuse with a hint naming what to supply."""
        value = self.values.get(key)
        if value:
            return value
        if key not in allowed_keys(self.prerequisite):
            raise input_error(
                f"{key} is not available to a {self.prerequisite.value} command",
                hint="declare the command with a broader prerequisite",
                key=key,
            )
        raise input_error(
            f"{key} is not configured",
            hint=f"export {key}, or set it in the project .env",
            key=key,
        )

    def require_repo(self) -> Path:
        """Return the checkout root, or explain that this needs one."""
        if self.repo_root is None:
            raise input_error(
                "this command needs a repository checkout",
                hint="run it from inside a clone of the memory-system repository",
            )
        return self.repo_root

    def __repr__(self) -> str:
        """Redacted by construction, so a traceback cannot spill a credential."""
        shown = {
            key: ("<redacted>" if _is_secret(key) else value)
            for key, value in sorted(self.values.items())
        }
        return (
            f"Config(prerequisite={self.prerequisite.value!r}, "
            f"repo_root={self.repo_root}, values={shown})"
        )


def resolve(
    prerequisite: Prerequisite,
    *,
    overrides: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    start: Path | None = None,
) -> Config:
    """Resolve configuration for one command, loading nothing it did not ask for.

    Args:
        prerequisite: The command's declared class. `NONE` short-circuits before
            any file is touched.
        overrides: Values from explicit flags. Highest precedence.
        environ: The process environment. Defaults to `os.environ`, read but
            never written.
        start: Where to begin searching for a checkout.

    Returns:
        A `Config` holding only permitted keys.
    """
    if prerequisite is Prerequisite.NONE:
        # Nothing is read: no environment, no file, no filesystem walk. This is
        # what lets `snpmemory schema` answer on a machine with no credentials,
        # no database, and no configuration at all.
        return Config(prerequisite=prerequisite)

    permitted = allowed_keys(prerequisite)
    source = os.environ if environ is None else environ
    repo_root = find_repo_root(start)

    resolved: dict[str, str] = {}

    # 3. project .env -- LOCAL only. A REMOTE command may be running inside an
    #    unrelated project, where a stray .env is somebody else's configuration.
    if prerequisite is Prerequisite.LOCAL and repo_root is not None:
        env_file = repo_root / ".env"
        if env_file.is_file():
            from dotenv import dotenv_values

            for key, value in dotenv_values(env_file).items():
                if key in permitted and value:
                    resolved[key] = value

    # 2. process environment
    for key in permitted:
        value = source.get(key)
        if value:
            resolved[key] = value

    # 1. explicit overrides
    for key, value in (overrides or {}).items():
        if value:
            resolved[key] = value

    return Config(prerequisite=prerequisite, values=resolved, repo_root=repo_root)
