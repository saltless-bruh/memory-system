# Demonstration Runbook: OpenCode & Fresh Agent on SNP Memory System V3

> **Document Status**: Production Demo Guide & Rehearsal Runbook  
> **Date**: 2026-09-04  
> **Target Audience**: Presenters, System Operators, Evaluation Engineers  
> **Target Client**: OpenCode (and any standard MCP-compliant AI coding agent)  
> **Reference Corpus**: 433-page Obsidian Knowledge Vault  

---

## 1. Executive Demonstration Objectives

The purpose of this live rehearsal and demonstration is to prove the **V3 Retrieval Inversion** end-to-end on a **fresh, isolated AI agent (OpenCode)**.

The demonstration proves that an agent with **zero prior local memory** and **no local disk access to the vault** can accurately discover, read, ground, and cite institutional knowledge strictly through the authenticated Scout Model Context Protocol (MCP) interface.

### The 4 Core Acceptance Conditions (Blueprint V3 §1.2)

| Condition | Rule | Verification Method in Demo |
| :--- | :--- | :--- |
| **H-1: No Local Vault Bypass** | Agent has zero filesystem access to the vault (no `grep`, no `cat`, no direct shell). | OpenCode workspace contains no vault files; vault lives only in container volume `vault-replica`. |
| **H-2: Fresh Isolation** | Corpus is not cached on agent host; starts from clean session state. | Fresh OpenCode session with empty context and clear MCP cache. |
| **H-3: Observable Trace** | Every answer produces an auditable retrieval trace: Query $\rightarrow$ Ranked Hits $\rightarrow$ Canonical Read $\rightarrow$ Grounded Answer. | OpenCode terminal displays raw tool call arguments and responses. |
| **H-4: Shipped Surfaces Only** | Entire demo is driven via official MCP tools (`wiki_search`, `wiki_read`) and `snpmemory` CLI. | No ad-hoc database queries or manual script patching during demo. |

```text
+-----------------------------------------------------------------------------+
|                     DEMO RETRIEVAL & ISOLATION TOPOLOGY                     |
+-----------------------------------------------------------------------------+
|                                                                             |
|   [ OpenCode Agent Workspace ] (Isolated Sandbox / Fresh Session)           |
|   - Zero Vault Files on Disk                                                |
|   - Tools: wiki_search, wiki_read (MCP over Streamable HTTP)                |
|                               |                                             |
|                               | 1. wiki_search("SS7 bypass 2FA", k=5)       |
|                               v                                             |
|   +---------------------------------------------------------------------+   |
|   |                    Scout MCP Server (:8080/mcp)                     |   |
|   |          - Token Auth & Scope Gate (Scope: redteam, infra)          |   |
|   |          - Prompt Injection Guard (R-8.5: Data, Not Instructions)   |   |
|   +---------------------------------+-----------------------------------+   |
|                                     |                                       |
|                     +---------------+---------------+                       |
|                     |                               |                       |
|                     | 2. Hybrid SQL Query           | 3. wiki_read(path)    |
|                     v                               v                       |
|   +-----------------------------------+   +-----------------------------+   |
|   |    PostgreSQL 16 + pgvector       |   |    /vault-replica Volume    |   |
|   |  - HNSW Dense (1024-dim LiteLLM)  |   |  - Read-Only Mount          |   |
|   |  - GIN Sparse (tsv + tsv_simple)  |   |  - Canonical Markdown Page  |   |
|   |  - Reciprocal Rank Fusion (RRF)   |   |  - Envelope Normalizer      |   |
|   |  - Fail-Closed RLS Filtering      |   |                             |   |
|   +-----------------------------------+   +-----------------------------+   |
|                                                                             |
+-----------------------------------------------------------------------------+
```

---

## 2. Pre-Demo Environment Verification

Execute these pre-flight checks on the host machine before launching OpenCode:

### Step 2.1: Verify Infrastructure Health
```bash
# Build (or rebuild) and bring up the stack with the revision stamped into
# the Scout image, so `snpmemory status` can attest to what is running
# instead of reporting an unverifiable image:
SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose build scout sync-job
docker compose up -d

# Check running containers
docker compose ps

# Expected services:
# - snp-memory-postgres-1   (healthy)
# - snp-memory-litellm-1    (healthy)
# - snp-memory-scout-1      (healthy)
# - snp-memory-sync-job-1   (healthy)
# - snp-memory-host-sync-1  (healthy)
# - snp-memory-git-1        (healthy)
```

### Step 2.2: Confirm Readiness Probes
```bash
# 1. Check Scout readiness. Scout serves MCP at /mcp and has no /health route;
#    Compose probes the TCP port, so probe the same thing here.
docker compose ps scout --format '{{.Service}} {{.Status}}'
# Expect: scout Up ... (healthy)

# 2. Check sync-job readiness flag
test -f /tmp/snp-sync-job/ready && echo "Sync-job ready!"

# 3. Check LiteLLM embedding model route (1024 dimensions)
curl -fsS -X POST http://localhost:4000/v1/embeddings \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model":"snp-embed","input":"test probe"}' | grep -o '"embedding":\[[^]]*' | cut -c1-30
```

### Step 2.3: Verify Vault Indexing Status
```bash
# Verify indexed document count in PostgreSQL (Reference vault: 433 documents)
docker compose exec postgres psql -U postgres -d snp_rag \
  -c "SELECT count(*) AS total_documents FROM rag_documents;"

# Verify total chunks (Expect ~1,500 chunks)
docker compose exec postgres psql -U postgres -d snp_rag \
  -c "SELECT count(*) AS total_chunks FROM rag_chunks;"
```

---

## 3. Configuring OpenCode with SNP MCP

OpenCode supports standard MCP tool declarations. Connect OpenCode to the authenticated Scout server and the local `snpmemory` tools.

### Step 3.1: Generate MCP Configuration
The exporter supports `cursor`, `vscode`, `claude` and `gemini`. It prints to
stdout unless you pass `--out`:

```bash
uv run snpmemory mcp-config --client claude
```

**There is no `opencode` target yet**, and the `claude` output is Claude Code's
`.mcp.json` / `mcpServers` shape, which OpenCode does not read. For this
rehearsal, hand-write `opencode.json` using Step 3.2 and verify it with
`opencode mcp list` before starting a session. Generating an OpenCode config
from the CLI is tracked separately and is not part of this runbook.

### Step 3.2: Configure OpenCode (`opencode.json` or `.mcp.json`)
Add the `scout` and `snpmemory` servers to OpenCode's configuration file:

```json
{
  "mcpServers": {
    "scout": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://localhost:8080/mcp",
        "--allow-http",
        "--header",
        "Authorization:${SCOUT_AUTH_HEADER}"
      ],
      "env": {
        "SCOUT_AUTH_HEADER": "Bearer <YOUR_SCOUT_JWT_OR_STATIC_TOKEN>"
      }
    },
    "snpmemory": {
      "command": "uv",
      "args": [
        "run",
        "snpmemory",
        "mcp",
        "--root",
        "/home/ple/Documents/memo-project/snp-memory-system-main"
      ]
    }
  }
}
```

### Step 3.3: Load the Portable Plugin Package (`packages/snp-agent`)
Ensure OpenCode successfully loads the packaged rules, instructions, and portable skills:

```bash
# Dry-run test package installation to an agent workspace
uv run snpmemory install-agent <opencode-agent-dir> --dry-run
```

When OpenCode initializes:
1. **Plugin Schema Discovery**: It loads `packages/snp-agent/plugin.json` conforming to Agent Plugins 1.0.0.
2. **Skill Registration**: It registers the 7 portable domain skills:
   - `snp-bootstrap-system`, `snp-compile-wiki`, `snp-export-mcp`, `snp-ingest-raw-data`, `snp-query-wiki`, `snp-read-wiki-page`, `snp-verify-vault`.
3. **Tool Surface Audit**: Confirm OpenCode recognizes all declared MCP tools:
   - **`scout`**: `wiki_search`, `wiki_read`.
   - **`snpmemory`**: `verify`, `plan_articles`, `compile_plan`, `compile_status`, `wiki_search`, `wiki_read`.
   - OpenCode must report zero missing tool dependencies and zero unregistered/hallucinated tools.

---

## 4. Live Demonstration Script (Step-by-Step)

```text
+-----------------------------------------------------------------------------+
|                         DEMO REHEARSAL PLAYBOOK                             |
+-----------------------------------------------------------------------------+
|  ACT 1: The "Find & Ground" Query (I-1, O-1)                                |
|         -> OpenCode searches semantic topic -> reads canonical page -> cites|
|  ACT 2: The Multi-Department Boundary (Security Gate)                       |
|         -> Cross-department query fails closed via PostgreSQL RLS           |
|  ACT 3: Zero-Command Sync (I-3, W-2)                                        |
|         -> Human edits in Obsidian -> push -> index auto-updates in seconds |
|  ACT 4: Agent PR Governance (I-3, W-4)                                      |
|         -> OpenCode writes page -> opens PR -> master remains protected     |
+-----------------------------------------------------------------------------+
```

---

### Act 1: The "Find & Ground" Query (I-1, O-1)

**Presenter Prompt to OpenCode**:
> *"Can SS7 signaling protocol vulnerabilities be exploited to bypass two-factor authentication (2FA)? Please find the answer in our knowledge base and cite the exact page and section heading."*

#### What Happens Behind the Scenes (Observable Trace):
1. **Step 1: Discovery (`wiki_search`)**:
   OpenCode calls:
   ```json
   {
     "query": "SS7 signaling protocol bypass two factor authentication 2FA",
     "department": "redteam",
     "k": 5
   }
   ```
   Scout returns an envelope whose `results` carry ~40-token routing snippets:
   ```json
   {
     "results": [
       {
         "path": "techniques/SS7 Interception as a Service.md",
         "type": "concept",
         "score": 0.824,
         "snippet": "SS7 network flaws allow remote attackers to intercept SMS verification codes by spoofing carrier location updates...",
         "seen": false,
         "degraded": false
       },
       {
         "path": "concepts/Signaling System 7 Security.md",
         "type": "concept",
         "score": 0.781,
         "snippet": "Telecom SS7 protocol lacks origin authentication on MAP messages...",
         "seen": false,
         "degraded": false
       }
     ],
     "returned": 2,
     "suppressed_as_seen": 0,
     "has_more": false
   }
   ```
   A page already named in `seen` comes back redacted to
   `{"path": ..., "title": ..., "seen": true}` instead of a scored row.
2. **Step 2: Canonical Read (`wiki_read`)**:
   OpenCode inspects the snippets and chooses the top relevant page:
   ```json
   {
     "path": "techniques/SS7 Interception as a Service.md",
     "mode": "tldr"
   }
   ```
   Scout extracts and normalizes the page envelope directly from `/vault-replica`.

   `tldr` carries no outline and no section bodies, so it cannot support a
   heading citation on its own. OpenCode escalates once it knows which section
   it needs:
   ```json
   {
     "path": "techniques/SS7 Interception as a Service.md",
     "mode": "section",
     "section": "Attack Chain"
   }
   ```
   This is the read-mode ladder working as designed: the cheapest mode that
   answers the question, escalated only when the answer needs more.
3. **Step 3: Grounded Answer & Citation**:
   OpenCode answers:
   > *"Yes. Attackers exploit SS7 by sending forged UpdateLocation messages to mobile carriers, redirecting incoming SMS messages containing one-time passwords (OTPs) to an attacker-controlled handset.*
   > 
   > **Source Citation**: `[[techniques/SS7 Interception as a Service#Attack Mechanics]]`"*

**Presenter Talking Point**:
> *"Notice that OpenCode spent only ~250 tokens discovering the right document and ~40 tokens validating the TL;DR before synthesizing the answer. It cited the exact heading and never touched the raw disk directly."*

---

### Act 2: The Multi-Department Boundary (Security Gate)

**Presenter Prompt to OpenCode**:
> *"What are the internal credentials or redteam exploits related to Active Directory Kerberoasting? Run this search as department `ai_eng`."*

#### What Happens Behind the Scenes:
1. OpenCode calls `wiki_search`:
   ```json
   {
     "query": "Active Directory Kerberoasting exploit credentials",
     "department": "ai_eng",
     "k": 5
   }
   ```
2. Scout sets PostgreSQL session context:
   ```sql
   SET LOCAL scout.current_depts = 'ai_eng';
   ```
3. PostgreSQL Row-Level Security (RLS) evaluates `doc_dept_overlap_select`:
   - Redteam documents have `allowed_depts = {'redteam'}`.
   - `allowed_depts && ARRAY['ai_eng']` evaluates to `FALSE`.
4. Scout returns an empty envelope — the shape never changes, only the contents:
   ```json
   {
     "results": [],
     "returned": 0,
     "suppressed_as_seen": 0,
     "has_more": false
   }
   ```
   Note `suppressed_as_seen: 0`. The redteam pages were not hidden as
   already-read; row-level security meant they were never rows to begin with.
   The caller cannot tell the difference between "no such page" and "no such
   page *for you*", and that is the intended behaviour.
5. OpenCode honestly reports:
   > *"I found no documents matching this topic within your authorized department scope (`ai_eng`)."*

**Presenter Talking Point**:
> *"The security isolation is not a fragile Python 'if' statement in our API. It is enforced directly inside the PostgreSQL 16 kernel. Even if the AI model tries to access redteam documents, the database physically returns zero rows."*

---

### Act 3: Zero-Command Sync (I-3, W-2)

**Action by Presenter**:
1. Open Obsidian on the host or terminal.
2. Edit `concepts/Signaling System 7 Security.md` and add a new section:
   ```markdown
   ## 2026 Telephony Guardrail
   All Tier-1 telecom operators in the region now mandate Diameter-to-SS7 gateway filtering to block rogue UpdateLocation packets.
   ```
3. Run — **from the vault checkout, not this source checkout**:
   ```bash
   # Confirm you are in the vault repo and that the remote is the private
   # Gitea one. If this prints a github.com URL, STOP: you are in the source
   # checkout and this push would publish the vault.
   git remote get-url origin
   # Expect: http://localhost:3000/snp-admin/snp-memory.git

   git add "concepts/Signaling System 7 Security.md"
   git commit -m "docs(telecom): add 2026 telephony guardrail update"
   git push origin main
   ```

#### What Happens Behind the Scenes:
* Gitea fires a webhook $\rightarrow$ `host-sync` pulls snapshot into `/vault-replica`.
* `sync-job` detects file change $\rightarrow$ detects `content_hash` mismatch.
* Re-chunks sections $\rightarrow$ embeds via LiteLLM $\rightarrow$ atomic PostgreSQL update.
* **Elapsed Time**: ~7 seconds, measured by the W-2 acceptance gate
  (`engine_acceptance.py --group vault-change-propagates`); the same gate
  observes nothing arriving at all within 180 s when the watcher is stopped.
  **Operator commands run**: Zero.

**Presenter Prompt to OpenCode**:
> *"What is the 2026 telephony guardrail for SS7 security?"*

#### Output:
OpenCode calls `wiki_search`, immediately reads the updated chunk, and quotes:
> *"According to `[[concepts/Signaling System 7 Security#2026 Telephony Guardrail]]`, Tier-1 telecom operators mandate Diameter-to-SS7 gateway filtering to block rogue UpdateLocation packets."*

---

### Act 4: Agent PR Governance (I-3, W-4)

**Presenter Prompt to OpenCode**:
> *"Please create a new concept page documenting 'Diameter Protocol Vulnerabilities' in telecommunications. Follow our V3 schema contract."*

#### What OpenCode Must Do:
1. Validates schema using `snpmemory` or reads `SCHEMA.md`.
2. Creates a clean YAML frontmatter block:
   ```yaml
   ---
   title: Diameter Protocol Vulnerabilities
   created: 2026-09-04
   updated: 2026-09-04
   type: concept
   tags: [telecom, security, diameter, lte]
   sources: [https://enisa.europa.eu/publications/diameter-security]
   confidence: high
   ---
   ```
3. Formats body with mandatory frame:
   - `# Diameter Protocol Vulnerabilities`
   - `## TL;DR`
   - `## Technical Overview`
   - `## Provenance`
   - `## Cross-References` (includes `[[Signaling System 7 Security]]` and `[[LTE Network Architecture]]`).
4. Prepares the change on branch `feat/agent-diameter-security`.
5. **Stops there.** No exposed tool performs a `git push` — the branch-and-PR
   rule is enforced by absent capability, not by good behaviour (`AGENTS.md`,
   rules R-6.4 and R-7.3). A human pushes the branch and opens the pull
   request. Demonstrate this by asking the agent to push and watching it
   decline for lack of a tool; that refusal is the control being demonstrated.
6. **`main` remains untouched until human review and merge.**

---

## 5. Live Acceptance Scorecard

> Fill this in **during** the rehearsal, from what you observe. Every row ships
> blank on purpose: a scorecard filled before the run records an intention, not
> a result, and the whole point of these criteria is that they can fail.

| Acceptance Criterion | Verification Check | Demo Result |
| :--- | :--- | :--- |
| **I-1: Ask** | OpenCode answers complex technical prompt. | [ ] not yet run |
| **I-2: Add Document** | New document indexed and findable. | [ ] not yet run |
| **I-3: Edit Page** | Human edit in Obsidian updates index in < 5s. | [ ] not yet run |
| **O-1: Grounded Answer** | Answers cite `[[Page#Heading]]`, no hallucinated URLs. | [ ] not yet run |
| **H-1: No Local Read** | Zero disk reads of `/vault-replica` in agent shell. | [ ] not yet run |
| **H-2: Fresh Agent** | OpenCode starts with empty context and succeeds. | [ ] not yet run |
| **H-3: Audit Trace** | Query $\rightarrow$ Hits $\rightarrow$ Envelope logged in console. | [ ] not yet run |
| **H-4: Shipped Surfaces** | Driven 100% via `wiki_search`, `wiki_read`, `snpmemory`. | [ ] not yet run |
| **P-1: Package Loading** | OpenCode successfully loads `packages/snp-agent` (`plugin.json`, rules, instructions, and 7 portable skills) with zero schema or parse errors. | [ ] not yet run |
| **T-1: Tool Boundary & Execution** | OpenCode restricts actions exclusively to provided MCP tools and `snpmemory` CLI commands, attempts zero hallucinated/unauthorized tools, and successfully runs all invoked tools to clean completion. | [ ] not yet run |

**Note: I-3 is currently expected to fail as written.** The W-2 acceptance gate measures propagation at ~7 seconds (Act 3), against I-3's bar of under 5. That gap is real and unexplained, not a rounding error — record the observed time during the rehearsal rather than adjusting the criterion, and treat closing it as work rather than a wording problem.

---

## 6. Live Emergency Troubleshooting

```text
+-----------------------------------------------------------------------------+
|                          TROUBLESHOOTING CHEATSHEET                         |
+-----------------------------------------------------------------------------+
|                                                                             |
|  SYMPTOM 1: OpenCode reports "wiki_search tool not found"                   |
|  Fix: Ensure mcp-remote is running. Check OpenCode MCP status indicator.    |
|       Verify Scout container is running: docker compose ps scout.           |
|                                                                             |
|  SYMPTOM 2: Scout returns 401 Unauthorized or 403 Forbidden                 |
|  Fix: Check SCOUT_AUTH_HEADER. Ensure token has "Bearer " prefix and        |
|       contains valid canonical departments: redteam, blueteam, ai_eng, infra|
|                                                                             |
|  SYMPTOM 3: Search returns empty array [] for valid query                   |
|  Fix: Check department clearance passed in query argument.                  |
|       Verify database rows: SELECT count(*) FROM rag_chunks;                |
|       Check if LiteLLM embedding route is responsive: curl :4000/health     |
|                                                                             |
|  SYMPTOM 4: OpenCode attempts to run `grep` or `find` on the local machine   |
|  Fix: Reiterate prompt instruction: "Use your wiki_search MCP tool to find  |
|       knowledge in the remote vault."                                       |
|                                                                             |
+-----------------------------------------------------------------------------+
```
