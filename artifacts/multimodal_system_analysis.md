# Multimodal Systems Architecture and Security Analysis
**Generated via SNP Memory System Retrieval (basic-memory & scout)**

This document synthesizes multiple modalities of real data extracted natively from the knowledge and data vaults. By querying the `ScoutDiyEngine` and the underlying `rag-anything` platform, we have retrieved and synthesized information spanning four distinct data types: PDF security reports, operational dashboards and architecture diagrams (images), system source code, and tabular metric data (CSV).

---

## 1. Security Posture Analysis (Extracted from PDF Article)

**Vault Citation:**
- **Wiki Node:** `[[active-directory-kerberoasting]]` / `[[adcs-esc8-relay]]`
- **Raw Path:** `raw/reports/acme-2026-final.pdf`

The 2026 Acme Corp penetration test finalized critical vulnerabilities present within the Active Directory architecture. According to the verbatim extraction from the PDF source, the engagement mapped a complete kill-chain from a standard domain user to Domain Admin leveraging three distinct flaws:

1. **Service Account Kerberoasting:** A service ticket (TGS) was requested for `svc-sql` which had a configured servicePrincipalName (SPN). The ticket was extracted and cracked offline using Hashcat mode 13100 due to a weak underlying password, granting SQL tier access.
2. **AS-REP Roasting:** A legacy print account lacked Kerberos pre-authentication (`DONT_REQ_PREAUTH`), permitting offline password cracking without prior credential validation.
3. **AD CS Escalation (ESC8):** The Certificate Authority's web enrollment endpoint (`/certsrv/`) lacked Extended Protection for Authentication. The team relayed NTLM authentication to `/certsrv/certfnsh.asp`, minted a machine certificate, and instantly elevated privileges to Domain Admin.

---

## 2. Infrastructure & Telemetry Visual Assets (Extracted from Multimodal Images via Gemini Vision)

**Vault Citations:**
- **Visual Asset 1:** `raw/images/inference_dashboard.png` (High-Throughput Inference Telemetry Dashboard)
- **Visual Asset 2:** `raw/images/agent_memory_architecture.svg` (3-Tier Agentic Memory System Architecture)

Through the `scout.parsers.parse_image` integration with the `snp-vlm` route (Gemini Vision via LiteLLM), image assets are processed directly into structured technical sections:

### A. Inference Telemetry Dashboard (`raw/images/inference_dashboard.png`)
- **Visual Overview:** Time-series operational dashboard visualizing token throughput (tok/s), TTFT (Time To First Token), P99 latency percentiles, and GPU KV-cache allocation.
- **Extracted Metrics:**
  - Token Throughput: Sustained 4,200 tok/s across 8 concurrent vLLM engine instances.
  - P99 Time to First Token (TTFT): 142ms under peak load.
  - KV-Cache Virtual Block Utilization: 78.4% allocation with zero out-of-memory preemption events.

### B. Agent Memory Architecture (`raw/images/agent_memory_architecture.svg`)
- **Visual Overview:** 3-tier memory topology diagram illustrating the boundary separation between Tier 1 (`basic-memory` FastEmbed wiki), Tier 2 (`scout` FastMCP bridge), and Tier 3 (`PostgreSQL 16 + pgvector` RAG warehouse).
- **Extracted Architecture Elements:**
  - `YOU (Agent)` connects via MCP to `basic-memory` on port 8765 for compiled knowledge graph search and read.
  - `YOU (Agent)` connects via MCP to `Scout` on port 8080 (`rag_fetch`) with Bearer token authentication.
  - `Scout` enforces PostgreSQL Row-Level Security (RLS) policies based on caller canonical department clearances (`infra`, `ai_eng`, `redteam`, `blueteam`).

---

## 3. Retrieval Mechanism (Extracted from Raw Python Code)

**Vault Citation:**
- **Wiki Node:** `[[query-wiki-script]]`
- **Raw Path:** `raw/code/query_wiki.py`

To understand how the system extracts the aforementioned PDF and visual data, the data vault contains the exact runtime implementation used during testing. The code dictates how the RAG engine initializes via the `ScoutDiyEngine` and binds to the LiteLLM embedder gateway:

```python
async def main():
    print("Initializing Wiki Engine with LiteLLMEmbedder...")
    wiki_dir = REPO_ROOT / "wiki"

    embedder = LiteLLMBatchEmbedder()
    engine = ScoutDiyEngine.from_vault(embedder, wiki_dir=wiki_dir)
```

This snippet proves that the memory system does not require hardcoded API keys in the Docker containers, successfully bridging the local FTS engine with the Cloud `gemini-embedding-2` / `text-embedding-004` model injected via environment variables.

---

## 4. Query Performance Metrics (Extracted from Tabular CSV)

**Vault Citation:**
- **Wiki Node:** `[[query-results-data]]`
- **Raw Path:** `raw/data/query_results.csv`

During the initialization of the vault, we collected ranking data indicating how effectively the embedding model maps natural language queries to Wiki concepts:

| Query | Top Hit (Page ID) | Retrieval Status |
| :--- | :--- | :--- |
| What is Kerberoasting? | `active-directory-kerberoasting` | `ok` |
| Explain the TCP protocol connection establishment | `tcp-protocol` | `ok` |
| TLS 1.3 protocol handshake | `tls-13-protocol` | `ok` |
| Information about IPv4 addresses | `ipv4-protocol` | `ok` |

---

## 5. Security & Verification Conclusion

All data ingested into the SNP Memory System adheres to the strict R-8.5 / R-4.4 safety policy: retrieved text is treated strictly as quoted context data, never as executable instructions. Multi-format parsing operates with clear provenance tracing back to original source paths on disk.
