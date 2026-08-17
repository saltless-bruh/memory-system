# Phase 0 Spikes — Verification Runbook

Everything in Phase 0 is authored and smoke-tested. This runbook is the
**verification half**: the steps to actually run the gates once the local
infrastructure is up. Nothing here is marked done in `tasks.md` until its
DoD is genuinely observed.

## Why these run on your machine, not in this session

The gates need running services the authoring environment does not have:
Docker daemon, a local Ollama with models pulled, `basic-memory`, and
`fastembed`. Also note: **Python subprocesses here are sandboxed** and
cannot write new files into the repo (that is why the harnesses print all
results to stdout and only *try* to save a JSON transcript). On your
machine, unsandboxed, transcripts save normally.

## 0. Prerequisites (one time)

```powershell
# --- Docker (already installed: v29.1.3) — just start the daemon ---
#     Launch Docker Desktop and wait for it to report "running".
docker info    # should print server version, not an error

# --- Ollama (NOT installed; PATH entry is stale) ---
#     Install from https://ollama.com/download, then:
ollama pull bge-m3                # embedding (multilingual, VN)
ollama pull qwen2.5:7b-instruct   # LLM for RAG entity extraction
ollama pull qwen2.5vl:7b          # VLM for image/table → text
ollama list                       # confirm all three tags

# --- Python deps for the spikes ---
uv pip install pyyaml fastembed basic-memory
#   pyyaml     — vault parsing (already present)
#   fastembed  — Gate 4 backend A (basic-memory's in-process model)
#   basic-memory — Gates 1, 2, 4
```

```powershell
# --- Environment ---
Copy-Item .env.example .env
#   Edit .env: set LITELLM_MASTER_KEY, confirm OLLAMA_* model tags match
#   `ollama list` exactly.
```

## 1. T-0.1 DoD — stack comes up, repo push/clone works

```powershell
docker compose up -d git litellm
docker compose ps            # both healthy
# Gitea:   http://localhost:3000   (first-run: create admin, make a repo)
# LiteLLM: http://localhost:4000/health/liveliness
git remote add origin http://localhost:3000/<you>/snp-wiki.git
git push -u origin main && echo "push OK"
```

## 2. T-0.2 DoD — model calls work, and still work with egress cut

```powershell
# Embedding through the chokepoint:
curl -s http://localhost:4000/v1/embeddings `
  -H "Authorization: Bearer $env:LITELLM_MASTER_KEY" `
  -H "Content-Type: application/json" `
  -d '{"model":"snp-embed","input":"kerberoasting"}' | ConvertFrom-Json | Select-Object -First 1

# LLM + VLM: same pattern against /v1/chat/completions with snp-llm / snp-vlm.

# No-egress proof (R-8.1): disconnect the external NIC (or block outbound
# in the firewall, leaving localhost), then repeat the calls. They must
# still succeed — everything terminates on local Ollama.
```

## 3. Gate 3 first (cheapest — no infra) — T-0.3

Read `gate3_agpl_license/DECISION_MEMO.md`, get the written answer, fill
the decision record, and record the row in `GATE_RESULTS.md`. A DENIED
here deletes the whole basic-memory branch, so resolve it before sinking
time into Gates 1/2/4.

## 4. T-0.4 — basic-memory sees the vault

```powershell
basic-memory project add snp-wiki .\wiki
basic-memory sync
basic-memory tool search-notes --query "kerberoasting"   # returns the page
```

## 5. Gate 1 — Git↔index sync (T-0.5)

```powershell
python spikes\gate1_git_sync\run_gate1.py --writers 2 --iterations 20
```
Read the transcript. Record PASS / PASS-with-Postgres / FAIL and the
SQLite-vs-Postgres decision in `GATE_RESULTS.md`.

> The harness appends spike commits to `wiki/log.md`. Reset afterward:
> `git checkout wiki/log.md` (or keep them — they're harmless markers).

## 6. Gate 2 — sources[] passthrough (T-0.6)

```powershell
python spikes\gate2_sources_passthrough\run_gate2.py --page wiki/techniques/adcs-esc8.md
```
Uses the two-source page on purpose. If `sources[]` changed, the harness
prints the diff and the verdict flips to "activate sidecar fallback"
(design.md §3). Record it.

## 7. Gate 4 — Vietnamese recall (T-0.7)

```powershell
# Both backends (needs fastembed installed AND litellm up):
python spikes\gate4_vietnamese_recall\run_gate4.py

# One backend only:
python spikes\gate4_vietnamese_recall\run_gate4.py --backends bge-m3
```
Compare **paraphrase-only recall** between backends — that column, not
overall recall, is what decides whether FastEmbed default is good enough
for Vietnamese or wiki-search should unify on bge-m3 (R-2.6).

## 8. Close Phase 0

When all four rows in `GATE_RESULTS.md` have conclusions:
- If any gate forced a branch, edit `design.md` first.
- Then flip the Phase 0 boxes in `tasks.md` and start Phase 1.

## Offline check you can run anywhere (no infra)

```powershell
python spikes\_lib\vault.py   # R-1.2 tree check + per-page lint
```
This is the standing structural check; it passed at authoring time
(7 pages, PASS).
