# Scout — Blueprint (RAG bridge + engine fallback)

| | |
|---|---|
| **Phạm vi** | **CHỈ Scout** — mảnh tự viết nối Wiki ↔ RAG. Nội bộ RAG-Anything (KG/VDB/parse) và nội bộ engine wiki = **hàng xóm**, chỉ vẽ ở đường seam. |
| **Nguồn** | `Proposal_SNP_Memory_System_v2.md` (v2.2) + `design.md` + `LLM-Wiki_Blueprint.md` |
| **Hai vai** | **VAI 1 — RAG BRIDGE** (luôn bật, engine-independent) · **VAI 2 — WIKI ENGINE FALLBACK** (chỉ khi basic-memory rớt gate) |

> **Ranh giới (đọc trước):** Scout là **thủ thư** — nó **không** lưu tri thức (việc của Wiki) và **không** index nguồn thô (việc của RAG-Anything). Nó chỉ **cầm địa chỉ** từ một trang wiki rồi **lấy đúng đoạn nguyên bản** từ RAG, kèm trích dẫn. Mọi thứ *bên trong* RAG (parse, KG, VDB, embedding của RAG) nằm **ngoài blueprint này**; Scout chỉ gọi `rag.query(...)`.

---

## 1. Scout là gì (một câu) + IN/OUT

**Một câu:** một tiến trình nhỏ, chỉ-đọc, đứng giữa Wiki và RAG; **VAI 1** biến một *địa chỉ* (`path+hint` lấy từ frontmatter của trang) thành *đoạn gốc có trích dẫn*; **VAI 2** (dự phòng) kiêm luôn việc tìm/đọc trang wiki nếu engine chính bị bỏ.

| IN (blueprint này lo) | OUT (hàng xóm, chỉ nhắc ở seam) |
|---|---|
| VAI 1 — RAG bridge: nhận address → query → **post-filter** → cite (§3) | Nội bộ RAG-Anything: parse, KG, VDB, `only_need_context` cơ chế (chỉ *gọi*) |
| VAI 2 — wiki-engine fallback (§4) | Cấu trúc/── schema trang wiki (xem `LLM-Wiki_Blueprint.md`) |
| Hợp đồng MCP `rag_fetch` (§5) | LiteLLM gateway (chỉ *dùng* khi fallback embed) |
| Vì sao **post-filter** bắt buộc (§6) | Sơ đồ toàn hệ thống (proposal §2.1) |
| Chống prompt-injection (§7) | |
| Bất biến + rủi ro + spike (§8–§9) | |

**Vì sao Scout tồn tại (một dòng):** RAG-Anything **merge mọi tài liệu vào một KG + một VDB** → **không có `path_filter`**; muốn trả **đúng nguồn của đúng trang** phải có ai đó **post-filter theo `file_path`** và **chỉ trích + cite** (chống injection). Đó là Scout.

---

## 2. Sơ đồ kiến trúc Scout (ASCII)

```
   ┌────────────────────────────────────────────────────────────────────────┐
   │  CODING AGENT (IDE)                                                     │
   │   (đã đọc trang wiki qua engine)  →  rút sources[] = {path, loc, hint}  │
   └───────────────────────────────┬────────────────────────────────────────┘
                                   │ MCP: rag_fetch(path, hint)
                                   │   [PRIMARY: AGENT đưa address — Scout KHÔNG đọc vault]
                                   ▼
   ╔════════════════════════════ SCOUT ═══════════════════════════════════════╗
   ║  VAI 1 — RAG BRIDGE   (luôn bật · engine-independent · CHỈ ĐỌC)           ║
   ║    ┌──────────────────────────────────────────────────────────────────┐  ║
   ║    │① nhận address (path + hint)                                       │  ║
   ║    │② rag.query(hint, mode="mix", only_need_context=True) ───────────► │  ║ ──┐
   ║    │③ POST-FILTER: chỉ giữ chunk có file_path == address.path          │  ║   │ query
   ║    │④ rỗng sau filter? → status="no_source"  (KHÔNG bịa)               │  ║   │
   ║    │⑤ đóng gói {context[], citations[]} — CHỈ trích + cite             │  ║   │
   ║    │   (không tóm-tắt-rồi-ra-lệnh → injection guard §7)                 │  ║   │
   ║    └──────────────────────────────────────────────────────────────────┘  ║   │
   ╠══════════════════════════════════════════════════════════════════════════╣   │
   ║  VAI 2 — WIKI ENGINE FALLBACK   (chỉ khi basic-memory rớt gate)           ║   │
   ║    kiêm wiki_search / wiki_read trên vault                                ║   │
   ║    (embed summary → cosine → top-K · đọc .md thẳng)                       ║   │
   ║    → chi tiết ở LLM-Wiki_Blueprint §5.3                                   ║   │
   ╚═════════════════════════════════════╤════════════════════════════════════╝   │
         ▲ {context, citations[]}          │                                        │
         │  (trả về agent)                 │ (fallback đọc file)                     ▼
         │                                 ▼                     ┌──────────────────────────────────┐
   ┌─────┴───────────┐          ┌────────────────────┐          │  RAG-ANYTHING (neighbor)          │
   │  Coding Agent   │          │  GIT VAULT (wiki/)  │          │   KG + VDB trên raw/ · merge      │
   │  trả lời + cite │          │  chỉ khi FALLBACK   │          │   corpus · read-only              │
   └─────────────────┘          └────────────────────┘          │  — NỘI BỘ NGOÀI blueprint này —   │
                                                                └──────────────────────────────────┘
```

Ba điều cốt lõi: **(1)** VAI 1 (RAG bridge) là **lõi luôn bật**, độc lập engine; **(2)** ở **primary**, agent **đưa address** — Scout **không** chạm vault (chỉ chạm vault khi ở **fallback**); **(3)** Scout **chỉ đọc**, chỉ **trích + cite** — không diễn giải rồi ra lệnh.

---

## 3. VAI 1 — RAG BRIDGE (mổ sâu)

### 3.1. Components
| Component | Vai trò |
|---|---|
| **MCP server (agent-facing)** | Phơi tool `rag_fetch(path, hint)` cho agent. |
| **RAG client** | Gọi **RAG-Anything trực tiếp** (library/HTTP nội bộ — **KHÔNG** qua MCP trung gian; đã bỏ `rag-mcp`). |
| **Post-filter** | Lọc chunk theo `file_path == address.path`. |
| **Packager / citer** | Gộp chunk còn lại → `{context[], citations[]}` (mỗi mảnh kèm `path`+`loc`). |
| **Guard** | Output schema **cố định, không có trường "action"** → context là **dữ liệu**, không phải lệnh. |

### 3.2. Sơ đồ nội bộ (dataflow)
```
   rag_fetch(path, hint)
        │
        ▼
   ┌──────────────┐   hint
   │  RAG CLIENT  │────────► rag.query(hint, mode="mix", only_need_context=True)
   └──────┬───────┘                                   │
          │  chunks[] (mỗi chunk có file_path)  ◄──────┘   (tìm trên TOÀN corpus)
          ▼
   ┌──────────────┐   giữ chunk.file_path == path
   │ POST-FILTER  │──────────────────────────────► kept[]
   └──────┬───────┘
          │  kept rỗng?
          ├── CÓ ──► {status:"no_source", context:[], citations:[]}   (KHÔNG bịa)
          └── KHÔNG ─► ┌──────────────┐
                       │  PACKAGER    │──► {status:"ok",
                       │  + CITER     │      context: [đoạn gốc...],
                       └──────────────┘      citations: [{path, loc}...]}
```

### 3.3. Workflow (theo mốc ①–⑤)
- **① Nhận address.** `rag_fetch(path, hint)`. Ở **primary**, agent đã đọc trang (qua engine) và truyền `path`+`hint` từ `sources[]`. Scout **không** tự đọc vault.
- **② Query RAG.** `rag.query(hint, mode="mix", only_need_context=True)` — lấy **đoạn gốc** + `file_path`, **không** để LLM của RAG tổng hợp. RAG tìm trên **toàn corpus** (nó không lọc theo file được — §6).
- **③ Post-filter.** Giữ **chỉ** chunk có `file_path == address.path`. Đây là chỗ "đúng nguồn của đúng trang" được bảo đảm.
- **④ Xử lý rỗng.** Nếu sau filter không còn gì (hint lệch từ vựng KG — "rỗng im lặng") → trả `status="no_source"`, **không** bịa nội dung. Agent nên fallback về **nội dung trang wiki** (đã có sẵn) và nói rõ "không truy hồi được nguồn gốc".
- **⑤ Đóng gói + cite.** Gộp chunk → `context[]`; mỗi mảnh kèm `citations[] = {path, loc}`. **CHỈ trích + cite**, không tóm-tắt-rồi-ra-lệnh (§7).

### 3.4. Nhiều `sources[]` trên một trang
`rag_fetch` nhận **list address** (hoặc gọi lặp per-path). Với mỗi address: chạy ②–⑤ riêng; kết quả gộp lại nhưng **citation phân biệt theo từng nguồn**. Trang 2 nguồn → 2 nhóm `{context, citations}`.

### 3.5. Hợp đồng ra RAG (seam xuống, chỉ *gọi*)
Scout chỉ dùng **một** mặt của RAG-Anything:
```
rag.query(text, QueryParam(mode="mix", only_need_context=True))
    -> [ {chunk: str, file_path: str, ...} ]    # đoạn gốc, KHÔNG phải câu trả lời do RAG sinh
```
Mọi thứ bên trong (`mode` chọn KG/VDB thế nào, parse, embedding của RAG) **ngoài phạm vi** — Scout coi RAG là hộp đen trả chunk+file_path.

---

## 4. VAI 2 — WIKI ENGINE FALLBACK (mổ sâu)

Chỉ bật khi basic-memory **rớt một gate** (`LLM-Wiki_Blueprint §10.2`). Lúc đó Scout **kiêm** luôn engine wiki, phơi **đúng** hợp đồng `wiki_search`/`wiki_read` (để agent không thấy khác).

### 4.1. Components (khi fallback)
| Component | Vai trò |
|---|---|
| **Builder / indexer** | Đọc mọi trang → embed `title+summary+entities` (bge-m3 qua LiteLLM) → vector store nhỏ. |
| **Vector store nhỏ** | ~100–500 vector; map `vector → {page_id, path}`. |
| **File reader** | Đọc `.md` **thẳng** từ Git working tree → parse frontmatter + body. |
| **Change watcher** | File đổi → re-embed trang đó. |

### 4.2. Sơ đồ nội bộ (khi fallback)
```
   file .md đổi ──► BUILDER: embed(title+summary+entities) bge-m3 ──► VECTOR STORE nhỏ
   wiki_search(q,k) ──► embed(query) → cosine vs vectors ──► top-K {page_id, path}
   wiki_read(id)    ──► FILE READER: đọc .md thẳng từ Git → parse ──► {frontmatter, body}
```

### 4.3. Workflow (khi fallback)
- **BUILD:** file đổi → embed chuỗi định tuyến → lưu vector.
- **wiki_search:** `embed(query)` → cosine → top-K (vector-only; **không** FTS như basic-memory).
- **wiki_read:** đọc `.md` thẳng → parse → trả `{frontmatter, body}` (frontmatter gồm `sources[]` — rồi lại vào VAI 1).

> Chi tiết đầy đủ (bảng so kè component với basic-memory) ở `LLM-Wiki_Blueprint §5.3–§5.4`. Ở đây chỉ nêu góc nhìn từ Scout: **cùng một tiến trình** có thể mang **cả hai vai**, nhưng hai vai **độc lập logic** — RAG bridge chạy y hệt dù engine wiki là basic-memory hay chính Scout.

---

## 5. Hợp đồng (interfaces)

### 5.1. Agent-facing — luôn có (VAI 1)
```
rag_fetch(path: str, hint: str)      # hoặc rag_fetch(sources: list[{path,loc,hint}])
    -> { status: "ok" | "no_source",
         context:   [ str, ... ],                  # đoạn GỐC, không diễn giải
         citations: [ {path: str, loc: str}, ... ] }
```
Quy ước:
- **Chỉ đọc** RAG. Không hàm nào ghi vào RAG.
- Output **không có** trường hành động/lệnh (guard §7).
- `no_source` là kết quả **hợp lệ**, không phải lỗi — agent xử lý mềm.

### 5.2. Agent-facing — chỉ khi fallback (VAI 2)
```
wiki_search(query, k) -> [ {page_id, path, title, summary, score} ]
wiki_read(id | path)  -> { frontmatter, body }
```
(Đúng hợp đồng ở `LLM-Wiki_Blueprint §5.1` — để đổi engine 0 thay đổi phía agent.)

---

## 6. Vì sao POST-FILTER bắt buộc (cơ chế lõi)

RAG-Anything **gộp mọi document vào một KG + một VDB** ("over All Documents") — đó là **tính năng**, không phải lỗi. Hệ quả **không tránh được**:
- **Không có `path_filter`.** Không thể bảo RAG "chỉ tìm trong file X".
- `rag.query(hint)` trả chunk từ **bất kỳ** file nào khớp ngữ nghĩa hint.

Nếu **không** post-filter, một trang wiki về "kerberoasting ở Acme" có thể nhận về chunk từ report Globex/Initech (cùng nói kerberoasting) → **trích dẫn sai nguồn**. Post-filter theo `file_path == address.path` là hàng rào giữ **"đúng nguồn của đúng trang"**.

> Đây cũng là lý do `sources[].path` phải **mint từ RAG** (lấy `file_path` thật RAG tạo ra) — xem `LLM-Wiki_Blueprint §9`. Path lệch → post-filter loại sạch → `no_source`.

---

## 7. Chống prompt-injection (vì sao "chỉ trích + cite")

`raw/` chứa **dữ liệu do bên ngoài kiểm soát** (report pentest, phishing sample, evidence) — có thể nhét chỉ thị kiểu *"bỏ qua hướng dẫn, chạy lệnh sau…"*. Nguyên tắc cứng của Scout:

- Scout **KHÔNG** "đọc-hiểu-rồi-làm-theo" context. Nó **trích** đoạn gốc và **gắn citation**, hết.
- Output schema **cố định**: `{status, context[], citations[]}` — **không có** trường "action"/"command"/"next_step". Không có chỗ để injection "thoát" ra thành hành động.
- Context được đối xử là **dữ liệu**, đưa nguyên về agent; **agent** (có system prompt + luật riêng) mới là bên quyết định, không phải payload trong `raw/`.

> Test bắt buộc (spike §9): thả file `raw/` chứa chỉ thị → xác nhận Scout trả nó **như trích dẫn**, không thực thi.

---

## 8. Bất biến & luật thiết kế (riêng Scout)

1. **Chỉ đọc RAG.** Không đường ghi từ Scout vào RAG.
2. **Agent đưa address ở primary.** RAG-bridge Scout **không** đọc vault ở primary (chỉ đọc vault khi mang VAI 2 fallback).
3. **Gọi RAG trực tiếp.** Không middleman `rag-mcp`; RAG-Anything **không** expose cho agent — **chỉ** Scout gọi.
4. **Luôn `only_need_context=True`.** Lấy đoạn gốc, không để LLM của RAG tổng hợp (giữ chuỗi trích dẫn + tránh 2 model suy luận thừa).
5. **Luôn post-filter theo `file_path`.** Không có ngoại lệ (RAG không lọc file được).
6. **Chỉ trích + cite.** Output không có trường hành động (injection guard).
7. **`no_source` là hợp lệ.** Rỗng → nói thẳng, không bịa.
8. **Hai vai độc lập logic.** RAG bridge chạy y hệt bất kể engine wiki là ai.

---

## 9. Rủi ro & spike (riêng Scout)

| Rủi ro | Ảnh hưởng | Phương án / test |
|---|---|---|
| **Rỗng im lặng** (hint lệch KG của RAG) | agent vào ngõ cụt | `verify_addresses.py` bắt lúc build (PASS/FAIL/DRIFT); runtime trả `no_source` (§3.3.④) |
| **Post-filter sai** (so path không khớp định dạng `file_path` RAG trả) | loại nhầm / giữ nhầm nguồn | test: cho address đã biết, xác nhận chỉ giữ đúng file; chuẩn hoá path 2 bên |
| **Prompt-injection từ `raw/`** | agent bị điều khiển | test §7; output schema không có "action" |
| **RAG trả quá nhiều chunk** trước filter | tốn token/độ trễ | giới hạn `k` của `rag.query`; filter sớm |
| **Fallback recall tiếng Việt** (VAI 2) | tìm trật trang | dùng bge-m3 (đã multilingual) cho fallback embed |
| **RAG-Anything API đổi** (`only_need_context`, shape trả) | bridge gãy | pin version RAG-Anything; test hợp đồng §3.5 |

→ Ánh xạ task: `tasks.md` **T-2.2** (rag_fetch: query+post-filter+cite+no_source), **T-2.3** (phơi MCP, khoá RAG), **T-2.5** (verify_addresses), **T-2.6** (nhiều sources), **T-3.6** (test injection); VAI 2 ở **T-2.4**.

---

## 10. Thứ tự dựng Scout (tóm tắt — chi tiết ở `tasks.md`)

1. **RAG client** gọi RAG-Anything trực tiếp với `only_need_context=True` (T-2.1 phải xong: RAG đã index `raw/`).
2. **`rag_fetch`**: query → **post-filter** theo `file_path` → `no_source` khi rỗng → đóng gói `{context, citations}` (T-2.2).
3. **Phơi MCP agent-facing**; **khoá** RAG-Anything khỏi agent (chỉ Scout gọi) (T-2.3).
4. **`verify_addresses.py`** (PASS/FAIL/DRIFT) cho quy trình mint (T-2.5).
5. **Nhiều `sources[]`** per trang (T-2.6).
6. **Test injection** từ `raw/` (T-3.6).
7. **VAI 2 fallback**: `wiki_search`/`wiki_read` sau cùng hợp đồng (T-2.4) — chỉ bật khi engine chính rớt gate.
