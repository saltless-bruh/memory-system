"""Every `SKILL.md` in this repository, held to the Agent Skills specification.

Agent Skills is an open standard, and the reason to conform is not tidiness: a
skill that fails these rules does not *load*. The name must match its directory
or the runtime cannot find it; frontmatter that is not valid YAML is not a
degraded skill but an absent one, and this repository shipped six of those.

The rules encoded here are the normative ones from the specification:

* `name` — 1–64 characters, lowercase alphanumeric and hyphens only, no leading
  or trailing hyphen, no consecutive hyphens, and **equal to the parent
  directory name**.
* `description` — 1–1024 characters, non-empty, saying what the skill does and
  when to use it.
* the frontmatter must parse as strict YAML.
* **no `<` or `>` anywhere in the frontmatter.** The specification calls this
  out as a safety rule: angle brackets there can inject unintended instructions
  into the system prompt. It is also why a `>-` folded scalar — the obvious way
  to fix a description containing `: ` — is the wrong fix here.
* only specification-defined keys, so a typo cannot silently become metadata a
  conformant runtime ignores.

`skills-ref` exists and would check most of this, but its own authors describe
it as "intended for demonstration purposes only and not meant to be used in
production", so the rules are encoded here instead — the same choice this repo
made for the CLI Spec, where the schema is vendored rather than the tooling
imported.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every place a skill lives. `.agent/` is the authoritative contract,
#: `.claude/` mirrors it for Claude Code, and `packages/snp-agent/` is the
#: portable distribution. A rule that held in only one of them would be a rule
#: that lets a broken skill ship.
SKILL_ROOTS = (
    REPO_ROOT / ".agent" / "skills",
    REPO_ROOT / ".claude" / "skills",
    REPO_ROOT / "packages" / "snp-agent" / "skills",
)

#: Specification frontmatter keys. `name` and `description` are required; the
#: rest are optional and everything else is unrecognised.
KNOWN_KEYS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)

_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _skills() -> list[Path]:
    found = [
        skill
        for root in SKILL_ROOTS
        if root.is_dir()
        for skill in sorted(root.glob("*/SKILL.md"))
    ]
    assert found, "no SKILL.md files found — the roots moved"
    return found


def _frontmatter_text(skill: Path) -> str:
    match = _FRONTMATTER.match(skill.read_text(encoding="utf-8"))
    assert match is not None, f"{skill} has no `---` frontmatter block"
    return match.group(1)


def _rel(skill: Path) -> str:
    return skill.relative_to(REPO_ROOT).as_posix()


@pytest.mark.parametrize("skill", _skills(), ids=_rel)
def test_frontmatter_parses_as_strict_yaml(skill: Path) -> None:
    """An unparseable frontmatter is an absent skill, not a degraded one."""
    parsed = yaml.safe_load(_frontmatter_text(skill))
    assert isinstance(parsed, dict), f"{_rel(skill)}: frontmatter is not a mapping"


@pytest.mark.parametrize("skill", _skills(), ids=_rel)
def test_name_is_valid_and_matches_its_directory(skill: Path) -> None:
    parsed = yaml.safe_load(_frontmatter_text(skill))
    name = parsed.get("name")

    assert isinstance(name, str) and name, f"{_rel(skill)}: missing `name`"
    assert 1 <= len(name) <= 64, f"{_rel(skill)}: name must be 1-64 characters"
    assert _NAME.match(name), (
        f"{_rel(skill)}: name {name!r} must be lowercase alphanumeric and "
        "hyphens, and must not start or end with a hyphen"
    )
    assert "--" not in name, f"{_rel(skill)}: name must not contain `--`"
    # The runtime finds a skill by its directory. A mismatch does not load.
    assert name == skill.parent.name, (
        f"{_rel(skill)}: name {name!r} must equal its directory {skill.parent.name!r}"
    )


@pytest.mark.parametrize("skill", _skills(), ids=_rel)
def test_description_is_present_and_within_the_length_limit(skill: Path) -> None:
    parsed = yaml.safe_load(_frontmatter_text(skill))
    description = parsed.get("description")

    assert isinstance(description, str), f"{_rel(skill)}: missing `description`"
    assert description.strip(), f"{_rel(skill)}: description is empty"
    assert len(description) <= 1024, (
        f"{_rel(skill)}: description is {len(description)} characters, over the "
        "1024 limit"
    )


@pytest.mark.parametrize("skill", _skills(), ids=_rel)
def test_frontmatter_carries_no_angle_brackets(skill: Path) -> None:
    """A specification safety rule, not a style preference.

    Angle brackets in frontmatter can inject unintended instructions into the
    system prompt. This is also what rules out `description: >-`, which is the
    obvious YAML fix for a value containing `: ` and the wrong one here.
    """
    text = _frontmatter_text(skill)
    offenders = [line for line in text.splitlines() if "<" in line or ">" in line]
    assert not offenders, (
        f"{_rel(skill)}: angle brackets in frontmatter: {offenders} — quote the "
        "value instead of using a folded scalar"
    )


@pytest.mark.parametrize("skill", _skills(), ids=_rel)
def test_only_specification_keys_are_declared(skill: Path) -> None:
    """A typo must not silently become metadata a conformant runtime ignores."""
    parsed = yaml.safe_load(_frontmatter_text(skill))
    unknown = sorted(set(parsed) - KNOWN_KEYS)
    assert not unknown, f"{_rel(skill)}: unrecognised frontmatter keys {unknown}"


def test_the_authoritative_and_mirrored_skill_sets_are_identical() -> None:
    """`.claude/` mirrors `.agent/`; a skill fixed in one must be fixed in both.

    Excepting the `superpowers-*` layer, which lives in `.agent/` only. The
    exclusion is imported rather than restated so that this test and
    `test_agent_package_sync` cannot disagree about what is carved out.
    """
    from test_agent_package_sync import CLAUDE_EXCLUDED_PREFIXES

    agent = {
        p.parent.name for p in (REPO_ROOT / ".agent" / "skills").glob("*/SKILL.md")
    }
    claude = {
        p.parent.name for p in (REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md")
    }
    excluded = {name for name in agent if name.startswith(CLAUDE_EXCLUDED_PREFIXES)}
    assert excluded, "the carve-out names nothing; it has become vacuous"
    assert not (claude & excluded), (
        f"excluded skills are still present under .claude/: {sorted(claude & excluded)}"
    )
    assert agent - excluded == claude
