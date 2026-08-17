# LLM-Wiki — Blueprint (chỉ lớp Wiki)

| | |
|---|---|
| **Phạm vi** | **CHỈ lớp LLM-Wiki** (lớp RAM của SNP Memory System). RAG-Anything, Scout-RAG-bridge, LiteLLM = **hàng xóm**, chỉ vẽ ở đường seam. |
| **Nguồn** | `Proposal_SNP_Memory_System_v2.md` (v2.2) + `design.md` |
| **Engine** | PRIMARY **basic-memory** (AGPL-3.0, self-host) · FALLBACK **Scout-DIY** — thay nhau trên cùng vault |
| **Quy mô** | Bounded **~100–500 trang** (nhỏ có chủ đích) |

> **Ranh giới (đọc trước):** LLM-Wiki là **lớp RAM** — tri thức đã compile, người + AI đọc–ghi, lưu Markdown trong Git. Nó **KHÔNG** chứa nguồn gốc thô (việc của RAG) và **KHÔNG** làm truy hồi RAG (việc của Scout-RAG-bridge). Thứ duy nhất LLM-Wiki phơi ra cho phần còn lại của hệ thống là **địa chỉ `sources[]`** trong mỗi trang. Mọi thứ sau địa chỉ đó nằm **ngoài blueprint này**.

---

## 1. LLM-Wiki là gì (một câu) + cái gì IN/OUT

**Một câu:** một wiki Markdown-trong-Git, bounded, mà cả người lẫn coding agent đọc–ghi được; mỗi trang là tri thức *đã compile* (mật độ cao, khẳng định) và mang một **địa chỉ** trỏ xuống nguồn gốc trong RAG.

| IN (blueprint này lo) | OUT (hàng xóm, chỉ nhắc ở seam) |
|---|---|
| Cấu trúc vault (§3) | RAG-Anything (KG/VDB, parse, query) |
| Schema trang / frontmatter (§4) | Scout-RAG-bridge (post-filter, `only_need_context`) |
| Engine slot: basic-memory ↔ Scout-DIY (§5) | LiteLLM gateway (trừ embedding của wiki-search) |
| `index.md` + lint (§6) | Sơ đồ toàn hệ thống (xem proposal §2.1) |
| Vòng đời tri thức (§7) | |
| Đường đọc / đường ghi trong wiki (§8) | |
| Hợp đồng seam `sources[]` ra RAG (§9) | |

---

## 2. Sơ đồ kiến trúc LLM-Wiki (ASCII)

```
   CONSUMERS
   ┌───────────────────────────┐                 ┌────────────────────────────────────────┐
   │ Coding Agent (IDE)        │                 │ Con người: Git IDE · Web UI · "bảo AI"  │
   │  MCP: wiki_search / read  │                 │  commit / Pull Request  (PR-first §8)   │
   └───────────┬───────────────┘                 └───────────────────┬────────────────────┘
               │ ĐỌC (read path §8)                                  │ GHI — thẳng vào Git
               ▼                                                     │  (engine KHÔNG chắn ghi)
   ╔══════════════════ ENGINE SLOT — cắm ĐÚNG 1 trong 2 ═══════════╗  │
   ║  HỢP ĐỒNG CHUNG (đổi engine, agent KHÔNG đổi):                ║  │
   ║    wiki_search(q,k) → [{page_id,path,title,summary,score}]    ║  │
   ║    wiki_read(id|path) → {frontmatter, body}                   ║  │
   ╠══════════════════════════════════════════════════════════════╣  │
   ║  ● PRIMARY — basic-memory   (AGPL-3.0 · self-host · MCP)      ║  │
   ║    ┌────────────────────────────────────────────────────────┐║  │
   ║    │① BUILD  (sync --watch: file .md đổi → re-index)         │║  │
   ║    │   parse .md → frontmatter + [[wikilink]]/relations      │║  │
   ║    │   → KG (từ relations) + vector (FastEmbed, in-proc)     │║  │
   ║    │   + full-text  ──►  SQLite / Postgres                   │║  │
   ║    │② wiki_search →  hybrid full-text + vector → rank → top-K │║  │
   ║    │③ wiki_read   →  read_note / build_context (đi graph)    │║  │
   ║    └────────────────────────────────────────────────────────┘║  │
   ╠══════════════════════════════════════════════════════════════╣  │
   ║  ○ FALLBACK — Scout-DIY   (tự viết · bật nếu primary hụt gate)║  │
   ║    ┌────────────────────────────────────────────────────────┐║  │
   ║    │① BUILD  (file .md đổi → re-embed)                       │║  │
   ║    │   đọc mọi trang → embed(title+summary+entities)         │║  │
   ║    │   bge-m3 (qua LiteLLM)  ──►  vector store nhỏ           │║  │
   ║    │② wiki_search →  embed(query) → cosine vs vectors → top-K│║  │
   ║    │③ wiki_read   →  đọc .md THẲNG từ Git → parse            │║  │
   ║    └────────────────────────────────────────────────────────┘║  │
   ╠══════════════════════════════════════════════════════════════╣  │
   ║  BẤT BIẾN cả hai:  Index = DẪN XUẤT — xóa → rebuild từ file   ║  │
   ║  (0 mất mát) · vault là nguồn sự thật · KHÔNG engine nào giữ  ║  │
   ║  dữ liệu gốc riêng.                                           ║  │
   ╚══════════╤═══════════════════════════════════════════════════╝  │
              │ đọc file (read)                                       │ ghi file (write)
              │              ┌────────── watch / re-index ◄───────────┼──────────┐
              ▼              ▼                                        ▼          │
   ┌────────────────────────────────────────────────────────────────────────────┐
   │  GIT VAULT   =   NGUỒN SỰ THẬT   (per-department · versioned · ~100–500)   │
   │                                                                            │
   │    wiki/                                                                   │
   │      index.md         ← SINH TỰ ĐỘNG (gen_index.py) · deterministic        │
   │      techniques/ entities/ playbooks/ concepts/   ← trang tri thức         │
   │      archive.md       ← trang nguội (prune) · rời index                    │
   │      log.md           ← audit                                              │
   │                                                                            │
   │    MỘT TRANG .md  =  frontmatter (schema §4)  +  body  +  [[wikilink]]     │
   │    frontmatter.sources[]  ───────────────────────►  ĐỊA CHỈ (path+loc+hint)│
   └──────────────────────────────────────────────────────────────┬─────────────┘
                                                                  │  seam §9
                                                                  ▼
                                       ┌────────────────────────────────────────┐
                                       │  ra RAG  (Scout-RAG-bridge)            │
                                       │  ——  NGOÀI phạm vi blueprint này  ——   │
                                       └────────────────────────────────────────┘
```

**Đọc sơ đồ — engine slot:** cả hai engine phơi **đúng cùng hai hàm** (`wiki_search`/`wiki_read`), nên agent gọi y hệt dù đang chạy engine nào. Khác nhau chỉ ở **bên trong**:

| Bước | **basic-memory** (primary) | **Scout-DIY** (fallback) |
|---|---|---|
| **① BUILD** | `sync --watch`: parse `.md` → KG (từ `[[wikilink]]`/relations) + vector (FastEmbed) + full-text → SQLite/Postgres | file đổi → đọc trang → embed `title+summary+entities` (bge-m3) → vector store nhỏ |
| **② search** | **hybrid** full-text + vector → rank → top-K | vector-only: `embed(query)` → cosine → top-K |
| **③ read** | `read_note`/`build_context` — đọc trang + **đi graph** | đọc `.md` **thẳng** từ Git → parse |

Ba điều cốt lõi của sơ đồ: **(1)** engine là khe cắm thay được (index chỉ là **dẫn xuất** — xóa rồi rebuild từ file, 0 mất mát); **(2)** **ghi đi thẳng vào Git** — engine không chắn đường ghi, nó chỉ *watch* rồi re-index — nên Git vault là nguồn sự thật; **(3)** wiki chỉ phơi ra `sources[]` — phần dưới seam không thuộc wiki.

---

## 3. Cấu trúc vault

```
wiki/
├── index.md            # tự sinh — KHÔNG sửa tay
├── techniques/         # type: technique   — kỹ thuật (kerberoasting, ad-cs-esc8…)
│   └── <slug>.md
├── entities/           # type: entity      — người/tổ chức/hệ thống (acme-ad, dc01…)
│   └── <slug>.md
├── playbooks/          # type: playbook    — quy trình nhiều bước
│   └── <slug>.md
├── concepts/           # type: concept     — khái niệm nền (ntlm, kerberos…)
│   └── <slug>.md
├── archive.md          # trang nguội bị prune (rời index)
└── log.md              # audit trail (do agent/script ghi)
```

**Quy ước đặt tên (naming):**
- File & slug: **kebab-case**, không dấu, ASCII: `ad-cs-esc8.md`, `acme-ad.md`.
- `type` ⇄ thư mục: `technique`→`techniques/`, v.v. (1-1).
- Entity slug trong `entities:` cũng kebab-case, khớp tên file entity nếu có.
- Một khái niệm = **một** trang (không trùng lặp). Trùng chủ đề → gộp hoặc `[[link]]`.

---

## 4. Giải phẫu một trang (data model)

### 4.1. Frontmatter — hợp đồng cơ khí

```yaml
---
type: technique                 # enum: technique | entity | playbook | concept
title: AD CS ESC8               # tên hiển thị
summary: NTLM relay tới Web Enrollment của AD CS để lấy cert máy DC.
entities: [ad-cs, esc8, ntlm-relay, petitpotam]
department: redteam
sources:                        # ĐỊA CHỈ ra RAG (seam §9)
  - path: raw/reports/acme-2026-final.pdf
    loc:  "p.31-34"
    hint: "Acme ESC8 PetitPotam relay web enrollment DC certificate"
last_compiled: 2026-05-20
# supersedes: {page: <slug>, claim: "<khẳng định bị lật>"}   # V2
---
```

| Field | Kiểu | Bắt buộc | Ý nghĩa | Ai dùng |
|---|---|---|---|---|
| `type` | enum | ✔ | Phân loại + quyết định thư mục | wiki + basic-memory |
| `title` | string | ✔ | Tên hiển thị / neo index | wiki + basic-memory |
| `summary` | string (1 câu) | ✔ | Dòng `index.md` **và** vector định tuyến (fallback) | wiki + engine |
| `entities` | list[slug] | ✔ | Khái niệm chính; hỗ trợ tìm/lọc | engine |
| `department` | string | ✔ | Scope hook (V1 **chưa** enforce; hook cho RBAC V2) | wiki (V2: RBAC) |
| `sources` | list[{path,loc,hint}] | ✔* | **Địa chỉ ra RAG**; engine bỏ qua, Scout đọc | seam → Scout |
| `last_compiled` | date | ✔ | Lần compile gần nhất (phục vụ prune/supersede) | lifecycle |
| `supersedes` | {page,claim} | — (V2) | Đánh dấu tri thức bị thay | lint (V2) |

*`sources` **bắt buộc nếu** trang có nguồn gốc trong `raw/`. Trang `concept` thuần tổng hợp có thể để trống — nhưng khi có nguồn, phải có địa chỉ.

**Vì sao `summary` không được thiếu:** nó vừa sinh `index.md`, vừa là chuỗi được **embed để định tuyến** ở chế độ Scout-DIY. Thiếu → định tuyến trật, nhất là câu hỏi tiếng Việt không trùng từ khoá.

### 4.2. Body — cấu trúc chuẩn

```
## TL;DR                    # 2–4 câu, mật độ cao, KHẲNG ĐỊNH — không kể lể
## Technical Specifications # chi tiết đã compile (điều kiện, chuỗi lệnh, dấu hiệu…)
## Provenance              # nối về raw/ + ghi RÕ mâu thuẫn giữa nguồn (nếu có)
## Cross-References         # [[wikilink]] tới trang liên quan
```

### 4.3. Luật `[[wikilink]]`
- Chỉ đặt **trong body** (mục Cross-References + inline khi cần).
- Là **nguồn liên kết DUY NHẤT** — **không** có `related:` ở frontmatter (tránh hai nguồn lệch nhau).
- `[[wikilink]]` **chính là** `Relations` của basic-memory → nó tự dựng knowledge graph từ đây.
- Link tới slug/tiêu đề trang có thật; link gãy → **lint FAIL** (§6).

---

## 5. Engine slot — khe cắm thay thế được

Engine là lớp phủ lo **tìm / đọc / graph** trên vault. Nó **thay được** vì vault là Markdown thuần. Phần này mổ sâu: **hợp đồng chung** (5.1), **basic-memory** (5.2: components + workflow), **Scout-DIY** (5.3: components + workflow), **so kè components** (5.4), **cơ chế đổi engine** (5.5), **hòa hợp schema** (5.6), **vì sao thiết kế vậy** (5.7).

### 5.1. Hợp đồng engine (interface tối thiểu — cả hai PHẢI khớp)

Đây là **mặt cắt** mà agent thấy. Engine nào cũng phải phơi **đúng** shape này thì mới cắm vừa khe:

```
wiki_search(query: str, k: int)
    -> [ {page_id: str, path: str, title: str, summary: str, score: float} ]   # rỗng nếu không khớp

wiki_read(id: page_id | path)
    -> { frontmatter: {type,title,summary,entities,department,sources,...}, body: str }
    -> ERROR "not_found" nếu id không tồn tại
```

Quy ước bắt buộc:
- `wiki_search` trả **theo score giảm dần**, ≤ `k` phần tử; **không** kèm body (giữ nhẹ). Body lấy sau bằng `wiki_read`.
- `wiki_read` trả **frontmatter đã parse** (gồm `sources[]` nguyên vẹn) + `body` thô. Agent tự rút `sources[]` để đưa ra seam (§9).
- Cả hai **chỉ đọc**. Không hàm nào trong hợp đồng ghi vào vault (ghi đi đường Git, §8.2).
- **Bất biến chung:** index của engine là **dẫn xuất** — xóa sạch rồi rebuild từ file phải ra kết quả tương đương. Engine **không** giữ bản dữ liệu gốc nào ngoài file.

### 5.2. PRIMARY — basic-memory (mổ sâu)

#### 5.2.1. Components
| Component | Vai trò |
|---|---|
| **Sync watcher** (`sync` / `sync --watch`) | Theo dõi vault; file `.md` đổi → kích hoạt re-index. Có lệnh `basic-memory doctor` kiểm nhất quán file↔DB. |
| **Markdown parser** | Bóc mỗi trang thành: `frontmatter` · `[[wikilink]]`/**Relations** · **Observations** (`- [category] … #tag`) · body. |
| **Knowledge Graph (KG)** | Entity + quan hệ, dựng từ Relations/`[[wikilink]]`. Cho phép "đi graph" khi đọc. |
| **Vector index** | Embedding **FastEmbed** (in-process, local) cho semantic search. |
| **Full-text index** | Khớp từ khoá (FTS của SQLite / Postgres). |
| **DB backend** | **SQLite** (mặc định) hoặc **Postgres** (đỡ concurrency tốt hơn — hữu ích cho phòng ban). |
| **MCP server** | Phơi tool cho agent: `search_notes`, `read_note`, `build_context`, `write_note`, … (tên/đối số **xác nhận theo docs khi build**). |
| **`memory://` URL** | Cách định danh chuẩn của note/entity để tham chiếu nội bộ. |

> Lưu ý trung thực: đây là mô tả **chức năng** theo tài liệu basic-memory. Tên tool và hành vi chính xác **phải kiểm lại khi triển khai** (nằm trong spike §10.2).

#### 5.2.2. Sơ đồ nội bộ (dataflow)
```
   file .md đổi
        │  (sync --watch)
        ▼
   ┌──────────────┐   frontmatter / [[wikilink]]=Relations / Observations / body
   │   PARSER     │──────────────────────────────┬───────────────┬──────────────┐
   └──────────────┘                              ▼               ▼              ▼
                                          ┌───────────┐   ┌───────────┐  ┌───────────┐
                                          │    KG     │   │  VECTOR   │  │ FULL-TEXT │
                                          │(entities+ │   │(FastEmbed)│  │  (FTS)    │
                                          │ relations)│   └───────────┘  └───────────┘
                                          └─────┬─────┘         │              │
                                                └──────── DB backend (SQLite/Postgres) ──────┘
        MCP tools trên cùng:
        search_notes ─►②   read_note / build_context ─►③   write_note ─►(ghi file → vòng lại①)
```

#### 5.2.3. Workflows (theo mốc ①②③)
- **① BUILD / SYNC.** `sync --watch` bắt file đổi → parser bóc frontmatter + Relations + Observations → **upsert** vào KG, sinh **embedding** (FastEmbed) vào vector index, cập nhật **FTS**; xóa file → gỡ khỏi index. Kết quả: DB luôn phản chiếu vault. (Rủi ro: nếu commit Git rơi vào lúc watcher chưa kịp/đang bận → có thể lệch → dùng `doctor`; đây là **gate §10.2**.)
- **② SEARCH (`wiki_search`).** Nhận query → chạy **hybrid**: FTS (khớp từ khoá) **song song** vector (khớp ngữ nghĩa) → **hợp nhất + rank** → trả top-K {page_id, path, title, summary, score}. Đây là chỗ "câu hỏi tiếng Việt diễn giải lại vẫn ra đúng trang" — miễn model embedding đỡ tiếng Việt (gate §10.2).
- **③ READ (`wiki_read`).** Nhận id/path → `read_note` load nội dung; nếu cần ngữ cảnh liên kết, `build_context` **đi graph** theo Relations tới độ sâu N → trả {frontmatter, body}.
- **(GHI) `write_note`.** Khi agent tạo/sửa trang: `write_note` ghi file `.md` (có frontmatter) vào working tree → **vòng lại ①** (re-index). **Nhưng** file đó **vẫn phải** commit + PR (§8.2) — engine không tự merge.

### 5.3. FALLBACK — Scout-DIY (mổ sâu)

Bật khi basic-memory **rớt một gate** (§10.2). Cố tình **tối giản**: ở quy mô ~100–500 trang, không cần KG/FTS/DB riêng — chỉ **vector nhỏ + đọc file**.

#### 5.3.1. Components
| Component | Vai trò |
|---|---|
| **Builder / indexer** | Đọc mọi trang → soạn "chuỗi định tuyến" `title + summary + entities` → embed. |
| **Embedding client** | Gọi **bge-m3 qua LiteLLM** (local). |
| **Vector store nhỏ** | ~100–500 vector; map `vector → {page_id, path}`. Có thể in-memory / `sqlite-vec` / file phẳng. |
| **File reader** | Đọc `.md` **thẳng** từ Git working tree; parse frontmatter + body. |
| **Change watcher** | File đổi → re-embed đúng trang đó. |
| **MCP server** | Phơi **đúng** `wiki_search`/`wiki_read` (§5.1) — agent không thấy khác. |

#### 5.3.2. Sơ đồ nội bộ (dataflow)
```
   file .md đổi
        │
        ▼
   ┌──────────────┐   title + summary + entities
   │   BUILDER    │──────────────► embed(bge-m3 via LiteLLM) ──► ┌───────────────┐
   └──────────────┘                                             │ VECTOR STORE  │
                                                                │  (nhỏ)        │
   query ──► embed(query) ──► cosine similarity ───────────────►└──────┬────────┘
                                                                       │ top-K {page_id,path}
   wiki_read(id) ──► FILE READER: đọc .md thẳng từ Git ──► parse ──► {frontmatter, body}
```

#### 5.3.3. Workflows
- **① BUILD.** File đổi → builder đọc trang → embed `title+summary+entities` (bge-m3) → ghi vector + metadata vào store. Rẻ ở quy mô nhỏ; rebuild toàn bộ cũng nhanh.
- **② SEARCH (`wiki_search`).** `embed(query)` → **cosine** với mọi page-vector → sắp xếp → top-K {page_id, path, title, summary, score}. (Vector-only — **không** có nhánh FTS như basic-memory.)
- **③ READ (`wiki_read`).** Nhận id/path → **đọc file `.md` thẳng** từ Git → parse frontmatter + body → trả. "Đi graph" (nếu cần) suy trực tiếp từ `[[wikilink]]` trong body.

> Vì sao đơn giản mà đủ: quy mô bounded (§1). 500 vector là chuyện nhỏ; không cần bộ máy KG/FTS/DB. Đánh đổi: mất "đi graph" gọn của basic-memory và nhánh FTS — chấp nhận được cho một fallback.

### 5.4. So kè components (ai có gì)

| Component | basic-memory | Scout-DIY |
|---|---|---|
| Watcher (re-index khi file đổi) | ✔ (`sync --watch`) | ✔ |
| Parser frontmatter + `[[wikilink]]` | ✔ | ✔ |
| Knowledge Graph (đi graph) | ✔ | ✖ (suy từ `[[wikilink]]` khi cần) |
| Vector search | ✔ (FastEmbed, in-proc) | ✔ (bge-m3 qua LiteLLM) |
| Full-text search | ✔ (FTS) | ✖ (vector-only) |
| DB backend | SQLite / Postgres | vector store nhỏ (không DB riêng) |
| MCP server | ✔ (tool sẵn) | ✔ (tự phơi cùng contract) |
| `memory://` addressing | ✔ | ✖ (dùng path/slug) |
| Công sức | **cài + cấu hình** | **tự viết** (nhỏ) |

### 5.5. Cơ chế đổi engine (0 migration)

Đổi qua lại **không đụng một byte** trong `wiki/` — vì cả hai chỉ đọc/ghi Markdown, index là dẫn xuất:
```
1. Trỏ MCP của IDE sang engine kia (đổi endpoint/cấu hình).
2. Engine mới BUILD index từ chính vault đang có (rebuild từ file).
3. Xong — agent gọi y hệt wiki_search/wiki_read.
```
Không export/import dữ liệu, không đổi schema trang. Đây là **bằng chứng** cho "cược vào Markdown+Git, không cược vào tool".

**Embedding cho wiki-search:** ưu tiên **một model đỡ tiếng Việt cho cả wiki + RAG** (lý tưởng `bge-m3`). Nếu FastEmbed của basic-memory trỏ được sang model multilingual → dùng nó; nếu không, chấp nhận chênh recall giữa hai chế độ và ghi lại (gate §10.2).

### 5.6. Hòa hợp với convention riêng của basic-memory
- **Trùng, dùng chung:** `title`, `type`, `[[wikilink]]` (= Relations).
- **Field riêng của ta** (`summary`, `entities`, `department`, `sources`): kỳ vọng basic-memory giữ nguyên như **passthrough** (gate §10.2). Nếu nó viết lại/xoá field lạ → chuyển `sources[]` sang **block đánh dấu trong body** hoặc **sidecar `<slug>.sources.yml`**; hợp đồng frontmatter **không đổi**, chỉ đổi *nơi lưu*.
- `Observations` của basic-memory (`- [category] … #tag`) là **tuỳ chọn**, không bắt buộc trong schema của ta.

### 5.7. Vì sao thay được là điểm mạnh
Tri thức nằm ở **Markdown trong Git** → engine chỉ là lớp phủ. basic-memory còn trẻ hơn Gitea; nếu hụt (gate §10.2) thì gạt sang Scout-DIY với **0 migration** (§5.5). Ta cược vào Markdown+Git (bền), không cược vào một tool đơn lẻ.

---

## 6. `index.md` + lint (`gen_index.py`)

### 6.1. `index.md` — tự sinh, deterministic
`gen_index.py` gom mọi `summary` thành `index.md`, nhóm theo `type`. Cùng vault → cùng index (luôn khớp nội dung, không lệch).
```
# Chỉ mục Wiki  (TỰ SINH — không sửa tay)

## techniques
- [[ad-cs-esc8]] — NTLM relay tới Web Enrollment của AD CS…  · entities: ad-cs, esc8
- [[kerberoasting]] — Lấy TGS của service account có SPN…    · entities: kerberoasting, active-directory
## entities
- [[acme-ad]] — Rừng AD của khách hàng Acme…                · entities: acme, active-directory
## concepts
- ...
```

### 6.2. Luật lint (chạy mỗi lần merge)
| # | Kiểm | Kết quả |
|---|---|---|
| 1 | Frontmatter thiếu field bắt buộc | **FAIL** |
| 2 | `type` không thuộc enum | **FAIL** |
| 3 | `summary` rỗng | **FAIL** |
| 4 | `sources[].path` **không tồn tại trên đĩa** | **FAIL** |
| 5 | `[[wikilink]]` trỏ tới trang **không tồn tại** | **FAIL** |
| 6 | Trang không được link tới & không ai trỏ vào (**mồ côi**) | **WARN** |

> Lưu ý: lint (§6) kiểm **path có trên đĩa**; còn `hint` có **khớp KG của RAG** hay không là việc của `verify_addresses.py` ở seam (§9) — **hai lớp khác nhau**, đừng gộp.

---

## 7. Vòng đời tri thức

```
   NGUỒN (raw/)  ──compile──►  TRANG (active, trong index)  ──prune──►  archive.md (rời index)
                                     ▲                                        
                                     └── supersede (V2): ghi đè toàn file + khai `supersedes`
```

| Giai đoạn | Việc | V1? |
|---|---|---|
| **Compile** | Người/agent viết trang từ nguồn, **mint** địa chỉ (§9), mở PR | ✔ |
| **Lint** | Mỗi merge: `gen_index.py` kiểm (§6) | ✔ |
| **Prune** | Trang nguội (lâu không đụng / không link) → chuyển `archive.md` để `index.md` luôn nhỏ → giữ chi phí định tuyến thấp | ✔ (thủ công) → tự động ở later |
| **Supersede** | Tri thức bị thay: **ghi đè toàn file** + khai `supersedes` (KHÔNG append), lint kiểm | **V2** |

**Trạng thái trang:** `active` (trong `index.md`) ⇄ `archived` (trong `archive.md`, rời index). Prune chỉ dời khỏi index, **không xoá** — Git giữ lịch sử.

**Nguyên tắc chống tự-mâu-thuẫn:** khi một khẳng định bị lật, **thay** (ghi đè + `supersedes`), đừng **thêm** — tránh wiki tích mâu thuẫn theo thời gian.

---

## 8. Đường đọc & đường ghi

### 8.1. Đường đọc (read path)
```
agent → wiki_search(q, k) → top-K {page_id, path, summary, score}
agent → wiki_read(page)   → {frontmatter (gồm sources[]), body}
   ├─ nếu body ĐỦ trả lời      → trả lời + cite trang            [DỪNG]
   └─ nếu cần NGUYÊN BẢN gốc    → lấy sources[] → đưa ra seam (§9) [ra khỏi wiki]
```
- Đọc là **read-only** → nhiều agent đọc song song an toàn.
- Agent **không** nạp cả `index.md` vào context — nó **search** và chỉ nhận top-K (đây là điểm tiết kiệm token; chi tiết ở proposal §3.1).

### 8.2. Đường ghi (write path) — PR-first
```
Người kỹ thuật     : clone → sửa .md → commit
Người không rành Git: Git Web UI → sửa → Save (tạo commit)
Qua AI             : "cập nhật trang X" → agent viết (write_note/commit) → mở PR → người duyệt
```
- **Bất biến:** AI **KHÔNG** merge thẳng vào nhánh chính — **luôn PR + human review**.
- **Concurrency V1:** **không** có lock. PR + merge của Git đã serialize; xung đột giải khi review. **Lock tự động (L1/L2/L3) = V2.**
- basic-memory `write_note` ghi vào working tree → **vẫn phải** commit/PR (không auto-merge).

---

## 9. Seam ra RAG (hợp đồng outbound duy nhất)

LLM-Wiki phơi ra phần còn lại của hệ thống **đúng một thứ**: mỗi trang có `sources[]`, mỗi phần tử là một **địa chỉ**:
```
sources[i] = { path: <file trong raw/>, loc: <trang/dòng>, hint: <cụm từ để RAG khớp> }
```

**Quy tắc MINT (bắt buộc khi viết trang có nguồn):** địa chỉ phải **lấy từ chính RAG** — hỏi RAG về doc, lấy `file_path` + entity **thật** RAG tạo ra — **không viết tay theo phỏng đoán**. Vì RAG tự đặt tên entity theo KG của nó; đoán lệch từ vựng → địa chỉ **trả rỗng im lặng** (file vẫn còn, path-lint vẫn PASS, nhưng truy hồi vào ngõ cụt).

**`verify_addresses.py`** (chạy ở seam, sau merge): với mỗi `sources[]`, query RAG bằng `hint` → **PASS** (đúng nguồn) / **FAIL** (hint không khớp KG) / **DRIFT** (kéo được nhưng toàn file khác).

> Mọi thứ **sau** địa chỉ — `only_need_context=True`, post-filter theo `file_path`, KG/VDB của RAG — **KHÔNG** thuộc LLM-Wiki. Đó là Scout-RAG-bridge (xem `design.md` §2.3).

---

## 10. Bất biến & câu hỏi mở (riêng lớp Wiki)

### 10.1. Bất biến (invariants — đừng phá)
1. **Markdown + Git = nguồn sự thật.** Index của engine là **dẫn xuất**, rebuild được.
2. `[[wikilink]]` là **nguồn liên kết duy nhất** (không `related:`).
3. `summary` **bắt buộc**.
4. `sources[]` **mint từ RAG**, không viết tay.
5. AI ghi **qua PR**, không auto-merge.
6. Wiki **bounded ~100–500 trang**; điều hướng, **không** chứa nguồn thô.
7. Engine **thay được** — không lock-in.

### 10.2. Spike riêng lớp Wiki (phải chạy trước khi chốt basic-memory)
| Gate | Hỏi gì | Nếu rớt |
|---|---|---|
| **Git↔index sync** | Index basic-memory có nhất quán khi commit Git + nhiều writer? (`basic-memory doctor`) | Postgres backend; vẫn lệch → **Scout-DIY primary** |
| **`sources[]` passthrough** | basic-memory có giữ nguyên field lạ? | chuyển địa chỉ sang body-block/sidecar (§5.3) |
| **Recall tiếng Việt** | FastEmbed mặc định đủ recall câu hỏi VN không? | trỏ sang model multilingual / unify `bge-m3` |
| **AGPL policy** *(cấp hệ thống)* | Công ty cho phép phụ thuộc AGPL? | **Scout-DIY** thành primary |

---

## 11. Thứ tự dựng lớp Wiki (tóm tắt — chi tiết ở `tasks.md`)

1. Dựng **cây vault** (§3) + `AGENTS.md` (schema §4, luật `[[wikilink]]`, PR-first).
2. Viết **`gen_index.py`** (sinh `index.md` + lint §6).
3. Cài **basic-memory** self-host trỏ vào vault; phơi MCP (`wiki_search`/`wiki_read`) tới IDE.
4. **Chạy 4 spike** (§10.2) → quyết định primary basic-memory hay rẽ Scout-DIY.
5. Cấu hình **embedding wiki-search** theo kết luận spike.
6. Viết **Scout-DIY fallback** sau **cùng hợp đồng** `wiki_search`/`wiki_read` (§5.1) — đổi qua lại 0 migration.
7. Nối **đường ghi PR-first** (§8.2): `write_note`/commit → PR → review.

→ Ánh xạ task: `tasks.md` Phase 0 (T-0.3…T-0.7 spike) + Phase 1 (T-1.1…T-1.5) + T-2.4 (fallback engine).
