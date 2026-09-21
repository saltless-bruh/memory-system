# Architecture & Flow Review — SNP Memory System V3

**Date:** 2026-09-10  
**Context:** Review of architectural topology, data flows, and invariants following visual diagramming with `/archify` and `/diagram-design`.

---

## Architectural Shape & Invariant Mismatch

```
                   ┌──────────────────────────────────────────────────┐
                   │               Human Author (Obsidian)            │
                   └─────────────────────────┬────────────────────────┘
                                             │
                                             │ git push (W-2)
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ The Git vs RLS Disconnect:                                                             │
│                                                                                        │
│   Gitea Repository (snp-memory) ──────────────▶ All 432 Markdown Notes                 │
│   (ONE monolithic Git repo clone)               (Git boundary gates NOTHING)           │
│                                                          ▲                             │
│                                                          │                             │
│   PostgreSQL 16 + pgvector      ──────────────▶ Department RLS Gates                   │
│   (Strict rag_app_role RLS)                     (redteam, blueteam, ai_eng, infra)     │
│                                                                                        │
│   CRITICAL GAP: RLS strictly filters queries in PostgreSQL, but any user with Git       │
│   access in Obsidian possesses the full plaintext vault across all departments.         │
└────────────────────────────────────────────────────────────────────────────────────────┘

                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ The Two-Store Asynchronous Sync Window:                                                │
│                                                                                        │
│   Gitea Push ──▶ Webhook ──▶ Host-Sync ──▶ Vault Replica (/current)                    │
│                                                  │                                     │
│                                                  ├─▶ Scout reads immediately (mount ro)│
│                                                  │                                     │
│                                                  ▼ inotify trigger                     │
│                                              Sync-Job ──▶ LiteLLM ──▶ PostgreSQL       │
│                                                                       (Delayed Index)  │
│                                                                                        │
│   RACE CONDITION: A page updated on disk is immediately readable via wiki_read          │
│   before its embeddings are indexed, while deleted pages remain searchable in vector   │
│   indexes until sync-job reconciles.                                                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Blockers

### B-1: Git Layer vs RLS Security Disconnect (Defect #29)
* **Finding:** The architecture advertises strict department isolation (`redteam`, `blueteam`, `ai_eng`, `infra`), enforced via PostgreSQL Row-Level Security (`rag_app_role`) and Scout caller scope tokens. However, the underlying vault is stored in **one single Git repository** (`snp-admin/snp-memory`) containing all 432 pages.
* **Impact:** The pluralism principle ("humans edit on their own clone in Obsidian") means that any human developer with Git clone access has full, unrestricted read access to all confidential markdown pages from every department on their local filesystem. RLS gates the search index, but Git gates nothing.
* **Resolution:** If multi-department confidentiality is required, the vault must be partitioned into per-department Git repositories with separate access controls, or client-side vault encryption must be introduced.

---

## 2. Majors

### M-1: Two-Store Asynchronous Sync Divergence
* **Finding:** Scout retrieves search hits from PostgreSQL 16 + pgvector, but fulfills `wiki_read` directly from the `vault-replica` filesystem mount (`/vault-replica/current/wiki`). 
* **Impact:** Because `host-sync` updates the filesystem mount in milliseconds while `sync-job` asynchronously chunks, embeds (via remote LiteLLM), and inserts into PostgreSQL, there is an observable divergence window:
  - A newly committed page can be read by path via `wiki_read` before it is discoverable via `wiki_search`.
  - A deleted or renamed page will still appear in `wiki_search` results until the reconciliation loop finishes, but `wiki_read` will return a 404/failure when attempting to read the file from disk.
* **Resolution:** Ensure query results cross-check the mounted replica or enforce read-after-write consistency tokens across the sync pipeline.

### M-2: Ingestion Latency Ceiling on Acceptance Workflow W-2 (Defects #36, #37)
* **Finding:** The measured W-2 latency from Obsidian `git push` to searchable index hits a p50 of 5.65s and p95 of 7.78s against a 5.0s target.
* **Analysis:** Profiling shows that `git archive` + `tar extract` of all 433 files takes only **0.109s**, and embedding takes **0.974s**. The dominant bottleneck (~3.9s p95, ~50% of the entire budget) is trapped in Gitea webhook dispatch, network delivery on the Docker bridge, and `host-sync` loop latency.
* **Resolution:** Optimize webhook delivery in Gitea and eliminate unnecessary polling loops in `host-sync`.

### M-3: Missing `## TL;DR` Heading on 86% of the Vault (Defect #38)
* **Finding:** The retrieval contract strictly defines `## TL;DR` as **Chunk 0**—the primary text scored during page-level hybrid retrieval. In practice, 371 out of 433 pages (85.7%) lack a `## TL;DR` section.
* **Impact:** For 86% of the vault, retrieval queries match arbitrary paragraph fragments rather than the canonical topic abstract, degrading precision and routing accuracy.
* **Resolution:** Run a batch lint/enrichment workflow to author grounded `## TL;DR` summaries across the remaining 371 pages.

---

## 3. Minors

### m-1: Unencrypted Internal Bearer Tokens (Defect #31)
* **Finding:** Emitted configurations default to cleartext HTTP (`http://scout:8080/mcp`, `http://litellm:4000/v1`). Static bearer tokens and request bodies cross the Docker network unencrypted.
* **Impact:** Acceptable on loopback and isolated Docker bridges, but violates zero-trust transit if services are deployed across distributed nodes.
* **Resolution:** Enable TLS termination or reverse proxy encryption before expanding beyond localhost.

### m-2: Concurrency & Lock Service Omission (Defect #30)
* **Finding:** There is no distributed locking service between concurrent human Obsidian writers and agent PR branches.
* **Impact:** Concurrent pushes to the same markdown pages create Git merge conflicts that require manual developer resolution.
* **Resolution:** Keep human authorship single-writer per note or introduce optimistic locking headers during automated generation.

### m-3: Inotify Inode Binding Fragility
* **Finding:** `sync-job` watches the directory `/vault-replica` rather than `/vault-replica/current` because inotify binds to the underlying inode, not the symlink.
* **Impact:** Watching the parent directory triggers on any file mutation inside `/vault-replica`, requiring careful debouncing to prevent spurious re-index cycles.

---

## 4. Nits

### n-1: Dead `basic-memory/` References in Manifest (Defect #32)
* **Finding:** `basic-memory/` is no longer built or deployed in Compose, yet its `requirements.lock` remains tracked as a release-manifest input.
* **Resolution:** Purge orphaned lockfile references from `write_release_manifest.py`.

### n-2: Vietnamese vs English Recall Disparity (Defect #39)
* **Finding:** Empirical recall on English queries is `recall@1 0.95`, whereas Vietnamese queries score `recall@1 0.70` across the 276 bilingual pages.
* **Resolution:** Evaluate multilingual embedding checkpoints or tune sparse tokenization weights in pg_trgm.

---

## Overall Summary & Next Actions

| Severity | Count | Primary Areas |
|---|---|---|
| **Blocker** | 1 | Git repo monolithic access vs PostgreSQL RLS scope |
| **Major** | 3 | Two-store sync latency, W-2 webhook delay, 86% missing `## TL;DR` |
| **Minor** | 3 | Cleartext HTTP, lock service absence, inotify symlink watch |
| **Nit** | 2 | Manifest debris, Vietnamese embedding recall gap |

### Recommended Sequencing:
1. **Architectural Decision (Owner Action):** Acknowledge or decide whether the single monolithic Git repository is acceptable for internal trust, or if per-department repos must be created.
2. **Quality Sprint:** Execute a targeted compilation/backfill pass to add required `## TL;DR` summaries to the 371 ungrounded wiki pages.
3. **Performance Profiling:** Instrument the Gitea webhook dispatch span to reduce the unexplained ~3.9s gap in the W-2 acceptance loop.
