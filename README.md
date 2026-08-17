# 🧠 SNP Memory System (V2)

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)
![MCP](https://img.shields.io/badge/MCP-Enabled-brightgreen.svg)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16_+_pgvector-336791.svg)
![LiteLLM](https://img.shields.io/badge/AI_Gateway-LiteLLM-orange.svg)
![Tests](https://img.shields.io/badge/Tests-170%20Passing-success.svg)

> [!IMPORTANT]
> **ATTENTION AI CODING AGENTS**: Before taking any action, searching the vault, or modifying code in this repository, you MUST read the operational contracts:
> 1. **Primary Operational Contract**: [`AGENTS.md`](AGENTS.md) — The mechanical rules for query workflows, 5-step search protocol, frontmatter schemas, address minting, and PR-first commits.
> 2. **Agent Onboarding & Error Playbook**: [`.agent/instructions/agent_guide.instructions.md`](.agent/instructions/agent_guide.instructions.md) — Setup guide, operational rules, Do's & Don'ts, and troubleshooting tables.

A self-hosted, bi-temporal shared-knowledge platform designed for engineering and security organizations. It unifies a compiled **Knowledge Vault** (Markdown over Git VCS) with a **Data Vault** (PostgreSQL 16 + pgvector with database-level Row-Level Security). An engine-independent **Model Context Protocol (MCP) bridge** allows autonomous AI coding agents to answer complex multi-hop questions with verbatim, verifiable source citations—without granting agents direct or destructive access to the underlying data stores.

---

## 🏗️ Three-Tier V2 Architecture

```mermaid
graph TD
    subgraph Agent_Layer ["1. Autonomous Agent Layer"]
        Agent["AI Coding Agent / IDE (Cursor / Claude / Gemini)"]
    end

    subgraph Knowledge_Layer ["2. Knowledge Vault (Wiki)"]
        BM["basic-memory (Port 8765)<br><i>FastEmbed In-Process Vector Search (:ro)</i>"]
        Gitea["Gitea VCS (Port 3000)<br><i>Git Source of Truth (wiki/*.md)</i>"]
        HostSync["snp-host-sync (Port 9000)<br><i>HMAC-SHA256 Webhook Auto-Puller (:rw)</i>"]
    end

    subgraph Data_Layer ["3. Data Vault (RAG & Security)"]
        Scout["Scout Bridge (Port 8080)<br><i>Fail-Closed rag_fetch MCP Tool</i>"]
        PG[("PostgreSQL 16 + pgvector<br><i>HNSW Vector Index + Department-Set RLS</i>")]
        RawDocs["raw/ Data Warehouse<br><i>PDFs, RFCs, Code, Benchmarks</i>"]
    end

    subgraph Gateway_Layer ["4. Model Gateway & CI/CD"]
        LiteLLM["LiteLLM Proxy (Port 4000)<br><i>AI Gateway (Gemini / OpenAI / Anthropic)</i>"]
        HealerBot["Gitea CI Auto-Healer Bot<br><i>Automated Address Drift Healer + pip-audit</i>"]
    end

    Agent -- "1. search_notes / read_note" --> BM
    Agent -- "2. rag_fetch(path, hint)" --> Scout
    Scout -- "3. SET LOCAL scout.current_depts + HNSW Cosine Search" --> PG
    PG -. "4. Filtered Chunks" .-> Scout
    Scout -- "5. Quoted Context & Citations" --> Agent

    Gitea -- "Push Webhook (HMAC-SHA256)" --> HostSync
    HostSync -- "git fetch & git reset --hard" --> BM
    Gitea -. "PR Verification & Scheduled Sweep" .-> HealerBot
    HealerBot -- "Re-mints Drifted Addresses via pgvector" --> Gitea
```

### Core Invariants & The Golden Rule

* **The Wiki tells you where to go; RAG gives you the verbatim source. Never mix the two jobs.**
* **Wiki Vault (`wiki/*.md`)**: Compiled, human/agent-curated knowledge graph. Always read first via `basic-memory` (R-5.1).
* **Data Vault (`raw/` & PostgreSQL)**: Original unstructured sources and forensic evidence. Accessible *only* through Scout's `rag_fetch` (R-4.2).
* **Scout MCP Bridge**: Enforces access control, evaluates semantic similarity, post-filters paths, and strips executable commands to prevent prompt injection (R-8.5).

---

## ⚡ V2 System Upgrades & Highlights

| Feature | V1 Architecture (Legacy) | V2 Architecture (Current Release) |
|---|---|---|
| **RAG Storage** | `RAG-Anything` (Monolithic mock) | **PostgreSQL 16 + pgvector** with HNSW indexing and GIN full-text search. |
| **Access Control** | Application-level filter (Bypassed) | **Database-Level Row-Level Security (RLS)** via Department Sets (`scout.current_depts`). |
| **Wiki Security** | `basic-memory` with direct Git awareness | **Zero-Credential `basic-memory`** (`:ro` mount) + **Host-Sync Webhook** (`host-sync` on port 9000). |
| **Embedding Engine** | Remote API egress for wiki search | **FastEmbed In-Process Engine** (`BAAI/bge-small-en-v1.5`, 384 dims) inside basic-memory container. |
| **CI/CD Auto-Healer** | Manual address repair scripts | **Gitea Actions Auto-Healer Bot** (`scout/healer.py --ci`) with `pip-audit` security gates. |
| **Branch Protection** | Unchecked local commits | **Protected Branch Guard** (`main`/`master` strictly refused by CI healer; PR-first workflow). |
| **Quality & Tests** | Basic unit tests | **170 Passing Tests** (100% pytest suite across 18 test files; strict mypy typing). |

---

## 🤖 First-Class Agent Environment

This repository includes a suite of **Progressive Disclosure Agent Skills** located in `.agent/skills/`:

| Agent Skill | Description & Purpose |
|---|---|
| `snp-search-wiki` | Search and traverse the compiled Knowledge Vault via `basic-memory` MCP. |
| `snp-rag-fetch` | Fetch verbatim, injection-safe source evidence from raw data using Scout MCP. |
| `snp-compile-wiki` | Synthesize raw source reports into structured, AGENTS.md-compliant Wiki pages. |
| `snp-ingest-raw-data`| Parse, chunk, and embed `.pdf`, `.md`, `.csv`, `.docx`, and code files into PostgreSQL. |
| `snp-verify-vault` | Run mechanical linters (`gen_index.py`) and verify that all RAG addresses resolve. |
| `snp-auto-heal-vault`| Execute `scout/healer.py` to autonomously repair semantic link drift. |
| `snp-export-mcp` | Generate ready-to-paste JSON client configs for Claude Desktop, Cursor, and Gemini. |
| `snp-bootstrap-system`| Orchestrate Docker containers and environment configurations to bring the stack online. |

---

## 🚀 Quickstart & System Setup

**Prerequisites:** Docker & Docker Compose, Python 3.12+, `uv` package manager, and Cloud API keys (e.g. `GEMINI_API_KEY`, `OPENAI_API_KEY`).

### 1. Scaffold & Configure Environment
```bash
# Initialize directories and template .env
./scripts/bootstrap.sh

# Edit .env and supply your API keys
nano .env
```

### 2. Start Core Docker Stack
```bash
# Build and launch all 6 core services
docker compose up -d --build
```

### 3. Verify System Health & Gates
```bash
# 1. Deterministic Wiki Index Check
python3 scripts/gen_index.py --check

# 2. RAG Source Address Verification Gate
uv run python scripts/verify_addresses.py

# 3. Security Vulnerability Scan
uv run --with pip-audit pip-audit

# 4. Full Pytest Regression Suite (170 tests)
uv run pytest
```

---

## 🔌 Connecting AI Agents via MCP

The system exposes two standard MCP servers over Streamable HTTP:

1. **`basic-memory` (Knowledge Vault)**: `http://localhost:8765/mcp`
   * Tools: `search_notes`, `read_note`, `list_notes`, `build_context`
2. **`scout` (Data Vault Bridge)**: `http://localhost:8080/mcp`
   * Tools: `rag_fetch`

### Client Configuration Template (Cursor / Claude Desktop / Gemini CLI)
```json
{
  "mcpServers": {
    "snp-basic-memory": {
      "url": "http://localhost:8765/mcp"
    },
    "snp-scout": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```
*For automated setup scripts, see [`docs/CONNECT_AGENTS.md`](docs/CONNECT_AGENTS.md) or run `uv run python scripts/export_mcp_config.py`.*

---

## 📂 Repository Layout

```text
snp-memory-system/
├── 📁 wiki/                      # Knowledge Vault (Compiled Markdown notes — source of truth)
│   ├── 📁 concepts/              # High-level technical & architectural concepts
│   ├── 📁 playbooks/             # Operational incident & deployment runbooks
│   ├── 📁 techniques/            # Specialized engineering mechanics & algorithms
│   ├── 📁 entities/              # Tracked infrastructure & environment targets
│   ├── 📄 index.md               # Deterministically generated master index
│   └── 📄 log.md                 # Immutable audit log of auto-healer and agent operations
│
├── 📁 raw/                       # Data Vault (Original, uncompiled source reports, code & data)
│   ├── 📁 architecture/          # RFCs, Kubernetes manifests, SQL schemas
│   ├── 📁 code/                  # Source code reference files (Python, shell)
│   ├── 📁 data/                  # Benchmark CSVs and numerical metrics
│   ├── 📁 reports/               # Research PDFs and architecture documents
│   └── 📁 runbooks/              # Cluster deployment scripts
│
├── 📁 scout/                     # RAG Bridge Service (MCP server & security filter)
│   ├── 📄 core.py                # Post-filtering logic & rag_fetch implementation
│   ├── 📄 mcp_server.py          # FastMCP server exposing rag_fetch on Port 8080
│   ├── 📄 healer.py              # PR-first Auto-Healer daemon (--ci and --push modes)
│   ├── 📄 parsers.py             # Multi-format document extraction (PDF, MD, YAML, TXT)
│   ├── 📄 chunker.py             # Semantic sliding-window chunker + LiteLLM embedder
│   ├── 📁 backends/              # Swappable RAG adapters (PgVectorRlsBackend, etc.)
│   └── 📄 diy_engine.py          # Standalone SQLite BM25 + Vector fallback engine
│
├── 📁 basic-memory/              # Wiki Engine Service (AGPL search/read MCP on Port 8765)
│   └── 📄 config.json            # FastEmbed local embedding configuration
│
├── 📁 scripts/                   # CLI Maintenance, CI/CD, and Validation Scripts
│   ├── ⚙️ bootstrap.sh           # 1-step environment setup
│   ├── 📜 host_sync.py           # Zero-credential FastAPI Git webhook sync daemon
│   ├── 📜 ingest_v2.py           # Transactional PostgreSQL + pgvector ingestion script
│   ├── 📜 gen_index.py           # Deterministic index generator and frontmatter linter
│   ├── 📜 mint.py                # Verifiable RAG address minting tool
│   ├── 📜 verify_addresses.py    # Merge-time address verification gate
│   └── 📜 setup_gitea_webhook.py # Automated Gitea webhook registration utility
│
├── 📁 .gitea/workflows/          # Gitea Actions CI/CD Pipelines
│   └── 📄 auto-healer.yaml       # Auto-healer PR gate + pip-audit security audit
│
├── 📁 config/                    # Service Configurations
│   ├── 📁 postgres/              # PostgreSQL + pgvector init schema & RLS policies
│   ├── 📁 litellm/               # LiteLLM proxy router configuration
│   └── 📁 gitea/                 # Gitea actions runner configuration
│
├── 📁 .agent/                    # AI Agent Workflows, Instructions, and Custom Skills
├── 📁 tests/                     # 100% Passing Test Suite (18 test files, 170 tests)
└── 📁 docs/                      # Technical blueprints, roadmaps, and runbooks
```

---

## 🔒 Security & Operational Posture

1. **Row-Level Security (RLS)**: PostgreSQL enforces department-set isolation (`scout.current_depts`) at the database kernel. Queries without matching roles return 0 rows (fail-closed).
2. **Prompt Injection Immunity (R-8.5)**: Scout's output schema `{status, context, citations}` deliberately contains no executable action fields; retrieved text is treated strictly as passive data evidence.
3. **PR-First Immutability (R-6.4)**: Agents propose changes via branches and Pull Requests. Direct commits to `main` by automated tools are strictly blocked.
4. **Zero-Credential Read Replica**: `basic-memory` runs with a read-only filesystem mount (`:ro`), while the isolated `host-sync` daemon receives HMAC-signed webhooks to pull updates in the background.
5. **Continuous Supply Chain Security**: CI pipelines enforce `pip-audit` to detect dependency CVEs before merge.
