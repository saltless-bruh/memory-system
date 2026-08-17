# SNP Memory System — Runbook vận hành (V1)

Tài liệu vận hành cho phòng ban. Tập trung vào **ranh giới no-egress** (mục
quan trọng nhất — R-8.3) và các thao tác cơ bản.

---

## 1. Ranh giới no-egress — ĐỌC TRƯỚC (R-8.3)

> **Câu chốt:** đảm bảo "không ra internet" của hệ thống **chỉ phủ model của
> CHÍNH hệ thống**. **Model của coding-agent (trong IDE của member) nằm NGOÀI
> ranh giới.** Muốn no-egress tuyệt đối → member phải chạy model agent local.

```
   ┌─────────────────────── NGOÀI RANH GIỚI ───────────────────────┐
   │  Coding agent model (Claude / GPT / … trong IDE của member)   │
   │  → đọc trang wiki + chunk RAG rồi suy luận. Nếu là model cloud │
   │    thì NỘI DUNG member xem SẼ RA cloud của nhà cung cấp đó.    │
   └───────────────────────────────┬───────────────────────────────┘
                                   │ MCP (search/read, rag_fetch)
   ══════════════════ RANH GIỚI HỆ THỐNG (local-only) ══════════════
   │  basic-memory · Scout · rag (RAG-Anything) · LiteLLM · Gitea  │
   │  MỌI lời gọi model CỦA HỆ THỐNG → LiteLLM → Ollama local      │
   │  (bge-m3 embed · LLM entity-extract · VLM parse) — 0 egress   │
   ══════════════════════════════════════════════════════════════
```

**Vì sao phân biệt:** hệ thống (Scout/RAG/basic-memory) tự nó không gửi gì ra
ngoài — đã chứng minh: cắt mạng, các lời gọi embed/LLM/VLM qua LiteLLM vẫn
chạy (T-0.2). Nhưng khi agent **đọc** kết quả (trang wiki, đoạn RAG) để trả
lời, dữ liệu đó đi tới **nơi model agent chạy**. Nếu member dùng Claude/GPT
cloud, coi như nội dung đã rời máy.

**Chính sách cho phòng ban:**
- **Bí mật vừa:** dùng model agent cloud OK, nhưng ghi rõ "nội dung tra cứu
  có thể ra cloud của nhà cung cấp agent".
- **No-egress tuyệt đối (bắt buộc):** member **PHẢI** dùng model agent
  **local** (vd Ollama/llama.cpp trong IDE), không dùng model cloud.
- Việc này **không** do hệ thống ép được — nó nằm ở cấu hình IDE của từng
  member. Maintainer phải truyền đạt rõ.

**Kiểm chứng no-egress của HỆ THỐNG (không phủ agent):**
1. Chặn outbound của host (hoặc rút NIC ngoài), giữ localhost.
2. Gọi embedding/LLM/VLM qua LiteLLM (`/v1/embeddings`, `/v1/chat/completions`)
   → vẫn trả kết quả (route về Ollama local).
3. Nếu có lời gọi thất bại vì cần internet → có egress ẩn, phải soát lại.

---

## 2. Cổng & ranh giới mạng

| Service | Cổng | Ai gọi được |
|---|---|---|
| Gitea | `3000` (host) | member (clone/push/Web UI) |
| LiteLLM | `4000` (host) | hệ thống + kiểm thử; route về Ollama |
| **scout** MCP (`rag_fetch`) | `8080` (host) | IDE của member — **cửa DUY NHẤT vào rag** (R-4.1) |
| **basic-memory** MCP (container) | `8765` (host) | IDE của member (search/read wiki) |
| **rag** (RAG-Anything) | **KHÔNG publish** | **CHỈ scout** (mạng compose nội bộ) — R-4.2 |
| **sync-job** | KHÔNG publish | nội bộ; watch `raw/` → `rag /index` (T-3.2) |
| Ollama | `11434` (host) | LiteLLM + litellm-provider của basic-memory (bge-m3) |

> `rag` **không** mở cổng ra host là **cố ý** (R-4.2): agent không có đường
> chạm RAG trực tiếp, mọi truy hồi phải qua `scout` (`rag_fetch`, cổng 8080).
> IDE của member nối vào **2** MCP: `scout` (8080, dẫn chứng RAG) và
> `basic-memory` (8765, tra/đọc wiki).

---

## 3. Thao tác cơ bản

```bash
# Bật FULL stack (host cần Ollama chạy sẵn với bge-m3 + 1 LLM + 1 VLM).
# Dựng: gitea, litellm, rag, scout, sync-job, basic-memory (T-3.8).
docker compose up -d --build

# basic-memory chạy TRONG container (MCP :8765) — KHÔNG cần `bm mcp` trên host
#   nữa. Config bake sẵn từ docs/basic-memory-setup.md (bge-m3 qua litellm,
#   permalink OFF); vault bind-mount READ-ONLY nên không bao giờ bị sửa (R-2.5).
#   Lần đầu container quét toàn vault → import entity → embed (mất ít phút).

# Nạp RAG (Nhịp A): CHỈ cần thả file vào raw/ — `sync-job` tự gọi rag /index,
#   KHÔNG thao tác tay (T-3.2). Không còn bước POST /index thủ công.

# Tắt
docker compose down
```

Chi tiết cài đặt engine wiki (config bắt buộc để không mutate vault + recall
tiếng Việt): **`docs/basic-memory-setup.md`**. Ảnh Docker của engine bake đúng
config này (`basic-memory/config.json`, `basic-memory/Dockerfile`).

---

## 4. Ghi chú giấy phép (AGPL)

basic-memory là **AGPL-3.0** — mảnh AGPL duy nhất trong stack (còn lại MIT/
Apache). Dùng nội bộ self-host thì điều khoản network của AGPL không vướng.
Quyết định gate: `spikes/gate3_agpl_license/DECISION_MEMO.md` (hiện: ALLOWED
tạm thời để đánh giá — xác nhận policy chính thức trước khi productionize).

---

## 5. Hướng V2 (không bật ở V1)

Privilege lock theo role, thay RAG engine (RAG-Anything → R2R / pgvector+RLS),
lock service, UI. Xem `docs/proposal/Suggestion_V2_RAG_Replacement.md` và
proposal §5.
