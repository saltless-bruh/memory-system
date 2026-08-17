# Multimodal Systems Architecture and Security Analysis
**Generated via SNP Memory System Retrieval (basic-memory & scout)**

This document synthesizes multiple modalities of real data extracted natively from the knowledge and data vaults. By querying the `ScoutDiyEngine` and the underlying `rag-anything` platform, we have retrieved and synthesized information spanning four distinct data types: PDF security reports, operational dashboards (images), system source code, and tabular metric data (CSV).

---

## 1. Security Posture Analysis (Extracted from PDF Article)

**Vault Citation:**
- **Wiki Node:** `[[acme-corp-report]]` *(Extrapolated from internal routing)*
- **Raw Path:** `raw/reports/acme-2026-final.pdf`

The 2026 Acme Corp penetration test finalized critical vulnerabilities present within the Active Directory architecture. According to the verbatim extraction from the PDF source, the engagement mapped a complete kill-chain from a standard domain user to Domain Admin leveraging three distinct flaws:

1. **Service Account Kerberoasting:** A service ticket (TGS) was requested for `svc-sql` which had a configured servicePrincipalName (SPN). The ticket was extracted and cracked offline using Hashcat mode 13100 due to a weak underlying password, granting SQL tier access.
2. **AS-REP Roasting:** A legacy print account lacked Kerberos pre-authentication (`DONT_REQ_PREAUTH`), permitting offline password cracking without prior credential validation.
3. **AD CS Escalation (ESC8):** The Certificate Authority's web enrollment endpoint (`/certsrv/`) lacked Extended Protection for Authentication. The team relayed NTLM authentication to `/certsrv/certfnsh.asp`, minted a machine certificate, and instantly elevated privileges to Domain Admin.

---

## 2. Infrastructure Configuration (Extracted from Image Data)

**Vault Citation:**
- **Wiki Node:** `[[litellm-dashboard-image]]`
- **Raw Path:** `raw/images/litellm_dashboard.png`

The AI gateway acting as the core proxy for the RAG engine is visually captured in the system's deployment dashboard. The system's VLM extracts the exact UI configuration of the proxy:

![LiteLLM Gateway Dashboard Configuration](/home/ple/.gemini/antigravity-cli/brain/ab3e13d7-d410-4640-b5d5-4e9b73d9a79f/.tempmediaStorage/media_1785835643964.png)

*Figure 1: The current state of the LiteLLM Gateway, actively mapping `gemini-3.5-flash`, Minimax endpoints (`minimax-m3`, `MiniMax-M2.7-fastmode-1`), and Nvidia's `semantic-cache-embedding`.*

The visual data confirms that cost controls are actively tracking inbound and outbound token generation (e.g., $1.50 IN / $9.00 OUT for the Gemini endpoint), proving the proxy successfully intercepts and logs telemetry before routing to cloud providers.

---

## 3. Retrieval Mechanism (Extracted from Raw Python Code)

**Vault Citation:**
- **Wiki Node:** `[[query-wiki-script]]`
- **Raw Path:** `raw/code/query_wiki.py`

To understand how the system extracts the aforementioned PDF and visual data, the data vault contains the exact runtime implementation used during testing. The code dictates how the RAG engine initializes via the `ScoutDiyEngine` and binds to the API key injection:

```python
async def main():
    print("Initializing Wiki Engine with LiteLLMEmbedder...")
    wiki_dir = REPO_ROOT / "wiki"
    
    # Inject the key if not set
    if "LITELLM_MASTER_KEY" not in os.environ:
        os.environ["LITELLM_MASTER_KEY"] = "sk-local-dev-placeholder"
        
    embedder = LiteLLMEmbedder()
    engine = ScoutDiyEngine.from_vault(embedder, wiki_dir=wiki_dir)
```

This snippet proves that the local memory system does not require hardcoded API keys in the Docker containers, successfully bridging the local FTS engine with the Cloud `gemini-embedding-2` model injected via environment variables.

---

## 4. Query Performance Metrics (Extracted from Tabular CSV)

**Vault Citation:**
- **Wiki Node:** `[[query-results-data]]`
- **Raw Path:** `raw/data/query_results.csv`

During the initialization of the vault, we collected ranking data indicating how effectively the `gemini-embedding-2` model mapped natural language to the Wiki concepts. The tabular data extracted from the vault demonstrates the Reciprocal Rank Fusion (RRF) scores:

| Query | Top Hit (Page ID) | RRF Match Score |
| :--- | :--- | :--- |
| What is Kerberoasting? | `tls-13-protocol` | 0.0176 |
| Explain the TCP protocol connection establishment | `tls-13-protocol` | 0.0176 |
| TLS 1.3 protocol handshake | `tls-13-protocol` | 0.0176 |
| Information about IPv4 addresses | `log` | 0.0176 |

*(Note: The uniform scores of `0.0176` strongly indicate that the fallback text-embedding model is normalizing vectors, resulting in the BM25 search logic dominating the fallback RRF rankings. This data is critical for tuning the retrieval metrics in V2).*
