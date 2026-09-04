# Implementation Plan: Create & Globally Deploy `ascii-diagram-explainer` Skill

This plan details the creation of the `ascii-diagram-explainer` skill to empower agents to generate clear, structured, beautiful ASCII diagrams and visual explanations across workflows, architectures, code logic, and data structures, and installing it into the global skills repository.

## User Review Required

> [!IMPORTANT]
> - **Execution Gate**: Per Superpowers protocol, implementation will pause until user review and invocation of `/superpowers-execute-plan` (or explicit confirmation).
> - **Storage Scope**: The skill will be created globally under `~/.gemini/config/skills/ascii-diagram-explainer/`, mirrored to `~/.gemini/antigravity/skills/ascii-diagram-explainer/` and `~/.agents/skills/ascii-diagram-explainer/`.

## Proposed Changes

### Global Customizations Directory

#### [NEW] [SKILL.md](file:///home/ple/.gemini/config/skills/ascii-diagram-explainer/SKILL.md)
- Frontmatter containing `name: ascii-diagram-explainer` and triggering description.
- Core rules for ASCII diagrams:
  - Width constraints (max 80-90 chars to prevent wrapping).
  - Code fence styling (` ```text `).
  - Box and connector standards (`+---+`, `|`, `-->`, `v`, `^`).
  - Diagram selection guide based on explanation intent (workflow, architecture, code logic, sequence, state machine).
- Step-by-step procedure to convert an abstract explanation into a high-impact ASCII diagram.
- Pointer to `references/patterns.md` for copy-pasteable archetypes.

#### [NEW] [patterns.md](file:///home/ple/.gemini/config/skills/ascii-diagram-explainer/references/patterns.md)
- Reusable ASCII templates covering:
  1. **Flowcharts & Decision Trees**: Linear steps, branching, loops.
  2. **Architectures & Layer Stacks**: Tiered components, client-gateway-backend-DB.
  3. **Code Control Flow & Data Pipelines**: Function execution, transformation chains, stack/heap layout.
  4. **Sequence & Protocol Flows**: Message passing between entities over time.
  5. **State Machines**: States, triggers, transitions, terminal nodes.
  6. **Tree & Hierarchy Structures**: Directories, taxonomies, decision DAGs.

---

### Global Synchronization

- Mirror the new skill to `~/.gemini/antigravity/skills/ascii-diagram-explainer/` and `~/.agents/skills/ascii-diagram-explainer/`.
- Update `~/.agents/.skill-lock.json` if applicable.

## Verification Plan

### Automated Tests
1. **Schema Validation**:
   - Run `python3 ~/.gemini/config/skills/skill-creator/scripts/quick_validate.py ~/.gemini/config/skills/ascii-diagram-explainer`
   - Expect: `Skill is valid!`
2. **Packaging Test**:
   - Run `python3 ~/.gemini/config/skills/skill-creator/scripts/package_skill.py ~/.gemini/config/skills/ascii-diagram-explainer /tmp/test-pkg`
   - Expect: successful `.skill` package creation.
3. **Global Discovery**:
   - Run `npx skills ls -g` to verify registration across supported agent harnesses.
