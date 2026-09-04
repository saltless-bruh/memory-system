# SNP Memory System — Comprehensive Skills Audit Report

> **Document Status**: Complete Audit Report  
> **Date**: 2026-09-04  
> **Scope**: Skills evaluation across `.agent/skills/` and `packages/snp-agent/skills/`  
> **Author**: Architecture & Agentic Tooling Audit Team  

---

## 1. Executive Summary & Automated Baseline

This audit provides a comprehensive evaluation of the custom agent skills powering the **SNP Memory System V3**. The evaluation combines deterministic automated specification checks with qualitative analysis conducted under the **Skill Creator** framework.

### Automated Test Baseline

Prior to qualitative analysis, the complete skills and agent packaging test suite was executed against `.agent/skills/` and `packages/snp-agent/skills/`:

```bash
uv run pytest tests/test_agent_skills_spec.py \
              tests/test_agent_package.py \
              tests/test_agent_package_sync.py \
              tests/test_agent_tool_namespace.py -v
```

```text
+-----------------------------------------------------------------------------+
|                     AUTOMATED SUITE EXECUTION SUMMARY                       |
+-----------------------------------------------------------------------------+
|  Test Module                                    Tests Run   Status   Time   |
|  --------------------------------------------   ---------   ------   -----  |
|  test_agent_skills_spec.py (YAML/Syntax/Spec)   211         PASSED   ~1.8s  |
|  test_agent_tool_namespace.py (Tool Contracts)  26          PASSED   ~0.8s  |
|  test_agent_package.py (MCP Tool Surface)       8           PASSED   ~0.4s  |
|  test_agent_package_sync.py (Sync Parity)       2           PASSED   ~0.2s  |
|  --------------------------------------------   ---------   ------   -----  |
|  TOTAL EXECUTION                                247 / 247   PASSED   3.24s  |
+-----------------------------------------------------------------------------+
```

### Key Findings
1. **100% Spec & Syntax Compliance**: All 16 skills conform to Agent Plugins 1.0.0, use valid YAML frontmatter, avoid unquoted colons, and strictly omit prompt-injection risk characters (`<` / `>`).
2. **Compact Progressive Disclosure**: All skills are well within the recommended 500-line ceiling (ranging from 25 to 50 lines), leaving maximum context window headroom for model reasoning.
3. **Retrieval Semantic Dissonance**: A prominent naming mismatch exists between `snp-rag-fetch` and `snp-search-wiki` due to legacy V2 tool names, causing model hesitation and routing friction.
4. **Actionability Gap**: Several skills outline high-level operational steps but omit concrete CLI commands and readiness verification probes.

---

## 2. Skill Taxonomy & Architecture

The skills repository is partitioned into two distinct operational scopes governed by `packages/snp-agent/plugin.json`:

```text
+-----------------------------------------------------------------------------+
|                         SKILL ECOSYSTEM TAXONOMY                            |
+-----------------------------------------------------------------------------+
|                                                                             |
|  PORTABLE DOMAIN SKILLS (ships)             REPO-LOCAL METHODOLOGY          |
|  Distributed to external consumers          Local development discipline    |
|  +--------------------------------+         +----------------------------+  |
|  | snp-bootstrap-system           |         | superpowers-workflow       |  |
|  | snp-compile-wiki               |         | superpowers-plan           |  |
|  | snp-export-mcp                 |         | superpowers-brainstorm     |  |
|  | snp-ingest-raw-data            |         | superpowers-debug          |  |
|  | snp-rag-fetch                  |         | superpowers-review         |  |
|  | snp-search-wiki                |         | superpowers-finish         |  |
|  | snp-verify-vault               |         | superpowers-tdd            |  |
|  +--------------------------------+         | superpowers-python-auto    |  |
|                                             | superpowers-rest-auto      |  |
|                                             +----------------------------+  |
|                                                                             |
+-----------------------------------------------------------------------------+
```

---

## 3. Qualitative Evaluation: Portable Domain Skills (`snp-*`)

### 3.1 `snp-compile-wiki`
* **Current Grade**: **A**
* **Primary Role**: Guides the authoring and revision of compiled wiki pages from available evidence.
* **Strengths**:
  * Authoritative alignment with target vault's `SCHEMA.md`.
  * Enforces the mandatory V3 heading frame (`# H1`, optional `## TL;DR`, `## Provenance`, and required `## Cross-References` with $\ge 2$ `[[wikilinks]]`).
  * Enforces Git branch and PR governance (explicitly forbids pushing directly to `main` or `master`).
  * Clearly states that source references record provenance, not manual vector retrieval pointers.
* **Deficiencies & Risks**:
  * Lacks an inline YAML frontmatter template, forcing the agent to assemble the YAML block from verbal descriptions.
* **Recommendations**:
  * Add an explicit, copy-pasteable frontmatter template block to the skill body.

### 3.2 `snp-export-mcp`
* **Current Grade**: **A**
* **Primary Role**: Generates and merges client configurations for Scout (HTTP) and `snpmemory` (stdio).
* **Strengths**:
  * High operational precision: provides the exact command `snpmemory mcp-config --client claude`.
  * Security-conscious: explicitly instructs agents never to print sensitive auth tokens during debugging.
  * Clearly distinguishes between remote retrieval (`scout`) and local stdio tools (`snpmemory`).
* **Recommendations**:
  * Production ready as-is.

### 3.3 `snp-verify-vault`
* **Current Grade**: **A-**
* **Primary Role**: Validates wiki pages against the V3 frontmatter and structural heading contract.
* **Strengths**:
  * Outstanding epistemic honesty: transparently declares that the legacy automated checker does not fully certify V3 heading frames, forcing explicit manual review.
  * Emphasizes that verification is read-only and that remediation belongs on a feature branch.
* **Deficiencies & Risks**:
  * Omits the CLI invocation command for the automated portion of the check (`uv run snpmemory verify-vault`).
* **Recommendations**:
  * Provide the exact CLI command alongside the manual verification checklist.

### 3.4 `snp-ingest-raw-data`
* **Current Grade**: **B+**
* **Primary Role**: Guides local evidence ingestion and acknowledges deferred external fetch queues.
* **Strengths**:
  * Strict boundary separation: distinguishes supported local files (`raw/`) from deferred external URLs.
  * Role security: mandates using `rag_ingest_role`, never migration admin or query identities.
  * Clear failure taxonomy: notes that a zero-chunk output is an infrastructure failure, not an empty document.
* **Deficiencies & Risks**:
  * Relies entirely on `sync-job` background watching without providing the manual on-demand CLI command (`snpmemory ingest --path ...`).
* **Recommendations**:
  * Add the direct CLI command for explicit ingestion triggers.

### 3.5 `snp-bootstrap-system`
* **Current Grade**: **B**
* **Primary Role**: Infrastructure setup, startup, and container health verification.
* **Strengths**:
  * Accurate 5-step lifecycle sequence (bootstrap script, `.env` population, role separation, docker compose, migration check).
* **Deficiencies & Risks**:
  * Vague verification guidance: instructs the agent to "confirm postgres-migrate completed" and "inspect readiness endpoints" without giving exact terminal commands.
* **Recommendations**:
  * Provide concrete verification one-liners: `docker compose ps`, `test -f /tmp/snp-sync-job/ready`, and `curl -fsS http://localhost:8080/health`.

### 3.6 The Retrieval Inversion Dilemma: `snp-rag-fetch` vs. `snp-search-wiki`
* **Current Grades**: `snp-rag-fetch`: **B-** | `snp-search-wiki`: **C+**
* **Root Cause Analysis**:
  In V3, retrieval was inverted from a page-to-index lookup (`rag_fetch`) to an index-to-page workflow (`wiki_search` followed by `wiki_read`). The two retrieval skills currently exhibit severe naming dissonance:

```text
THE RETRIEVAL NAMING CONFLICT:
+-----------------------------------------------------------------------------+
|  Actual V3 Contract:                                                        |
|    Step 1: wiki_search(query, k)  -> discovers candidate pages              |
|    Step 2: wiki_read(path, mode)  -> reads canonical page envelope          |
|                                                                             |
|  Skill A: snp-rag-fetch                                                     |
|    - Retains deprecated V2 name ('rag_fetch' does not exist in V3)          |
|    - Contains instructions and JSON payloads for BOTH Step 1 & Step 2       |
|                                                                             |
|  Skill B: snp-search-wiki                                                   |
|    - Named 'search', but the body tells the agent:                          |
|      "This skill is step two of retrieval... Call wiki_read"                |
+-----------------------------------------------------------------------------+
```

* **Observed Agent Failure Mode**:
  When a user asks to "search the wiki", the agent triggers `snp-search-wiki` by keyword association. Upon reading the skill, it is instructed that `snp-search-wiki` is actually step two, prompting confusion, tool hallucination, or unnecessary multi-turn delays.
* **Remediation Strategy**:
  1. Rename `snp-rag-fetch` $\rightarrow$ **`snp-query-wiki`** (focusing on search, candidate routing, and RRF scores).
  2. Rename `snp-search-wiki` $\rightarrow$ **`snp-read-wiki-page`** (focusing on `wiki_read`, read modes, outline extraction, and token budget management).
  3. Alternatively, merge both into a unified, definitive skill: **`snp-retrieve-knowledge`**.

---

## 4. Qualitative Evaluation: Repo-Local Superpowers Skills (`superpowers-*`)

The 9 repository-local skills in `.agent/skills/` enforce software engineering hygiene across all implementation sessions.

```text
+-----------------------------------------------------------------------------+
|                    SUPERPOWERS LIFECYCLE EVALUATION                         |
+-----------------------------------------------------------------------------+
|                                                                             |
|   superpowers-brainstorm  -->  superpowers-plan  -->  /superpowers-execute  |
|   (Goals, Risks, Options)      (Small 2-10m steps)    (Plan Approval Gate)  |
|                                                              |              |
|                                                              v              |
|   superpowers-finish      <--  superpowers-review  <-- superpowers-tdd     |
|   (Hygiene & Verification)     (Blocker/Major/Minor)   (Red/Green/Refactor) |
|                                                                             |
+-----------------------------------------------------------------------------+
```

### 4.1 Strengths Across the Superpowers Set
1. **Enforced Planning Gates**: Effectively stops agents from making impulsive, unreviewed multi-file modifications.
2. **Standardized Severity Classification**: Code reviews reliably segment issues by Blocker, Major, Minor, and Nit.
3. **Deterministic Persistence**: Enforces writing plans and reviews to disk under `artifacts/superpowers/`, ensuring auditability.
4. **Modular Automation Patterns**: `superpowers-python-automation` and `superpowers-rest-automation` provide production-ready templates for retries, timeouts, and idempotency.

### 4.2 Identified Risks & Remediations
1. **Activation Script Dependency**:
   `superpowers-workflow` requires running `python .agent/skills/superpowers-workflow/scripts/record_activation.py`. If executed in an environment without dependencies activated, this check fails.
   * *Remediation*: Wrap the activation call with a safe fallback or make it non-blocking.
2. **Description Trigger Broadening**:
   Descriptions currently focus on formal verbs ("refactor", "debug"). Broadening trigger phrases to capture informal user queries (e.g., "fix this bug", "why is this failing") will prevent undertriggering.

---

## 5. Summary Scorecard & Priority Backlog

```text
+-----------------------+-------+------------------+--------------------------+
| Skill Name            | Grade | Category         | Recommended Action       |
+-----------------------+-------+------------------+--------------------------+
| snp-compile-wiki      | A     | Domain / Author  | Add YAML frontmatter box |
| snp-export-mcp        | A     | Domain / Config  | Maintain as-is           |
| snp-verify-vault      | A-    | Domain / Audit   | Add CLI verify command   |
| snp-ingest-raw-data   | B+    | Domain / Ingest  | Add CLI ingest command   |
| snp-bootstrap-system  | B     | Domain / Infra   | Add concrete probes      |
| snp-rag-fetch         | B-    | Domain / Search  | Rename: snp-query-wiki   |
| snp-search-wiki       | C+    | Domain / Read    | Rename: snp-read-page    |
| superpowers-* (all 9) | A     | Repo Discipline  | Broaden trigger phrases  |
+-----------------------+-------+------------------+--------------------------+
```

### Actionable Implementation Steps
1. **Phase 1 (Immediate)**:
   * Add inline examples and CLI one-liners to `snp-compile-wiki`, `snp-bootstrap-system`, and `snp-ingest-raw-data`.
2. **Phase 2 (Retrieval Consolidation)**:
   * Refactor the retrieval skill pair to eliminate naming debt (`snp-rag-fetch` $\rightarrow$ `snp-query-wiki`; `snp-search-wiki` $\rightarrow$ `snp-read-wiki-page`).
   * Update `packages/snp-agent/plugin.json` and sync the changes across `.agent/` and `.claude/`.
3. **Phase 3 (Test Suite Alignment)**:
   * Update `tests/test_agent_package.py` and `tests/test_agent_tool_namespace.py` to assert the revised skill names and ensure zero regression.
