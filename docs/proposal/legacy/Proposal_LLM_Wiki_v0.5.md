# Đề xuất (v0.5): Team Security LLM-Wiki — Wiki + AI Gateway + RAG + Git

| | |
|---|---|
| **Version** | 0.5 (Draft) — thay thế v0.1 → v0.4 |
| **Tác giả** | saltless-bruh |
| **Ngày** | 15/07/2026 |
| **Trạng thái** | Draft — đã tích hợp phản hồi của team lead |
| **Thay đổi so với v0.4** | (1) **Thêm lớp Wiki (Wiki.js)** — trả lời trực tiếp hai ý *[lead's feedback]* và *[lead's feedback]*. (2) **RBAC dời sang Phase 2** theo chỉ đạo *[lead's feedback]*. (3) **Thêm kết quả đo corpus** → chốt chiến lược **tier RAG** (graph cho KB, vector cho archive). (4) Bỏ authz gateway khỏi v1. |

---

## 1. Tóm tắt (Executive Summary)

**Cái gì:** Một **LLM-Wiki dùng chung cho team Cybersecurity** — lưu multi-data (raw code, PDF, doc/docx, md, image, excel/csv), cho cả người và AI/Agent đóng góp, kết nối vào IDE/Agent của từng thành viên để query. Self-hosted hoàn toàn trên **Docker**, local-first.

**Kiến trúc chốt theo đúng chỉ đạo của team lead:**

> *[lead's feedback]*

```
   Wiki (Wiki.js)  →  AI GW (LiteLLM)  →  RAG (RAG-Anything)  →  Git (Gitea)
   người dùng          model gateway        index + query          lưu trữ tập trung
```

**Năm quyết định cốt lõi:**

| # | Quyết định | Vì sao (một dòng) |
|---|---|---|
| 1 | **Wiki UI = Wiki.js**, git ở dưới | Người dùng **không đụng git** → hết *[lead's feedback]*; đọc state trực tiếp từ server → hết *[lead's feedback]* |
| 2 | **Lưu trữ tập trung = Gitea** | Giữ đúng ưu điểm team lead khen: commit biết **ai sửa, sửa gì, lúc nào, agent hay người** |
| 3 | **RAG = RAG-Anything**, **tier theo repo** | Native multimodal; và **đo corpus cho thấy graph toàn bộ 5-10GB là bất khả thi trên 1 GPU** (§10.3) |
| 4 | **AI GW = LiteLLM (local-only)** | Một endpoint cho RAG + **chokepoint duy nhất** để chứng minh dữ liệu không rời hạ tầng |
| 5 | **Lock = per-file AI mutex + human preempt** | Thoả từng mệnh đề L1/L2/L3 của team lead |

**Phân quyền (RBAC): dời sang Phase 2** theo chỉ đạo. Thiết kế vẫn giữ nguyên "móc treo" để lắp vào sau mà không phải làm lại (§11).

---

## 2. Chỉ đạo của team lead & cách bản v0.5 đáp ứng

> *[The team lead's feedback, quoted word for word in this draft, was removed before publication on 2026-10-01.]*

---

## 3. Kiến trúc tổng quan

```
   NGƯỜI DÙNG                          AI / AGENT
   (team members)                      (service account)
        │                                    │
        │ soạn thảo trên trình duyệt         │ chỉ mở Pull Request
        │ (Markdown hoặc WYSIWYG)            │
        ▼                                    │
 ┌──────────────────────┐                    │
 │  WIKI.JS  (§4)       │                    │
 │  - editor + search   │                    │
 │  - PostgreSQL (chính)│                    │
 └──────────┬───────────┘                    │
            │ git sync module (2 chiều)      │
            ▼                                ▼
 ┌────────────────────────────────────────────────────┐
 │  GITEA  (§5)  — LƯU TRỮ TẬP TRUNG                  │
 │  main branch:  md / code / pdf / img / xlsx (LFS)  │
 │  lịch sử commit = ai, cái gì, lúc nào, agent/người │
 └──────────┬─────────────────────────────────────────┘
            │ webhook khi merge
            ▼
 ┌──────────────────────┐        ┌─────────────────────┐
 │  SYNC JOB  (§7.4)    │───────▶│  RAG-ANYTHING (§7)  │
 │  clone + git lfs pull│        │  index / repo (tier)│
 └──────────────────────┘        └──────────┬──────────┘
                                            │
                     ┌──────────────────────┼──────────────┐
                     ▼                      ▼              ▼
            ┌────────────────┐    ┌──────────────┐  ┌────────────┐
            │ LITELLM (§8)   │    │  MCP  (§9)   │  │ LOCK (§6)  │
            │ AI GW, local   │    │ → IDE/Agent  │  │ mutex      │
            └────────────────┘    └──────────────┘  └────────────┘
```

**Sáu lớp — mỗi lớp một lý do:**

| Lớp | Là gì | Vì sao có mặt |
|---|---|---|
| **Wiki** (§4) | Wiki.js | Team lead: *[lead's feedback]* + trả lời *[lead's feedback]* / *[lead's feedback]* |
| **Storage** (§5) | Gitea + LFS | Team lead: *[lead's feedback]* + giữ audit trail |
| **Lock** (§6) | Lock service | L1/L2/L3 — mảnh duy nhất git không cho sẵn |
| **RAG** (§7) | RAG-Anything, tier theo repo | Multimodal; và **chi phí index bắt buộc phải tier** (§7.3) |
| **AI GW** (§8) | LiteLLM | Team lead: *[lead's feedback]* + chokepoint local-only |
| **MCP** (§9) | MCP server | Kết nối IDE/Agent của từng thành viên |

---

## 4. Lớp Wiki — Wiki.js (MỚI ở v0.5)

### 4.1. Là gì

**Wiki.js** self-hosted trên Docker, dùng **git storage module** đồng bộ hai chiều với Gitea.

**Vì sao chọn Wiki.js:**

- **Có cả Markdown editor và WYSIWYG (Visual Editor)** — người không rành markdown vẫn viết được. Đây là thứ trực tiếp trả lời *[lead's feedback]*.
- **Git-backed content, sync hai chiều với git repo** — đúng mô hình "wiki ở trên, git lưu trữ tập trung ở dưới".
- **Hỗ trợ Mermaid/PlantUML** — vẽ sơ đồ ngay trong trang, hợp với tài liệu security.
- Docker, self-hosted, AGPL-3.0, full-text search.

### 4.2. Vì sao lớp Wiki giải quyết CẢ HAI ý của team lead

Hai ý *[lead's feedback]* và *[lead's feedback]* thực ra là **một vấn đề**: **con người phải trực tiếp đụng vào git**.

> *[The team lead's feedback, quoted word for word in this draft, was removed before publication on 2026-10-01.]*

**Giữ nguyên ưu điểm team lead khen:** Wiki.js commit xuống Gitea → lịch sử vẫn đầy đủ *ai sửa, sửa gì, lúc nào*. Agent vẫn commit qua PR → phân biệt được *agent hay người*.

### 4.3. ⚠️ Ba điểm phải verify TRƯỚC khi chốt Wiki.js

Nói thẳng, không giấu:

1. **Attribution của commit (QUAN TRỌNG NHẤT).** Team lead khen chính xác tính năng này. **Phải verify Wiki.js commit xuống git với danh tính của TỪNG người sửa**, chứ không phải gộp hết vào một service account `wikijs`. Nếu nó gộp → **mất đúng thứ team lead quý nhất** → phải patch hoặc đổi phương án (xem §13).
2. **Wiki.js lưu chính ở PostgreSQL, git là storage module đồng bộ.** Nghĩa là **git là bản mirror + audit log**, không phải store duy nhất. Hệ quả: có **sync interval** giữa DB và git → cửa sổ conflict nếu ai đó push thẳng vào repo cùng lúc. **Lock service (§6) đóng phần lớn cửa sổ này**, nhưng không phải 100%.
3. **Wiki.js đang chuyển v2 → v3.** v2 ổn định, v3 đang viết lại. Nên **chốt v2** cho v1 và theo dõi v3.

> **Nếu điểm 1 fail:** phương án dự phòng là **Gitea Wiki** (wiki tích hợp sẵn của Gitea, bản thân nó *là* một git repo → không có dual-store, không có sync interval, attribution chuẩn). Đổi lại: UI cơ bản hơn nhiều, không có WYSIWYG. Tức là quay lại vấn đề *[lead's feedback]*. Đây là đánh đổi thật, cần team lead quyết nếu chạm tới.

---

## 5. Lưu trữ tập trung — Gitea

**Là gì:** Gitea self-hosted là **nơi lưu trữ tập trung** duy nhất. Wiki.js sync xuống; agent PR vào; RAG index từ đây.

**Vì sao git (đúng như team lead chốt):**

1. **Lịch sử commit = audit trail miễn phí** — ai sửa, sửa/xóa cái gì, lúc nào, agent hay người. Đây là ưu điểm team lead nêu đích danh.
2. **PR + branch protection = cơ chế "AI chỉ được suggest"** (§6.1) — không phải build từ đầu.
3. **File substrate** → IDE/Agent đọc trực tiếp được, và bất kỳ tool file-based nào cũng edit được.
4. **Version + rollback** — sai thì revert, không mất gì.
5. **Móc treo cho RBAC Phase 2** — permission per-repo có sẵn, lắp vào sau không phải làm lại (§11).

**Cấu trúc repo:**

```
security-kb/          → tri thức chung        → index: GRAPH  (§7.3)
engagement-<tên>/     → theo từng engagement  → index: VECTOR (§7.3)
```

> Chia repo theo engagement **ngay từ v1** — không phải để phân quyền (đã dời Phase 2), mà vì **(a)** chi phí index bắt buộc tier theo repo (§7.3), và **(b)** khi RBAC lắp vào ở Phase 2, ranh giới đã sẵn sàng, **không phải tách repo lúc đó** (rất đau).

---

## 6. Lock & Collaboration

### 6.1. Ánh xạ yêu cầu → cơ chế

| Yêu cầu team lead | Cơ chế | Ghi chú |
|---|---|---|
| **L1** Human edit → AI chỉ *suggest* cùng file | **Branch protection trên `main`**: require PR + approval, **agent KHÔNG trong merge whitelist** | Enforce bởi **Gitea**, không dựa vào AI tự giác. Đây là workflow control, **không phải module phân quyền** — nên vẫn nằm trong v1. |
| **L2** AI edit → AI khác không edit cùng file | **Per-file AI mutex** (lock service) | Song song trên **file khác** vẫn OK |
| **L3** Human edit khi AI đang edit = stop/pause | Human claim → **HALT** → agent dừng, bỏ branch dở | Con người **luôn thắng** |

### 6.2. Lock service

```
lock:<repo>:<path> → { holder_id, role: "human"|"ai", acquired_at, ttl }
```

- **Agent acquire:** set `ai_locked` nếu file chưa bị `ai_locked`. Nếu đang `human_locked` → agent hạ xuống **suggest-only**.
- **Human preempt:** claim `human_locked` → phát **HALT** cho agent đang giữ lock.
- **TTL + heartbeat:** agent chết → lock tự hết hạn.
- **Bonus:** lock service cũng **đóng phần lớn cửa sổ conflict** của Wiki.js dual-store (§4.3 điểm 2).

### 6.3. Presence signal — mảnh duy nhất phải tự viết

File không tự báo "đang có người sửa".

- **v1:** Wiki.js **đã có** page-level edit state → dùng làm nguồn `human_locked` cho các trang wiki. Với file ngoài wiki (code, PDF): explicit claim command.
- **v2:** VS Code extension tự claim/release khi open/close file.

### 6.4. Binary: lock là lớp bảo vệ DUY NHẤT

Git merge được text, **không merge được** `.docx`/`.xlsx`/`.png`. Hai người sửa cùng file Office → phải **chọn nguyên một bản** → **công của một bên mất**.

- **md/code:** lock hụt → git merge vẫn là lưới an toàn.
- **binary:** **không có lưới**. Lock là lớp duy nhất.

→ **Lock không phải thủ tục hình thức** — nó là thứ duy nhất bảo vệ file Office.

---

## 7. RAG layer — RAG-Anything + chiến lược TIER

### 7.1. Vì sao RAG-Anything

- **Native multimodal** — text, image, table, equation trong cùng pipeline. Đúng mix dữ liệu của team.
- **Knowledge graph (LightRAG)** — với KB code + docs, **quan hệ chính là giá trị** (hàm nào gọi gì, finding nào tham chiếu tài liệu nào).
- **Incremental update** — chỉ reprocess phần đã đổi khi merge.
- **Có MCP server** → kết nối IDE/Agent.

### 7.2. Kết quả ĐO corpus (mới — dùng `corpus_survey.py`)

Vì chưa có quyền truy cập data, đã viết script **đo thay vì đoán**: `corpus_survey.py` quét repo, lấy mẫu từng loại file, đo **text thật sự extract được / byte**, rồi ước lượng số chunk và giờ GPU.

Chạy thử trên corpus mô phỏng (mix kiểu team security), kết quả **quan trọng**:

| Kịch bản (5-10GB) | GraphRAG | Vector-only |
|---|---|---|
| Corpus **text-heavy** (nhiều code/md) | **30-60 ngày** GPU | 7-15 giờ |
| Corpus **image/PDF-heavy** (nhiều evidence) | **4-8 ngày** GPU | 2-5 giờ |

**Kết luận thẳng: GraphRAG toàn bộ 5-10GB trên một RTX 3060 là BẤT KHẢ THI.** Từ vài ngày đến hai tháng, tuỳ mix. Và **không thể thuê GPU cloud** cho lần index đầu — vì dữ liệu security **không được rời hạ tầng** (§8).

Script cũng tự phát hiện:
- **% PDF là bản scan** (cần OCR — đắt). Lưu ý: test cho thấy PDF scan vẫn có thể *khai báo font* mà không có text → chỉ kiểm `pdffonts` là **không đủ**, phải kiểm thêm số ký tự/trang.
- **Ảnh chiếm bao nhiêu % tổng chi phí index** — trên corpus test, **126 ảnh = 51% tổng chi phí**, trong khi giá trị query thấp nhất.

### 7.3. → Chiến lược TIER (bắt buộc, không phải tối ưu hoá)

Mỗi repo có index riêng → **mỗi index có chiến lược riêng**:

| Repo | Chiến lược | Vì sao |
|---|---|---|
| `security-kb` (nhỏ, tri thức tinh, quan hệ quan trọng) | **GraphRAG đầy đủ** | Đây là chỗ graph thật sự đáng tiền |
| `engagement-*` (report, evidence, archive) | **Vector-only** | Nhu cầu thật là "tìm đoạn văn", không phải traversal. Nhanh gấp ~100 lần |
| `*/evidence/` (screenshot) | **`.ragignore`** (v1 không index) | Chiếm ~51% chi phí, giá trị query thấp nhất. Chỉ VLM ảnh trong `docs/` (diagram, architecture) |
| PDF scan | **Hoãn sang wave 3** | OCR đắt; chỉ OCR report quan trọng |

**Tier không phải optimization — nó là ranh giới giữa "chạy được" và "không chạy được".**

### 7.4. Ingest theo wave (thứ tự quyết định được NGAY, không cần biết mix)

Thứ tự dựa trên **chi phí/giá trị theo loại file** — biết trước, không phụ thuộc tỉ lệ:

| Wave | Nội dung | Chi phí | Khi nào |
|---|---|---|---|
| **1** | markdown + code (`security-kb`) | Rẻ, không OCR/VLM | Tuần 1 — hệ thống dùng được ngay |
| **2** | PDF có text layer + csv/xlsx | Trung bình | Tuần 2-3 |
| **3** | PDF scan (OCR) + ảnh trong `docs/` | Đắt | Khi có budget GPU |
| **—** | evidence screenshots | Rất đắt, giá trị thấp | **Không index v1** |

**Sync job:** Gitea webhook on merge → update clone → **`git lfs pull` (BẮT BUỘC)** → re-index đúng repo đó.
> Thiếu `git lfs pull` → index nhận **LFS pointer text** thay vì nội dung PDF thật. **Lỗi im lặng** — index vẫn "chạy", chỉ vô dụng.

---

## 8. AI Gateway — LiteLLM

**Là gì:** LiteLLM là gateway OpenAI-compatible. RAG-Anything trỏ vào nó cho **embedding + LLM + vision**. Phía sau là local inference (llama.cpp) + một **vision model**.

**Vì sao:**

1. **Chokepoint DUY NHẤT để chứng minh "không cloud"** — mọi lời gọi model đi qua đây → audit **một** file config là đủ. **Lý do mạnh nhất** với dữ liệu security.
2. **Một endpoint ổn định** cho RAG — đổi/route model không đụng config RAG.
3. **Virtual keys per consumer** → rate limit, thu hồi độc lập.
4. **Vision routing** — multimodal parsing cần VLM, route riêng slot đó.

> **LiteLLM không tự inference** — nó là proxy/gateway.

---

## 9. MCP query layer

**Là gì:** expose RAG qua **MCP** để IDE/Agent của từng thành viên cắm vào (Cursor, VS Code Copilot agent mode, Claude Code, Cline…).

**Lựa chọn (chốt ở review):** RAG-Anything MCP server (ưu tiên — giữ multimodal) · LightRAG MCP servers · code-RAG MCP servers · wrapper tự viết (fallback).

> **v1 không có authz gateway** (đã dời Phase 2 cùng RBAC). Nghĩa là **ai cắm MCP cũng query được mọi index**. Xem §11 — đây là rủi ro phải biết.

---

## 10. Data & Format policy

| Loại | Lưu | RAG | Ghi chú |
|---|---|---|---|
| **md** | text, merge được | ✅ tốt nhất | Định dạng hạng nhất; Wiki.js sinh ra md |
| **code** | text, merge được | ✅ (chunk theo function/class) | |
| **csv** | text | ⚠️ bảng | |
| **pdf** | binary → **LFS** | ✅ (scan → OCR, đắt) | Read-mostly |
| **docx/xlsx** | binary → **LFS** | ✅ | **Không merge được** (§6.4) |
| **image** | binary → **LFS** | ⚠️ VLM, đắt | `evidence/` → `.ragignore` |

**Born-digital → markdown** (tri thức team tự viết): diff/merge được, PR review có ý nghĩa, RAG parse chuẩn nhất, Wiki.js sinh ra nó tự nhiên.
**Received artifacts → giữ nguyên binary gốc** (report khách hàng, PDF vendor): **evidence/chain-of-custody** — bản gốc là bằng chứng, không "chuyển thể" rồi vứt gốc. Và read-mostly → vấn đề no-merge hiếm khi nổ.

**Không bao giờ đưa vào repo:** secrets/credentials (git giữ lịch sử **vĩnh viễn**) · **malware samples** (AV/EDR sẽ quarantine clone của mọi người) · raw capture chứa live credentials.
**`.gitignore`** = không vào repo. **`.ragignore`** = trong repo nhưng không vào index. Hai lớp khác nhau.

---

## 11. Phase 2 — Modules phân quyền (RBAC)

> Theo chỉ đạo: *[lead's feedback]* + *[lead's feedback]*. Mục này **không build ở v1**, ghi lại để Phase 2 lắp vào không phải làm lại.

### 11.1. ⚠️ Rủi ro của việc v1 không có phân quyền — cần team lead biết

**v1 = index phẳng, không ranh giới.** Ai cắm MCP vào cũng **query được mọi thứ**, kể cả dữ liệu của engagement khác. Với team security có nhiều khách hàng, đây là **leak xuyên need-to-know**.

**Giảm thiểu tạm cho v1 (rẻ, không phải build module):**
- **Gitea permission per-repo** vẫn bật (có sẵn, không phải code) → ít nhất chặn được ở tầng *đọc file*.
- **Chỉ index `security-kb` ở v1**, hoãn `engagement-*` sang Phase 2 cùng RBAC → không có dữ liệu khách hàng trong index thì không có gì để leak.
- Đây cũng **trùng khớp với chiến lược tier** (§7.3) — wave 1 vốn chỉ là `security-kb`.

→ **Khuyến nghị: v1 chỉ index `security-kb`.** Vừa đúng budget GPU, vừa né rủi ro phân quyền, vừa cho hệ thống chạy được ngay.

### 11.2. Thiết kế RBAC cho Phase 2 (đã có sẵn, chờ chốt)

**Chuỗi privilege — một chuỗi duy nhất, không có ACL system thứ hai:**

```
Boss / Team Lead (Gitea Org Owner)
   → cấp/thu hồi membership
Gitea Team = ROLE
   → team có permission trên repo
Repo = ranh giới need-to-know
   → mỗi repo có index dẫn xuất
Index = phải KẾ THỪA ACL của repo
   → authz gateway check LIVE
MCP scope = người đó query được gì
```

**Ba điểm kỹ thuật đã chốt sẵn cho Phase 2:**

1. **Gitea đã là identity provider** — dùng Gitea PAT/OAuth2, **không cần dựng SSO riêng**.
2. **Authz-aware MCP gateway** — check live với Gitea (cache TTL ~60s) thay vì static token. Lý do: **static token gây revocation drift** — thu hồi quyền mà token cũ vẫn query được, **thất bại âm thầm**.
3. **Physical index separation, KHÔNG phải filtered retrieval** — với **graph** RAG, entity bị **merge xuyên nguồn ngay lúc index**, community summary **trộn** nội dung nhiều tài liệu → filter lúc query là **quá muộn**. Ranh giới phải dựng ở tầng **vật lý**. (Đánh đổi: mất cross-repo graph reasoning — nhưng đó đúng là câu hỏi không nên trả lời được.)

> **Ghi chú thuật ngữ:** yêu cầu ban đầu gọi là *"decentralization"*. Phản hồi *[lead's feedback]* đã xác nhận: ý là **lưu trữ tập trung + phân quyền theo role**, tức **RBAC/least-privilege**, không phải decentralization kỹ thuật (P2P/federated). Câu hỏi này **đã đóng**.

---

## 12. Triển khai Docker

Toàn bộ trên Docker Compose, shared network. Thành phần v1: **Wiki.js (+ Postgres)**, **Gitea (+ Postgres)**, **LiteLLM**, **RAG-Anything**, **MCP server**, **Lock service (+ Redis)**, **sync-job**. Xem **Phụ lục A**.

**Resource note (trung thực):** Wiki.js (2) + Gitea (2) + RAG + MCP + LiteLLM + lock + redis + sync ≈ **10 container**. RAM 32GB đủ. **GPU là nút thắt thật**: parsing + graph + LLM trên một RTX 3060 12GB → xem §7.2. **Giữ inference tách khỏi wiki compute path**; production nên có GPU box riêng.

---

## 13. Build scope — cái gì phải TỰ VIẾT

| # | Thành phần | Quy mô | Phase |
|---|---|---|---|
| 1 | **Lock service** (per-file mutex + preempt + TTL/heartbeat) | Nhỏ (~200-300 dòng) | **v1** |
| 2 | **Sync/re-index job** (webhook → clone + `git lfs pull` → index) | Rất nhỏ (script) | **v1** |
| 3 | **Presence/claim** (v1: dùng edit state của Wiki.js + command; v2: VS Code extension) | Nhỏ → vừa | v1 → v2 |
| 4 | **`corpus_survey.py`** | ✅ **Đã xong** | — |
| 5 | **Patch attribution cho Wiki.js git module** (nếu §4.3 điểm 1 fail) | ? — **verify trước** | v1 (có điều kiện) |
| 6 | **Authz-aware MCP gateway** | Nhỏ–vừa | **Phase 2** |
| 7 | MCP wrapper (nếu server có sẵn thiếu coverage) | Nhỏ, có thể không cần | v1 (fallback) |

**Mọi thứ còn lại — Wiki.js, Gitea, RAG-Anything, LiteLLM, MCP server — là tool có sẵn, chỉ cấu hình.** Bề mặt code tự viết nhỏ → ít bug, dễ handover.

---

## 14. Lộ trình

**v1 (MVP) — mục tiêu: wiki chạy được, query được, lock đúng spec.**
1. Gitea + Git LFS + `.gitattributes` (**trước** file binary đầu tiên) + branch protection.
2. **Wiki.js + git sync module** → verify **attribution** (§4.3 #1) **trước tiên**.
3. LiteLLM (local-only) + RAG-Anything, **chỉ index `security-kb`, GraphRAG** (§11.1).
4. MCP server → cắm vào IDE của team.
5. Lock service + presence từ Wiki.js edit state.
6. Sync job (webhook + `git lfs pull`).
7. Chạy `corpus_survey.py` **ngay khi có quyền truy cập data** → chốt lại timeline index.

**v2+**
- Wave 2/3 ingest (PDF, xlsx, ảnh `docs/`) theo budget GPU.
- VS Code extension cho presence.
- **Phase 2: modules phân quyền** (§11.2) + authz gateway + index `engagement-*`.
- Code-aware chunking; scale storage backend (Postgres/Milvus/Neo4j).

---

## 15. Rejected alternatives — và vì sao

| Phương án | Vì sao loại |
|---|---|
| **Affine** | Store là CRDT/DB, **không phải file** → IDE/Agent không đọc trực tiếp; không có PR model → L1 không có chỗ enforce; **self-hosted thiếu native MCP + ít integration** (team lead đã gặp ở localhost); sync markdown↔CRDT là project riêng, lossy. |
| **Obsidian làm store/UI chung** | App **single-user**; `linuxserver/obsidian` là **KasmVNC remote desktop** — một session; **không có merge layer** → human + agent cùng file có thể **mất dữ liệu thẳng**. (Vẫn OK làm editor cá nhân trên clone.) |
| **Dify** | RAG native **vector-only** → phải tự build pipeline per-type; orchestration phục vụ **chat app**, không phải query-KB spine. |
| **Nextcloud + OnlyOffice** | Thứ duy nhất edit Office trong browser — nhưng **storage riêng**, yếu với code, **không git-back sạch** → phá substrate. |
| **Note apps khác** (SiYuan, Outline, Docmost, BookStack, Logseq…) | Markdown/block native, định dạng khác chỉ embed/preview; **hầu hết mang storage riêng** → mở lại bài toán sync. Wiki.js thắng **chính vì có git-backed storage**. |
| **Người dùng thao tác git trực tiếp** | Team lead: *[lead's feedback]* + *[lead's feedback]*. → thay bằng lớp Wiki (§4). |
| **GraphRAG toàn bộ corpus** | **Đo được: 4-60 ngày GPU** trên 1 RTX 3060, và không được thuê cloud (dữ liệu security). → tier (§7.3). |
| **Index ảnh evidence** | Chiếm **~51% tổng chi phí index**, giá trị query thấp nhất. → `.ragignore`. |
| **Single flat index + metadata filtering** (cho Phase 2) | Với graph, entity **đã merge xuyên nguồn lúc index** → filter lúc query **quá muộn**. Phải physical separation. |
| **Decentralization kỹ thuật (P2P)** | Team lead đã chốt *[lead's feedback]* → câu hỏi đóng. |

---

## 16. Rủi ro & Giả định

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| **Wiki.js commit gộp vào 1 service account** → mất attribution (thứ team lead quý nhất) | **Cao** | **Verify NGAY** trước khi build (§4.3 #1). Fallback: Gitea Wiki (mất WYSIWYG). |
| **v1 không có phân quyền** → index phẳng, ai cũng query được mọi thứ | **Cao** | **v1 chỉ index `security-kb`**, hoãn `engagement-*` sang Phase 2 (§11.1) |
| **Chi phí index vượt xa dự tính** (4-60 ngày) | **Cao** | Tier (§7.3) + wave (§7.4) + chạy `corpus_survey.py` ngay khi có data |
| **Wiki.js dual-store** (DB chính + git sync) → sync interval, cửa sổ conflict | Trung bình | Lock service đóng phần lớn cửa sổ (§6.2); chốt Wiki.js v2 |
| **Presence phụ thuộc kỷ luật** với file ngoài wiki | Trung bình | Wiki.js có sẵn edit state cho trang wiki; v2 làm extension |
| **`git lfs pull` bị quên** → index toàn LFS pointer | Trung bình (**lỗi im lặng**) | Health check: assert index có nội dung thật |
| **Git LFS threshold đặt muộn** → phải rewrite history | Trung bình | `.gitattributes` **trước** binary đầu tiên |
| **Wiki.js v2→v3 transition** | Thấp–TB | Chốt v2 cho v1, theo dõi v3 |

---

## Phụ lục A — `docker-compose` (rút gọn)

```yaml
networks:
  wiki-net: { external: true }   # docker network create wiki-net

services:
  gitea:                          # §5 — LƯU TRỮ TẬP TRUNG
    image: gitea/gitea:latest
    environment:
      GITEA__database__DB_TYPE: postgres
      GITEA__database__HOST: gitea-db:5432
      GITEA__server__LFS_START_SERVER: "true"     # §10 — LFS bật từ đầu
    volumes: ["gitea-data:/data"]
    depends_on: [gitea-db]
    networks: [wiki-net]

  gitea-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: gitea
      POSTGRES_USER: gitea
      POSTGRES_PASSWORD: ${GITEA_DB_PASSWORD}
    volumes: ["gitea-db-data:/var/lib/postgresql/data"]
    networks: [wiki-net]

  wikijs:                         # §4 — LỚP WIKI (người dùng chỉ thấy cái này)
    image: ghcr.io/requarks/wiki:2                # §4.3 #3 — chốt v2
    environment:
      DB_TYPE: postgres
      DB_HOST: wiki-db
      DB_PORT: "5432"
      DB_NAME: wiki
      DB_USER: wiki
      DB_PASS: ${WIKI_DB_PASSWORD}
    ports: ["3000:3000"]
    depends_on: [wiki-db, gitea]
    networks: [wiki-net]
    # Cấu hình sau khi lên: Administration > Storage > Git
    #   repo = http://gitea:3000/<org>/security-kb.git
    #   ⚠️ §4.3 #1 — verify commit có giữ danh tính từng người sửa không

  wiki-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: wiki
      POSTGRES_USER: wiki
      POSTGRES_PASSWORD: ${WIKI_DB_PASSWORD}
    volumes: ["wiki-db-data:/var/lib/postgresql/data"]
    networks: [wiki-net]

  litellm:                        # §8 — AI GATEWAY, local-only
    image: ghcr.io/berriai/litellm:main-latest
    command: ["--config", "/app/config.yaml"]
    volumes: ["./litellm-config.yaml:/app/config.yaml:ro"]
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
    networks: [wiki-net]
    # backends (llama.cpp + VLM) trong config.yaml — KHÔNG có cloud provider

  rag:                            # §7 — RAG-Anything / LightRAG
    build: { context: ./rag }
    environment:
      LLM_BINDING: openai
      LLM_BINDING_HOST: http://litellm:4000
      EMBEDDING_BINDING_HOST: http://litellm:4000
      INDEX_MODE: per-repo                        # §7.3 — tier theo repo
      REPO_MOUNT: /data/repos
    volumes:
      - "rag-storage:/app/storage"
      - "repo-clones:/data/repos:ro"
    depends_on: [litellm]
    networks: [wiki-net]

  rag-mcp:                        # §9 — MCP cho IDE/Agent
    build: { context: ./rag-mcp }
    environment:
      LIGHTRAG_SERVER_URL: http://rag:9621
      MCP_TRANSPORT: http
      MCP_HTTP_TOKEN: ${MCP_HTTP_SECRET}
    ports: ["9000:9000"]
    depends_on: [rag]
    networks: [wiki-net]
    # Phase 2: đặt authz-aware gateway phía trước (§11.2)

  sync-job:                       # §7.4 — webhook → clone + lfs pull → re-index
    build: { context: ./sync-job }
    environment:
      GITEA_URL: http://gitea:3000
      GITEA_TOKEN: ${GITEA_SYNC_TOKEN}
      RAG_URL: http://rag:9621
      LFS_PULL: "true"                            # §7.4 — BẮT BUỘC
    volumes: ["repo-clones:/data/repos"]
    depends_on: [gitea, rag]
    networks: [wiki-net]

  lock-svc:                       # §6 — per-file AI mutex + human preempt
    build: { context: ./lock-svc }
    environment:
      REDIS_URL: redis://redis-lock:6379
      WIKIJS_URL: http://wikijs:3000              # §6.3 — nguồn human edit state
    depends_on: [redis-lock]
    networks: [wiki-net]

  redis-lock:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes: ["redis-lock-data:/data"]
    networks: [wiki-net]

volumes:
  gitea-data:
  gitea-db-data:
  wiki-db-data:
  rag-storage:
  repo-clones:
  redis-lock-data:
```

## Phụ lục B — Lock state machine

```
State per file:  none | ai_locked{agent} | human_locked{user}

Agent muốn edit file f:
  none             -> set ai_locked{self}; branch; mở PR; release
  ai_locked{other} -> BLOCK (đợi / chuyển file khác — L2 cho phép song song khác file)
  human_locked{*}  -> KHÔNG direct-edit; hạ xuống suggest-only              [L1]

Human claim f (Wiki.js edit state / explicit command):
  set human_locked{self}
  if ai_locked{agent} -> HALT agent; agent dừng + bỏ branch dở              [L3]

Release: agent sau khi mở PR hoặc bị HALT · human khi rời trang/file
An toàn: AI-lock có TTL + heartbeat -> agent chết thì lock tự hết hạn
Ưu tiên: human_locked LUÔN thắng ai_locked                                  [Human > AI]

Lớp 1 (phối hợp): lock service
Lớp 2 (enforce)  : branch protection — agent không merge được kể cả khi lock hỏng
```

## Phụ lục C — Luồng agent authoring

```
agent → acquire AI-lock(files)        [L2]
      → branch → soạn nội dung
      → mở PR ("suggest")             [L1 — branch protection chặn push thẳng main]
      → release AI-lock
human → review PR → merge             (agent KHÔNG trong merge whitelist)
Gitea → webhook on merge
sync  → clone + `git lfs pull`        [BẮT BUỘC]
RAG   → incremental re-index (đúng index của repo đó)
Wiki.js → git sync module pull về → trang wiki cập nhật

Human claim file giữa chừng → HALT agent → branch bị bỏ.   [L3]
```

---

*Hết bản v0.5. Cần chốt ở review:*
1. **⚠️ Verify attribution của Wiki.js git module** (§4.3 #1) — **ưu tiên cao nhất**, vì nó ảnh hưởng đúng thứ team lead quý nhất.
2. **Xác nhận v1 chỉ index `security-kb`** (§11.1) — né rủi ro phân quyền + vừa budget GPU.
3. Chọn bản MCP server (§9).
4. Ngôn ngữ lock-service — Rust hay Python (§13 #1).
5. Chạy `corpus_survey.py` ngay khi có quyền truy cập data → chốt lại timeline (§7.2).
