# Implementation Plan: Install, Generalize, and Globally Deploy `skill-creator`

This plan details the steps to take the `skill-creator` skill from Anthropic, convert it from Claude-specific instructions and toolchains to an agent-agnostic / general AI coding assistant standard, and install it into the global skills repository.

## User Review Required

> [!IMPORTANT]
> - **Execution Gate**: Per Superpowers protocol, implementation will pause after plan approval until `/superpowers-execute-plan` is invoked (or explicit user approval to proceed immediately is given).
> - **Global Path Target**: The skill will be deployed to Antigravity's global customization directory (`~/.gemini/config/skills/skill-creator`) as well as mirrored to `~/.gemini/antigravity/skills/skill-creator` and `~/.agents/skills/skill-creator` to ensure full interoperability across both Antigravity and the broader agent CLI tools.

## Proposed Changes

### 1. Generalize Instructions in `SKILL.md`

#### [MODIFY] [SKILL.md](file:///home/ple/.agents/skills/skill-creator/SKILL.md)
- Replace references to "Claude", "Claude Code", and "Claude.ai" with "the AI agent", "the agent", or "agent session".
- Replace "Anthropic internal data" example with generic company data.
- Refactor the "Claude.ai-specific instructions" section to "Single-Agent / Headless Environments" (explaining how to execute the skill when subagents or GUI browsers are not available).
- Refactor "Cowork-Specific Instructions" to "Multi-Agent / Headless Environments".
- Make description optimization instructions general, providing fallback guidance when a specific CLI like `claude -p` is not present in the environment.

---

### 2. Generalize Scripts and Evaluation Tools

#### [MODIFY] [run_eval.py](file:///home/ple/.agents/skills/skill-creator/scripts/run_eval.py)
- Broaden root detection beyond `.claude/` to also check `.agent/`, `.agents/`, and `.git/`.
- Allow the evaluation runner to accept custom agent commands or fallback to generic prompt invocation rather than failing if `claude` CLI is absent.

#### [MODIFY] [improve_description.py](file:///home/ple/.agents/skills/skill-creator/scripts/improve_description.py)
- Update internal prompt strings from "a Claude Code skill" to "an AI agent skill".
- Provide configurable command execution for description improvement queries.

#### [MODIFY] [generate_report.py](file:///home/ple/.agents/skills/skill-creator/scripts/generate_report.py)
- Replace UI report strings referencing "Claude" with "the agent".

#### [MODIFY] [viewer.html](file:///home/ple/.agents/skills/skill-creator/eval-viewer/viewer.html)
- Update review prompt instructions ("paste into Claude Code" -> "share it with your AI agent", "tell Claude you're done" -> "let your agent know you are done reviewing").

---

### 3. Deploy to Global Skills Directories

- Create directory `~/.gemini/config/skills/skill-creator` (Antigravity Global Configuration directory).
- Copy all generalized files into `~/.gemini/config/skills/skill-creator/`.
- Ensure directory `~/.gemini/antigravity/skills/skill-creator` also exists and links or mirrors the generalized files.
- Update `~/.agents/skills/skill-creator/` with the generalized files to prevent conflicts when inspected by global tools.

## Verification Plan

### Automated Tests & Quality Checks
1. **Schema Validation**:
   - Run `python3 ~/.gemini/config/skills/skill-creator/scripts/quick_validate.py ~/.gemini/config/skills/skill-creator`
   - Expect: `Skill is valid!`
2. **Conflict & Keyword Check**:
   - Run grep on `~/.gemini/config/skills/skill-creator/` for case-insensitive `claude` and `anthropic`.
   - Expect: Zero matches in instructions, prompts, and scripts (excluding only legal copyright in `LICENSE.txt`).
3. **Packaging Test**:
   - Run `python3 ~/.gemini/config/skills/skill-creator/scripts/package_skill.py ~/.gemini/config/skills/skill-creator /tmp/test-dist`
   - Expect successful package creation and validation.
4. **Global Directory Verification**:
   - Verify `ls -la ~/.gemini/config/skills/skill-creator` contains all resources (`SKILL.md`, `agents/`, `eval-viewer/`, `references/`, `scripts/`, `assets/`).
