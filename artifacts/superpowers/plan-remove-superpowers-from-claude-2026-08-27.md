# Plan — remove the superpowers layer from Claude Code only

Date: 2026-08-27
Branch: fix/architecture-security-hardening

## Goal
Stop Claude Code from loading the superpowers rule, skills, and workflows,
while keeping the layer fully intact for Google Antigravity, which reads
`.agent/`.

## Constraint discovered
`tests/test_agent_package_sync.py::test_claude_mirrors_agent_contract`
enforces a bidirectional byte-for-byte mirror of `.agent/` -> `.claude/`
across instructions, rules, skills, workflows. Deleting the Claude-side
superpowers files fails it with 21 "not mirrored in .claude/" problems.
`.agent/plugin.json` repoLocal.note also asserts the layer lives in both
trees. This is an invariant amendment, not a plain deletion.

## Steps
1. Delete 21 files under `.claude/`: rules/superpowers.md,
   skills/superpowers-* (9 dirs, 12 files), workflows/superpowers-*.md (8).
2. Amend `test_claude_mirrors_agent_contract` to exclude the repo-local
   `superpowers-` prefix (and rules/superpowers.md) from the mirror set,
   reading the prefix from plugin.json's repoLocal rather than hardcoding.
   Lower/keep the truncation floor consistent with 25 remaining files.
3. Update repoLocal.note in `.agent/plugin.json` and
   `packages/snp-agent/plugin.json`, keeping them byte-identical.
4. Update README.md (~230-238) and docs/ARCHITECTURE_STATUS.md:18.
5. Leave `artifacts/superpowers/` in place: existing work product, and
   `.agent/rules/superpowers.md` still writes there.

## Verification
- pytest tests/test_agent_package_sync.py -v  (all pass)
- pytest tests/test_docs_contract.py tests/test_cli_install_agent.py -v
- pytest -q  (full suite, regression safety)
- diff -rq .agent/skills .claude/skills  -> only superpowers-* absent
- Confirm .agent/ superpowers file count still 21

## Acceptance
Claude Code loads no superpowers rule/skill/workflow; Antigravity's
`.agent/` layer is byte-identical to its pre-change state; suite green.
