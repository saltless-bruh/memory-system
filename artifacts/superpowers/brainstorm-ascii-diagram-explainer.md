# Brainstorm: `ascii-diagram-explainer` Global Skill

## 1. Goal
Create a dedicated, reusable global skill (`ascii-diagram-explainer`) that implements the mandatory visual explanation rule:
"Whenever explaining anything (complex to simple), use ASCII to make graphs or diagrams to showcase it."

## 2. Constraints & Requirements
- **Location**: Global skills folder (`~/.gemini/config/skills/ascii-diagram-explainer/`), mirrored to `~/.gemini/antigravity/skills/` and `~/.agents/skills/`.
- **Validation**: Must pass `quick_validate.py` (proper frontmatter, kebab-case name, description under 1024 characters without angle brackets).
- **Format**: Concise, high-leverage `SKILL.md` (<500 lines) with a detailed `references/patterns.md` template library following progressive disclosure.
- **Scope**: Must cover all key visual explanation archetypes:
  - Workflows & Process Lifecycles
  - Architecture & Component Topologies
  - Code Execution, Control Flow & Data Pipelines
  - Protocol Sequences & Request/Response Flows
  - State Machines & Transitions
  - Data Structures & Memory/Tree Layouts

## 3. Risks & Mitigations
- **Risk**: Diagrams wrap or distort on narrow chat screens.
  - **Mitigation**: Establish explicit width rules (max 80-90 characters) and monospaced code fencing (` ```text `).
- **Risk**: Vague or under-triggering description.
  - **Mitigation**: Craft an assertive, comprehensive description specifying exact contexts (explaining workflows, code, architectures, logic).

## 4. Acceptance Criteria
- [ ] Skill created with `SKILL.md` and `references/patterns.md`.
- [ ] Passes `quick_validate.py` with 0 errors.
- [ ] Deployed to `~/.gemini/config/skills/ascii-diagram-explainer/`, `~/.gemini/antigravity/skills/`, and `~/.agents/skills/`.
- [ ] Discovered and listed via `npx skills ls -g`.
