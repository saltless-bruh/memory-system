# LLM-Wiki — Tài liệu Kiến trúc Kỹ thuật

| | |
|---|---|
| **Version** | 1.2 — Technical Architecture |
| **Tác giả** | saltless-bruh |
| **Ngày** | 16/07/2026 |
| **Phạm vi** | Kiến trúc hệ thống, tech stack, workflow của embedded agent, giao diện với RAG-Anything, và luồng tương tác của end user. |

---

## 1. Mô hình khái niệm

Ba lớp, ánh xạ theo phần cứng máy tính:

```
   LLM   =  CPU       Model chính trong IDE của member. Suy luận, ra quyết định.
   Wiki  =  RAM       Tri thức đã compile. Nóng, điều hướng nhanh, người + AI cùng sửa.
   RAG   =  STORAGE   raw/ — nguồn gốc bất biến. Nguội, dung lượng lớn, agent chỉ đọc.
```

Nguyên tắc phân tách:

| | Wiki (RAM) | RAG (Storage) |
|---|---|---|
| Nội dung | Markdown đã compile | PDF, advisory, evidence, sheet gốc |
| Kích thước | ~100-500 trang | 5-10GB |
| Ghi | Người + AI, thường xuyên | Người thả vào, hiếm khi sửa |
| Đọc | Người + agent | **Chỉ agent** |
| Phạm vi | **Theo phòng ban** | **Dùng chung** |
| Truy cập | Điều hướng (index → page) | Truy hồi (semantic query) |

---

## 2. Tech Stack

| Lớp | Công nghệ | Vai trò | Lý do chọn |
|---|---|---|---|
| **Wiki base** | obsidian-second-brain (MIT, Python) | Vault người sửa được + AI bảo trì | Đa CLI, có sẵn semantic search local + MCP + fallback keyword |
| **Embedded agent** | Scout *(tự viết, ~400 dòng)* | Model tìm giùm agent chính | Khoảng trống chưa dự án nào lấp: token-saving + multilingual |
| **Embedding model** | bge-m3 (qua Ollama/LiteLLM) | Vector hoá query + trang | Multilingual — recall tiếng Việt |
| **Storage engine** | RAG-Anything (LightRAG) | Index + truy hồi `raw/` | Native multimodal: PDF/ảnh/bảng |
| **Model gateway** | LiteLLM | Chokepoint mọi lời gọi model | Chứng minh không ra cloud bằng 1 config |
| **Lưu trữ** | Git (GitLab/Gitea) | Version + audit trail | Ai/cái gì/lúc nào/agent-hay-người |
| **Lock** | Lock service *(tự viết, Python + Redis)* | L2/L3 concurrency | Wiki trên server → nhiều writer đồng thời |
| **Runtime** | Docker Compose | Đóng gói toàn bộ | Self-hosted, portable |

Ba thứ **tự viết**: Scout, Lock service, `AGENTS.md` (schema — văn bản). Còn lại cấu hình.

---

## 3. Kiến trúc hệ thống

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  END USER (thành viên phòng ban)                                              │
│                                                                                │
│   ┌────────────────┐   ┌────────────────┐   ┌────────────────┐                │
│   │ Coding Agent   │   │  Git Web UI    │   │  IDE / editor  │                │
│   │ Claude Code /  │   │ (xem/sửa/xóa   │   │  (sửa .md      │                │
│   │ Cursor / Cline │   │  qua browser)  │   │   trực tiếp)   │                │
│   └───────┬────────┘   └───────┬────────┘   └───────┬────────┘                │
└───────────┼────────────────────┼────────────────────┼─────────────────────────┘
            │ MCP                 │ HTTP                │ file I/O
            │ (wiki_search)       │ (commit)            │ (git commit)
            ▼                     ▼                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│  SERVER (self-hosted, Docker)                                                  │
│                                                                                │
│  ┌─────────────────────────────┐         ┌──────────────────────────────────┐ │
│  │  SCOUT  (embedded agent)     │         │  GIT REPO (per-department wiki)  │ │
│  │  ┌───────────────────────┐   │  đọc    │                                  │ │
│  │  │ 1. đọc index          │───┼────────►│  wiki/                           │ │
│  │  │ 2. định tuyến (vector)│   │  file   │   ├── index.md   (điều hướng)    │ │
│  │  │ 3. đọc trang đích     │◄──┼─────────│   ├── archive.md (trang nguội)   │ │
│  │  │ 4. rút address        │   │         │   ├── log.md     (audit)         │ │
│  │  │ 5. đóng gói + cite     │   │         │   ├── techniques/  entities/    │ │
│  │  └──────────┬────────────┘   │         │   └── playbooks/   concepts/    │ │
│  │             │ bge-m3         │         │        (frontmatter mang        │ │
│  │             ▼ (embedding)    │         │         ADDRESS → raw/)         │ │
│  └─────────────┼────────────────┘         └────────────────┬─────────────────┘ │
│                │                                            │ webhook on merge  │
│                │ query có path_filter                       ▼                   │
│                ▼                              ┌──────────────────────────────┐  │
│  ┌──────────────────────────────┐            │  SYNC JOB                     │  │
│  │  RAG-ANYTHING (Storage)      │◄───────────│  clone + index raw/          │  │
│  │  ┌────────────────────────┐  │  re-index  │  + sinh lại index.md         │  │
│  │  │ index vector + graph   │  │            └──────────────────────────────┘  │
│  │  │ trên raw/              │  │                                              │
│  │  └────────────────────────┘  │            ┌──────────────────────────────┐  │
│  │  raw/ reports/ advisories/   │            │  LOCK SERVICE (Redis)        │  │
│  │       evidence/ (chỉ agent)  │            │  L2: AI ⊥ AI cùng file       │  │
│  └──────────────┬───────────────┘            │  L3: human claim → HALT AI   │  │
│                 │ embedding + LLM             └──────────────────────────────┘  │
│                 ▼                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  LiteLLM  (AI Gateway — chokepoint DUY NHẤT)                             │ │
│  │  Scout ─► bge-m3    │    RAG-Anything ─► LLM + VLM    │  audit 1 config    │ │
│  │  ▼ route local-only                                                        │ │
│  │  llama.cpp / Ollama  (KHÔNG có cloud provider)                             │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Ba đường của end user vào hệ thống:**
- **Coding Agent → MCP → Scout**: hỏi, Scout tìm, trả payload. (đường chính, agent dùng)
- **Git Web UI → HTTP**: xem/sửa/xóa trang wiki trên browser. (người không rành git)
- **IDE/editor → file**: clone repo, sửa `.md`, commit. (người kỹ thuật)

**Scout là ranh giới giữa Wiki và RAG.** Agent chính không bao giờ gọi thẳng RAG-Anything; nó luôn đi qua Scout, và Scout quyết định khi nào cần xuống Storage.

---

## 4. Scout — workflow nội bộ của embedded agent

Scout là một **model nhỏ chạy local** đóng vai "thủ thư": agent chính giao việc tra cứu, Scout làm và trả về kết quả cô đặc.

```
   [Agent chính]  ──wiki_search("kerberoasting ở Acme?", caller)──►  [SCOUT]
                                                                        │
   ┌────────────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ BƯỚC 1 — SCOPE CHECK (authz hook)                                           │
│   caller.departments quyết định thấy trang nào.                             │
│   v1: enforce=False (thấy hết) · Phase 2: enforce=True (lọc theo phòng ban) │
│   → chống rò rỉ ngữ nghĩa xuyên phòng ban ngay từ tầng tìm kiếm.            │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ BƯỚC 2 — ĐỊNH TUYẾN (vector, trên METADATA không phải body)                 │
│   embed(query) qua bge-m3 → cosine với vector của từng trang.               │
│   Vector trang = title + summary + entities + hints  (KHÔNG phải toàn body).│
│   → chọn top-K trang khớp nhất. Đây là "page table" của wiki.               │
│   [chi phí token của bước này nằm ở SCOUT, không ở agent chính]             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ BƯỚC 3 — ĐỌC TRANG ĐÍCH + RÚT ADDRESS                                       │
│   Đọc top-K trang. Lấy frontmatter.sources[] — địa chỉ trỏ về raw/.         │
│   Nếu nội dung trang đã đủ trả lời → DỪNG. Không xuống RAG.                  │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  ▼ (chỉ khi trang chưa đủ)
┌─────────────────────────────────────────────────────────────────────────────┐
│ BƯỚC 4 — LẤY NGUỒN GỐC (§5 mô tả giao diện chi tiết)                        │
│   Route A: path là .md/.txt        → đọc thẳng file (0 token đi tìm).       │
│   Route B: path là PDF/binary      → gọi RAG-Anything, CÓ path_filter.      │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ BƯỚC 5 — ĐÓNG GÓI + TRÍCH DẪN                                               │
│   Ghép trang wiki + chunk nguồn thành payload cô đặc.                        │
│   MỖI mảnh kèm citation (trang nào / file nào / dòng nào).                   │
│   ⚠ Scout CHỈ TRÍCH + CITE. KHÔNG tóm-tắt-rồi-ra-lệnh.                       │
│      (raw/ chứa payload kẻ tấn công — chống prompt injection)               │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  ▼
   [Agent chính]  ◄──── { context, citations[], pages_read[] } ────┘
```

**Đo được từ prototype** (câu hỏi *"kerberoasting ở Acme?"*):

```
   Scout tốn định tuyến : 578 token   ← chạy trên model local của Scout
   Agent chính nhận     : 401 token   ← chỉ payload + citation
```

Chi phí đọc index (300 trang ≈ 13K token) **không bao giờ** vào context agent chính.

---

## 5. Giao diện Scout ↔ RAG-Anything

### 5.1. RAG-Anything thật sự là gì

RAG-Anything **không phải** một API lấy chunk theo file. Nó là **pipeline hoàn chỉnh**:

```
   Input Documents (PPT/DOC/JPG/PNG/XLS/PDF)
        │  Parallel Parser
        ▼
   Multi-modal Content Parsing
     Hierarchical Text Extraction ─┐
     Image Caption & Metadata     ─┼─► Structured Content List
     LaTeX Equation Recognition   ─┤    (Text · Image · Equation · Table Info)
     Table Structure & Content    ─┘
        │
        ▼  Multi-modal Processors (VLM/LLM)  →  "Textual Multi-modal Info"
        │                                       (ảnh/bảng/công thức → TEXT trước)
        ▼
   Graph-based Multi-modal Knowledge Grounding  (mỗi document)
     Entity & Relation Extraction ──► Knowledge Graph ──┐
     Text Encoder                 ──► Vector Database ──┼─► MERGED
                                                        │   "KG over All Documents"
                                                        │   "VDB over All Documents"
        │
        ▼  QUERY
     Query → High-/Low-level Keys Extraction ──► Graph-based Retrieval  ─┐
     Query → Text Encoder                    ──► Embedding Retrieval    ─┼─► Retrieved Info
                                                                         │        │
                                                                         │        ▼ LLM
                                                                         │    Response
```

**Ba điều rút ra, ảnh hưởng trực tiếp tới thiết kế:**

1. **Nó merge MỌI document vào MỘT KG + MỘT VDB.** "Merged Node", "KG over All Documents". Gộp xuyên tài liệu là **tính năng cốt lõi**, không phải tác dụng phụ.
2. **Multimodal → text trước.** VLM/LLM biến ảnh/bảng thành "Textual Multi-modal Info" rồi mới grounding. "Multimodal" nghĩa là *parse ra text rồi xử lý như text*.
3. **Đầu ra mặc định là Response do LLM của NÓ sinh ra**, không phải đoạn gốc.

### 5.2. API thật — và một đính chính

**API thật:**

```python
rag.query(text, QueryParam(mode="mix", only_need_context=True))
```

`mode`: `naive` (vector) · `local` (entity) · `global` (KG relation) · `hybrid` · `mix`.

> **Đính chính:** bản nháp trước của tài liệu này ghi `rag_query(path_filter=..., query_hint=...)`. **`path_filter` KHÔNG tồn tại.** RAG-Anything merge toàn bộ corpus nên **không lọc trước theo file được**. (Lọc `ids` chỉ có với PG vector DB, và theo doc-id nội bộ chứ không theo đường dẫn `raw/`.)

**Hai quyết định tích hợp:**

| Quyết định | Vì sao |
|---|---|
| **`only_need_context=True` — BẮT BUỘC** | Để mặc định thì RAG-Anything **tự chạy LLM của nó** → hai model cùng suy luận (thừa), và Scout **mất chuỗi trích dẫn** (nhận văn tổng hợp thay vì đoạn gốc). Bật lên → trả về **đoạn gốc**. Đây cũng là hàng rào injection: context là **dữ liệu**, không phải lệnh. |
| **POST-FILTER theo `file_path`** | Không lọc trước được → Scout lọc **sau**. RAG trả context kèm `file_path`; Scout chỉ giữ đoạn thuộc đúng nguồn mà trang wiki trỏ tới. |

### 5.3. Luồng thật của Scout

```
   address:  path: raw/reports/acme-2026-final.pdf
             hint: "Acme kerberoasting service account SPN"
        │
        ├── .md / .txt ──► ĐỌC THẲNG file  (deterministic, 0 token tìm kiếm)
        │
        └── .pdf/binary ─► rag.query(hint, mode="mix", only_need_context=True)
                               │
                               ▼  RAG tìm trên TOÀN corpus (không lọc trước được)
                           [ {file_path: acme-2026-final.pdf, content: ...},
                             {file_path: globex-2025.pdf,     content: ...},   ← khách khác
                             {file_path: generic-kerb.md,     content: ...} ]
                               │
                               ▼  SCOUT POST-FILTER theo file_path == address.path
                           chỉ giữ chunk của acme-2026-final.pdf  ──► payload
```

**Đã test:** RAG trả về chunk của **Globex** (khách hàng khác) → Scout **loại bỏ**, chỉ Acme vào payload.

> **Giới hạn cần nói thẳng:** RAG **vẫn đã retrieve** Globex — post-filter chỉ chặn nó tới agent. Đây là **containment ở ranh giới Scout**, không phải isolation trong RAG. Với v1 (một RAG general, chưa nạp data khách hàng) là đủ. Nhưng đó là lý do "phân quyền theo role" ở Phase 2 **không thể chỉ là một filter** — sẽ cần tách instance theo `working_dir`.

### 5.4. Address phải được MINT từ RAG, không viết tay

Cùng một document bị **hai quá trình trích xuất độc lập**:

```
   raw/reports/acme.pdf
        ├─► RAG-Anything: Entity & Relation Extraction → KG nodes:
        │                    "Kerberoasting" · "SPN" · "Service Account" · "Acme AD"
        └─► Agent compile → trang wiki → entities: [kerberoasting, service-account]
                                          hint: "..."
```

**Không ai bảo đảm hai bên dùng cùng từ vựng.** LightRAG rút *high-/low-level keys*
từ `hint` rồi khớp vào **KG của nó**. Lệch từ vựng ⇒ address **trả rỗng, im lặng** —
file vẫn tồn tại, lint đường dẫn vẫn PASS, nhưng agent đi vào ngõ cụt.

**Quy tắc:**

| Bước | Việc |
|---|---|
| 1 | Doc mới vào `raw/` → RAG index |
| 2 | Hỏi RAG về doc đó → lấy `file_path` + **entity THẬT** RAG trả về |
| 3 | Viết trang wiki dùng **chính** entity + file_path đó |
| 4 | `verify_addresses.py` → chứng minh `hint` kéo đúng nguồn |

`verify_addresses.py` phân ba trạng thái: **PASS** (đúng nguồn) · **FAIL** (hint
không khớp KG) · **DRIFT** (kéo được context nhưng toàn file khác). Đã test: hint
tiếng Việt lệch từ vựng → FAIL, exit 1.

> **Vì sao cần tool riêng:** `gen_index.py` chỉ kiểm `path` **tồn tại trên đĩa**.
> Nó không biết `hint` có kéo được gì từ RAG hay không. Hai lớp kiểm khác nhau.

### 5.5. Topology: nhiều Wiki → MỘT RAG

```
   wiki-redteam/   ─┐
   wiki-blueteam/  ─┼──►  RAG-Anything  (một, general, dùng chung)
   wiki-appsec/    ─┘         raw/ — nguồn gốc, agent đọc

   v1:      MỘT wiki + MỘT RAG.
   Tương lai: thêm wiki của phòng ban khác → vẫn nối vào CÙNG RAG đó.
```

`department` trong frontmatter (§6) là hook để Scout scope theo phòng ban khi nhiều wiki cùng tồn tại.

## 6. Data contract — frontmatter (schema hợp nhất)

Frontmatter là **liên kết cơ khí** giữa Wiki và RAG. Schema này hợp nhất phần
tốt nhất của hai bản nháp: **frontmatter có `title`/`summary`/`department`** (cần
cho định tuyến + scope), **body theo mẫu mật độ cao** (khẳng định, không kể lể),
và **liên kết bằng `[[wikilink]]` trong body** (nguồn sự thật duy nhất, hợp Obsidian).

```yaml
---
# ===== HỢP ĐỒNG CƠ KHÍ — KHÔNG đổi tên trường =====
type: technique                    # technique | entity | playbook | concept
title: Kerberoasting               # tên hiển thị index
summary: Lấy TGS của service account có SPN rồi crack offline.
                                   # ← dòng index.md + VECTOR ĐỊNH TUYẾN của Scout. BẮT BUỘC.
entities: [kerberoasting, active-directory, service-account]
department: redteam                # scope hook (Scout §4 bước 1)
sources:                           # ADDRESS xuống RAG
  - path: raw/reports/acme-2026-final.pdf
    loc:  "p.12-14"
    hint: "Acme kerberoasting service account SPN"
supersedes:                        # STRUCTURED — lint kiểm được cả hai dạng
  - page:  techniques/old-kerberoast.md    # trang bị thay (lint: phải tồn tại)
  - claim: "Acme DC IP là 10.0.0.1"        # claim bị lật (lint: đánh dấu REVIEW)
last_compiled: 2026-07-16
---

## TL;DR                 mật độ cao, khẳng định, KHÔNG kể lể
## Technical Specifications   tri thức đã compile ("RAM")
## Provenance            nối về raw/ + GHI RÕ mâu thuẫn giữa nguồn
## Cross-References      [[wikilink]] thuần — KHÔNG chỉ thị routing
```

**Vì sao `summary` bắt buộc (đo được):** nó là thứ Scout embed để định tuyến. Bỏ
`summary` → vector định tuyến chỉ còn entity-slug + hint. Test trên cùng wiki:
câu hỏi tiếng Việt KHÔNG trùng từ khoá (*"cách lấy mật khẩu tài khoản dịch vụ
trong AD"*) → có `summary` định tuyến ĐÚNG trang kerberoasting; bỏ `summary` →
định tuyến SAI sang trang khác. Đây đúng là case §4 nói hệ thống sinh ra để giải.

**Vì sao `[[wikilink]]` ở body chứ không ở frontmatter:** base là Obsidian vault,
graph view đọc wikilink trong body. Để `related:` ở frontmatter = hai nguồn sự
thật cho cùng một liên kết → sẽ lệch. Một nguồn duy nhất: body.

`index.md` sinh tự động từ các `summary` (deterministic), nên luôn khớp nội dung.
`gen_index.py` đồng thời lint: địa chỉ RAG gãy, wikilink gãy, `supersedes.page`
gãy, và đánh dấu `supersedes.claim` cho người review.

## 7. Write model & Lock

Wiki trên server, nhiều người + agent cùng ghi. Ba loại writer:

```
   HUMAN ────► sửa trực tiếp (Git UI / IDE)  ──► commit ──► main
   AI ────────► đề xuất  ────────────────────► PR ──► human review ──► main
   SCOUT ─────► CHỈ ĐỌC. Không bao giờ ghi.
```

**Quy tắc:**

| Luật | Cơ chế |
|---|---|
| **L1** Human sửa → AI chỉ suggest | Agent không push thẳng `main`; chỉ mở PR. Branch protection enforce. |
| **L2** AI sửa → AI khác không cùng file | Lock service: per-file mutex trên `wiki/<path>`. |
| **L3** Human interfere khi AI đang sửa | Human claim → lock service gửi HALT → agent dừng, bỏ branch. |
| **Chống tự mâu thuẫn** | Sự thật thay thế → ghi đè toàn file + khai `supersedes:`, KHÔNG append. |

**Vì sao cần lock riêng** (không dựa vào git merge): git merge cứu được text, nhưng hai agent ghi cùng file cùng lúc vẫn tạo branch xung đột và tốn công. Lock service ngăn từ đầu. *(Ba dự án open-source đều bỏ ngỏ chỗ này — MehmetGoekce nói thẳng "coi file wiki là shared resource" mà không giải.)*

---

## 8. Luồng tương tác end user

### 8.1. Query (đường chính — agent hỏi)

```
   Member gõ trong IDE: "AD CS ESC8 ở target 10.0.0.10 khai thác sao, team làm chưa?"
        │
        ▼  agent chính gọi MCP tool wiki_search(...)
   [SCOUT]  index → playbooks/adcs-abuse.md → address → RAG(path_filter=acme.pdf)
        │
        ▼  payload cô đặc + citation
   Agent chính: tổng hợp câu trả lời, kèm nguồn:
     • playbooks/adcs-abuse.md (Phase 2: ESC8 relay)
     • raw/reports/acme-2026-final.pdf p.45-62 (team đã làm trên 10.0.0.10)
```

Member **không** thấy Scout, không thấy 13K token index. Chỉ thấy câu trả lời có dẫn chứng.

### 8.2. Ingest (thêm tri thức mới)

```
   Member thả report mới vào raw/  ──► commit
        │
        ▼  webhook
   SYNC JOB: RAG index file mới
        │
        ▼
   Agent (đường A/B/C của §7): đọc report → cập nhật các trang wiki liên quan → PR
        │
        ▼
   Human review PR → merge → index.md tự sinh lại
```

### 8.3. Ba cách con người sửa wiki

```
   Không rành git   ──► Git Web UI: mở trang, bấm sửa, Save.       (0 setup)
   Kỹ thuật         ──► clone repo, sửa .md trong IDE, commit.
   Qua hội thoại    ──► bảo agent "cập nhật trang X" → agent mở PR → mình duyệt.
```

### 8.4. Lifecycle tri thức

```
   raw/ (nguồn)  ──compile──►  wiki (RAM, per-dept)  ──query──►  agent trả lời
                                     │
                                     ├── lint: mâu thuẫn, trang mồ côi, address gãy
                                     └── prune: trang nguội → archive.md (index nhỏ lại)
```

---

## 9. Deployment topology (Docker)

```
   docker compose:
   ┌─ scout          (embedded agent + bge-m3 client)
   ├─ rag            (RAG-Anything trên raw/)
   ├─ rag-mcp        (MCP: Scout ↔ RAG)
   ├─ litellm        (gateway → llama.cpp/Ollama local)
   ├─ lock-svc + redis
   ├─ sync-job       (webhook → index + gen index.md)
   └─ git            (GitLab nếu công ty đã có → bỏ service này)

   Wiki KHÔNG cần container riêng — nó là file trong git repo,
   agent đọc trực tiếp, Scout đọc trực tiếp.
```

---

## 10. Ranh giới & những gì chưa chốt

**Ranh giới thiết kế (cố ý):**
- Agent chính không gọi RAG trực tiếp — luôn qua Scout.
- Scout chỉ đọc, không ghi.
- RAG chỉ index `raw/`, không index `wiki/`.
- Wiki điều hướng (index), RAG truy hồi (vector). Không lẫn.

**Chưa verify (cần test trên data thật):**
- Recall tiếng Việt của bge-m3 trên corpus team.
- obsidian-second-brain (vốn cho cá nhân) chịu tải nhiều người trên server.
- Độ trễ Scout khi N người hỏi đồng thời.
- Chống prompt injection từ `raw/` qua Scout.
- Chi phí index lần đầu (chạy corpus_survey.py khi có data).

**Phase 2 (đã có hook, chưa bật):**
- Scope enforce theo phòng ban trong Scout.
- RBAC trên RAG (hiện dùng chung).
- Presence plugin thay claim command thủ công.
```
