# basic-memory — Required Setup & Config (V1)

> Hard-won configuration from getting basic-memory 0.22.1 working as the
> LLM-Wiki engine on the vault. **Without these settings basic-memory
> either mutates the vault or gives poor Vietnamese recall.** Verified
> live 2026-07-20/21 on the 7-page sample vault.
>
> ⚠️ basic-memory's config is **global** (`~/.basic-memory/config.json`),
> not per-project (only the project *path* is per-project). These settings
> therefore affect every project on the machine. Treat this file as the
> source of truth for what the SNP project needs.

## Install

```powershell
pip install --prefer-binary basic-memory
```

`--prefer-binary` is **required**: without it pip tries to build litellm's
source tarball, which needs a Rust toolchain and fails. `--prefer-binary`
pulls the litellm wheel instead.

CLI shims land in `%APPDATA%\Python\Python3xx\Scripts\` (`basic-memory.exe`,
`bm.exe`) — add to PATH or call by full path.

## Register the vault

```powershell
bm project add snp-wiki <repo>\wiki
bm project default snp-wiki
```

## Required config (`~/.basic-memory/config.json`)

| Setting | Value | Why |
|---|---|---|
| `disable_permalinks` | `true` | **Critical.** By default basic-memory writes a `permalink` into every file's frontmatter on sync. That mutates the vault (violates "vault = source of truth", R-2.5) and, in a sandboxed/read-only vault, the write fails and **entity import aborts**. Disabling → 0 file writes, `sources[]` preserved. |
| `ensure_frontmatter_on_sync` | `false` | Stops ongoing frontmatter normalization/rewrite. Same rationale — keep the vault pristine. |
| `semantic_embedding_provider` | `litellm` | **Settled decision.** Routes embeddings through the litellm library to bge-m3, unifying wiki-search with the RAG embedder (design §2.2 ideal). The default `fastembed` + `bge-small-en-v1.5` is English-only → poor Vietnamese recall (Gate 4), and FastEmbed has no bge-m3. |
| `semantic_embedding_model` | `ollama/bge-m3` | The Gate-4-validated multilingual model. litellm reaches Ollama at its default `localhost:11434` — **local, no egress** (preserves R-8.2's intent; see refinement note below). |
| `semantic_embedding_dimensions` | `1024` | bge-m3's output dim. **Required** for the litellm provider with a non-default model (the vector table schema is created before the first embedding response). |
| `semantic_min_similarity` | `0.35` | basic-memory's chunked scoring runs lower than raw cosine (both bge-m3 and mpnet score ~0.45–0.65 here); the default `0.55` filters out correct pages. At 0.35, all Vietnamese test paraphrases return the right page @1. Revisit as the corpus grows. |

> **R-8.2 refinement.** R-8.2 as written mandates *in-process FastEmbed*.
> We deviate: embeddings go through litellm → **local Ollama** (bge-m3).
> This preserves R-8.2's actual guarantee (local, no network egress) and
> satisfies R-8.1 (routed via LiteLLM), while gaining the unified bge-m3
> embedder Gate 4 selected. Cost accepted: a local embed call per query
> (latency) and wiki-search now depends on Ollama being up. On a bounded
> ~100–500 page wiki this is fine; revisit if query latency matters.

## Required `.bmignore` addition (`~/.basic-memory/.bmignore`)

```
index.md
```

`wiki/index.md` is a **generated navigation aggregate** (gen_index.py) that
concatenates every page summary. Indexed, it matches *every* query and
crowds out the real pages. Exclude it so the engine only surfaces real
knowledge pages.

## Run the engine (sync + MCP)

There is **no `bm sync` command** in 0.22. File→entity sync happens in the
**MCP server's startup lifespan**:

```powershell
bm mcp --transport streamable-http --host 127.0.0.1 --port 8765 --project snp-wiki
```

- On startup it does a full initial scan → imports files as entities →
  resolves `[[wikilinks]]` into graph relations → embeds chunks.
- `bm reindex` does **not** import files (it only rebuilds indexes over
  existing entities). Don't rely on it for initial load.
- If a prior `status`/`reindex` polluted the scan state so sync reports
  "0 changes" with 0 entities, `bm reset` (drop tables) then start the
  server fresh.

## Verification (what "working" looks like)

```
bm project info snp-wiki   → Entities 7, Relations 13, Chunks ~62
bm tool search-notes "cách lấy mật khẩu tài khoản dịch vụ có SPN"
                           → techniques/kerberoasting.md as top hit
```

Verified 2026-07-21: 5/5 Vietnamese paraphrase queries return the correct
page @1 (kerberoasting, asrep-roasting, adcs-esc8, acme-corp,
active-directory).

## Embedding decision — SETTLED (2026-07-21)

**Chosen: bge-m3 via LiteLLM** (`semantic_embedding_provider: litellm`,
`model: ollama/bge-m3`). basic-memory supports a `litellm` embedding
provider (`repository/litellm_provider.py`), confirmed working: 6/6
Vietnamese paraphrase queries return the correct page @1.

Rationale: design §2.2's stated ideal (unify wiki-search + RAG on one
model), the Gate-4-validated model, and it stays local/no-egress. On this
small vault bge-m3 and mpnet tied on recall (both @1) — the deciding
factor was architectural unification, not small-vault recall. See the
R-8.2 refinement note above for the accepted trade-off. The FastEmbed
mpnet path remains a documented fallback if in-process/offline embedding
is ever required.
