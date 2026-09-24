# Đề xuất (v0.8): Team Security LLM-Wiki — Git + AI Gateway + RAG + MCP

| | |
|---|---|
| **Version** | 0.8 (Draft) — thay thế v0.1 → v0.7 |
| **Tác giả** | saltless-bruh |
| **Ngày** | 15/07/2026 |
| **Trạng thái** | Draft — chờ team lead review |
| **Thay đổi so với v0.7** | **Sửa một giả định SAI về phần cứng.** v0.7 lấy cấu hình **máy cá nhân** (RTX 3060 12GB) làm mốc triển khai — sai, vì đây là **dự án cấp phòng ban** trong công ty lớn, hạ tầng do công ty cấp. Hệ quả: **(1)** §8.2 đổi từ *cổng feasibility* thành **công cụ sizing**; **(2)** lập luận tier chuyển từ **chi phí** sang **chính sách** (kết luận **không đổi** — xem §8.3); **(3)** rủi ro *"chi phí index"* hạ từ **Cao → Trung bình** và đổi nội dung; **(4)** thêm ghi chú quy mô phòng ban: **không phụ thuộc Gitea** (§6) và **RBAC phải có trước khi mở rộng** (§13.1). Phần còn lại **không đổi**. |

> **Ghi chú về hướng đi của v0.5 → v0.6:** v0.5 định giá một phàn nàn **cosmetic** như một vấn đề **kiến trúc**, rồi chồng thêm lớp để trả giá cho nó (Wiki.js → static site → convert pipeline). Thiết kế **đã có sẵn câu trả lời từ v0.2: R5 — file substrate, ai dùng tool nào cũng được.** v0.6/v0.7 quay về đúng nguyên tắc đó. **Thiết kế đơn giản hơn cũng chính là thiết kế an toàn hơn** — lớp thêm vào để "cho thoải mái" lại là thứ đe doạ đúng tính năng team lead quý nhất (attribution).

---

## 1. Tóm tắt (Executive Summary)

**Cái gì:** Một **LLM-Wiki dùng chung cho team Cybersecurity** — lưu multi-data (raw code, PDF, doc/docx, md, image, excel/csv), cho cả người và AI/Agent đóng góp, kết nối vào **IDE/Agent của từng thành viên** để query. Self-hosted hoàn toàn trên **Docker**, local-first.

**Kiến trúc theo đúng chỉ đạo:**

> *[lead's feedback]*

```
  Wiki (nội dung)  →  AI GW (LiteLLM)  →  RAG (RAG-Anything)  →  Git (Gitea)
  md trong repo        model gateway        index + query          lưu trữ tập trung
                                                                   + giao diện web
```

> **"Wiki" ở đây là *sản phẩm* (kho tri thức md có cấu trúc), không phải một *phần mềm wiki* phải cài thêm.** Chính tên dự án là **LLM-Wiki**. Nội dung wiki sống trong git; Gitea web UI là nơi đọc/sửa trên trình duyệt.

**Sáu quyết định cốt lõi:**

| # | Quyết định | Vì sao (một dòng) |
|---|---|---|
| 1 | **Lưu trữ tập trung = Gitea** | Chỉ đạo team lead. Commit history = **ai sửa, sửa gì, lúc nào, agent hay người** — ưu điểm anh nêu đích danh |
| 2 | **Giao diện = Gitea web UI + tool tuỳ chọn** | Trả lời *[lead's feedback]* + *[lead's feedback]* với **0 container thêm**; và đúng **R5** — ai thích tool nào dùng tool đó |
| 3 | **RAG = RAG-Anything**, **tier theo repo** | Native multimodal; tier vì **need-to-know** — graph xuyên engagement đúng là thứ phải cấm (§8.3) |
| 4 | **AI GW = LiteLLM (local-only)** | **Chokepoint duy nhất** để chứng minh dữ liệu security không rời hạ tầng |
| 5 | **MCP** | Kết nối IDE/Agent của từng thành viên (R3) |
| 6 | **Lock = per-file AI mutex + human preempt** | Thoả từng mệnh đề L1/L2/L3 |

**Phân quyền (RBAC): Phase 2** theo chỉ đạo *[lead's feedback]*. Ranh giới repo vẫn dựng sẵn ở v1 để lắp vào sau **không phải tách repo lại**.

**Chi phí xây dựng:** chỉ **3 thứ nhỏ phải tự viết** (§15). Còn lại là cấu hình tool có sẵn.

---

## 2. Use case & Yêu cầu (Requirements)

### 2.1. Use case

**Quy mô:** đây là dự án **cấp phòng ban** trong một công ty lớn — **thử nghiệm ở team Cybersecurity trước**, rồi mở rộng. Hạ tầng do công ty cấp. Mục tiêu bản đầu: xử lý **5-10GB**, mở rộng lớn hơn về sau — nhưng **v1 tập trung vào core**, không làm scale engineering sớm.

Một team Cybersecurity cần một knowledge base dùng chung: gom **code, báo cáo (PDF/doc/docx), ghi chú (md), sơ đồ/screenshot (image), bảng dữ liệu (excel/csv)** vào một nơi, rồi **để IDE và Agent của từng thành viên truy vấn** như một nguồn context — *"hỏi tri thức của team ngay trong editor"*. Dữ liệu nhạy cảm (findings, có thể chứa thông tin khách hàng) → bắt buộc **local-first, self-hosted**.

### 2.2. Functional requirements

| ID | Yêu cầu | Đáp ứng ở |
|---|---|---|
| **R1** | Lưu trữ multi-data: raw code (mọi ngôn ngữ), PDF, doc/docx, md, image, excel/csv | §6, §12 |
| **R2** | **Cả con người và AI/Agent đều đóng góp được** | §5, §7 |
| **R3** | IDE và Agent của từng thành viên **kết nối để query** knowledge base | §8, §9 |
| **R4** | **Multimodal retrieval** — hỏi được cả nội dung trong PDF/hình/bảng, không chỉ text thuần | §8 |
| **R5** | **Ai cũng dùng được tool mình quen** (MS Office cho Office docs, VS Code/IDE cho code…) và vẫn sync được | §5 |

### 2.3. Lock requirements (team lead đặt ra)

| ID | Yêu cầu | Đáp ứng ở |
|---|---|---|
| **L1** | **Human edit → AI không được edit, chỉ được *suggest* trên cùng file.** | §7.1 |
| **L2** | **AI edit → AI khác không được edit cùng file; khác file thì OK.** | §7.2 |
| **L3** | **Human có thể edit khi AI đang edit**, hoạt động như **stop/pause** khi AI thêm nội dung sai. | §7.2, §7.3 |

Tổng quát: **Human > AI** (con người luôn thắng), **AI ⊥ AI** (hai agent loại trừ nhau trên cùng file).

### 2.4. Constraints

| ID | Ràng buộc | Đáp ứng ở |
|---|---|---|
| **C1** | **Local-first, self-hosted, Docker.** Không cloud trong đường đi của LLM/embedding. | §10, §14 |
| **C2** | **Sync = async (git-style).** Edit → save → commit/PR/merge. **Không** real-time co-editing. | §5.2 |
| **C3** | **Need-to-know.** Dữ liệu compartmentalized theo engagement/khách hàng. | §13 |
| **C4** | **RBAC / least-privilege.** Mỗi người có **role**; privileges do **team lead và boss cấp**. Quyền phải **thu hồi được** và **audit được**. → **Phase 2**. | §13 |

### 2.5. Non-goals (v1)

- **Real-time co-editing** kiểu Google Docs — đã loại (C2, §17).
- **Một UI edit native cả 6 định dạng** — **không tồn tại**, là giới hạn cấu trúc (§5.3).
- **Decentralization theo nghĩa kỹ thuật** (P2P/federated) — đã loại, mâu thuẫn với C4 (§13.2).
- **Cross-repo graph reasoning** — **đánh đổi có chủ đích** để lấy compartmentalization (§8.3).

---

## 3. Chỉ đạo team lead & cách v0.8 đáp ứng

> *[The team lead's feedback, quoted word for word in this draft, was removed before publication on 2026-10-01.]*

---

## 4. Design spine — chuỗi suy luận

> Mọi thành phần dẫn xuất từ **ba sự thật**. Sự thật đổi → phần treo dưới phải xem lại.

**Sự thật 1 — Consumer là IDE/Agent query, không phải người đọc trang wiki (R3)**
```
→ QUERY SYSTEM, không phải authoring system
   → Cần: multimodal index (§8) + MCP query layer (§9)
   → Store phải là thứ IDE đọc trực tiếp được → FILE, không phải DB/CRDT
```

**Sự thật 2 — Sync async (git-style) + team đã quen code/terminal (C2)**
```
→ Canonical store = GIT (§6)
   ├→ Git là file → BẤT KỲ file-based tool nào cũng edit được → R5 (§5)
   │    → *[lead's feedback]* là vấn đề CẢM GIÁC, không phải năng lực → giải bằng LỰA CHỌN TOOL,
   │       không phải bằng cách xây thêm một app  ← bài học v0.5→v0.6
   ├→ Gitea có web UI sẵn → đọc/sửa trên browser, 0 container thêm (§5)
   ├→ Git có PR + branch protection → chính là L1 (§7)
   ├→ Git có org/team/permission → móc treo RBAC Phase 2 (§13)
   ├→ Git có commit author → attribution + audit MIỄN PHÍ, không cần verify
   └→ Git KHÔNG merge được binary → format policy (§12) + lock quan trọng cho binary (§7.4)
```

**Sự thật 3 — Dữ liệu security-sensitive, multi-modal, compartmentalized**
```
→ Multi-modal → RAG-Anything (§8)
→ Security-sensitive → local-only → LiteLLM chokepoint (§10)
→ Compartmentalized → repo = ranh giới; RBAC Phase 2 (§13)
→ Chi phí index thật (đo được) → BẮT BUỘC tier: graph cho KB, vector cho archive (§8.3)
```

### 4.1. Traceability — yêu cầu → cơ chế → mục

| Yêu cầu | Cơ chế | Mục |
|---|---|---|
| **R1** Lưu multi-data | Gitea + Git LFS | §6, §12 |
| **R2** Người + AI cùng đóng góp | Người: commit (UI hoặc tool). Agent: **chỉ PR** | §5, §7 |
| **R3** IDE/Agent query | RAG-Anything + MCP | §8, §9 |
| **R4** Multimodal retrieval | RAG-Anything parse text/image/table | §8 |
| **R5** **Ai cũng dùng tool mình thích** | **File substrate + git** → mọi tool file-based là first-class | **§5** |
| **L1** Human edit → AI chỉ suggest | Branch protection + agent ngoài merge whitelist | §7.1 |
| **L2** AI ⊥ AI cùng file | Per-file AI mutex | §7.2 |
| **L3** Human preempt AI | Claim → HALT | §7.2 |
| **C1** Local-first, Docker | LiteLLM local-only routing | §10, §14 |
| **C2** Async sync | Git; Gitea UI đọc state server | §5.2 |
| **C3** Need-to-know | Repo = ranh giới need-to-know (dựng sẵn v1) | §6, §13 |
| **C4** RBAC — lead & boss cấp quyền | Gitea Org/Team → repo → index → MCP scope (Phase 2) | §13.2 |

---

## 5. Surfaces — "ai cũng dùng tool mình thích" (R5)

**Nguyên tắc (có từ v0.2, v0.6 quay về đúng nó):** canonical store là **file trong git** → git **không quan tâm tool nào ghi ra file**. Mọi **file-based tool** là first-class citizen. **Đây không phải thoả hiệp — đây là phần thưởng của việc chọn git.**

### 5.1. Bảng surfaces

| Surface | Dành cho | Mạnh ở |
|---|---|---|
| **Gitea web UI** (mặc định, 0 setup) | Ai không muốn clone; sửa nhanh; team lead duyệt PR | Browse, đọc md, sửa nhanh, review PR, xem lịch sử |
| **VS Code + Foam** | Người viết nội dung kỹ thuật | Edit mọi thứ dạng text, **xem được docx/xlsx/PDF**, wikilink/backlink/graph, **là nơi MCP client sống** |
| **MS Office** | docx / xlsx (edit thật) | Sửa trên clone → commit |
| **Obsidian / IDE khác** | Ghi chú cá nhân, code | Mở clone như vault; git như thường |

> **Không ép ai dùng cái gì.** Team lead thích UI → Gitea web. Engineer thích terminal → VS Code/CLI. Cả hai ghi vào **cùng một repo**.

### 5.2. Vì sao Gitea web UI đã đủ trả lời cả hai ý của team lead

> *[The team lead's feedback, quoted word for word in this draft, was removed before publication on 2026-10-01.]*

**Và giữ nguyên ưu điểm anh khen — bằng cấu trúc, không cần verify:** Gitea commit **dưới danh tính người đăng nhập** → *ai sửa, sửa gì, lúc nào* luôn đúng. Agent commit qua PR bằng account riêng → *agent hay người* luôn phân biệt được.

### 5.3. Xem được những loại dữ liệu nào? (câu hỏi thực tế)

| Loại | Gitea web UI | VS Code | RAG/MCP query |
|---|---|---|---|
| **md** | ✅ render | ✅ + Foam | ✅ |
| **code** | ✅ syntax highlight | ✅ native | ✅ |
| **image** | ✅ | ✅ | ⚠️ VLM (đắt — §8.3) |
| **csv** | ✅ render thành bảng | ✅ (Rainbow CSV) | ⚠️ |
| **PDF** | ⚠️ tải về / xem thô | ✅ (extension) | ✅ |
| **docx / xlsx** | ❌ | ✅ (Office Viewer) | ✅ |

**Kết luận thẳng:** **không trình duyệt nào render được docx/xlsx** — đây là giới hạn **cấu trúc**, mọi phương án đều dính (Wiki.js cũng không làm được). **Nhưng nó không phải vấn đề**, vì:

1. **VS Code xem được cả 6 loại** — ai cần mở spreadsheet thì mở VS Code hoặc Office. Đúng R5.
2. **Xem được ≠ hỏi được.** RAG-Anything **đã parse docx/xlsx vào index** → dù không render trên browser, vẫn **hỏi được qua MCP từ IDE**. Khoảng trống là *browse*, không phải *retrieval*.

> **Nếu sau này thật sự vướng:** thêm bước build-time convert docx/xlsx → HTML bằng LibreOffice headless, đặt cạnh file gốc (giữ nguyên bản gốc — chain-of-custody §12). **Hoãn sang v2** — chưa có bằng chứng là cần.

---

## 6. Lưu trữ tập trung — Gitea

**Là gì:** Gitea self-hosted là **nơi lưu trữ tập trung** duy nhất, **và** là giao diện web mặc định, **và** là identity provider cho Phase 2.

**Vì sao git (đúng chỉ đạo team lead):**

1. **Commit history = audit trail miễn phí** — ai sửa/xóa cái gì, lúc nào, agent hay người. Ưu điểm team lead nêu đích danh.
2. **PR + branch protection = cơ chế "AI chỉ được suggest"** (§7.1) — không phải build.
3. **File substrate** → IDE/Agent đọc trực tiếp; mọi tool file-based edit được (R5).
4. **Web UI sẵn** → trả lời *[lead's feedback]* + *[lead's feedback]* với **0 container thêm** (§5.2).
5. **Version + rollback** — sai thì revert.
6. **Móc treo RBAC Phase 2** — permission per-repo có sẵn (§13).

> **Ở quy mô phòng ban — thiết kế KHÔNG phụ thuộc Gitea.** Nó phụ thuộc vào *một **git platform self-hosted** có **PR + branch protection + webhook + permission***. Nếu công ty **đã có GitLab / GitHub Enterprise / Bitbucket**, **dùng cái đó** — đỡ dựng thêm một hệ thống, đỡ vướng policy hạ tầng, và đỡ phải xin thêm identity provider. Toàn bộ §6-§7 giữ nguyên, chỉ đổi tên platform. **Cần xác nhận sớm:** công ty đang dùng git platform nào?

**Cấu trúc repo:**

```
security-kb/          → tri thức chung        → index: GRAPH  (v1)
engagement-<tên>/     → theo từng engagement  → index: VECTOR (Phase 2)
```

> **Chia repo theo engagement NGAY v1** — không phải để phân quyền (đã dời Phase 2), mà vì: **(a)** chi phí index bắt buộc tier theo repo (§8.3); **(b)** khi RBAC lắp vào Phase 2, **ranh giới đã sẵn, không phải tách repo lúc đó** (rất đau).
>
> **Ràng buộc:** git permission là **per-repo**, không phải per-folder. → Cần granularity nhỏ hơn thì **tách repo**, đừng bịa cơ chế mới.

---

## 7. Lock & Collaboration

### 7.1. Ánh xạ yêu cầu → cơ chế

| Yêu cầu | Cơ chế | Ghi chú |
|---|---|---|
| **L1** Human edit → AI chỉ *suggest* cùng file | **Branch protection trên `main`**: chặn direct push, require PR + approval, **agent KHÔNG trong merge whitelist** | Enforce bởi **Gitea ở tầng server** — không dựa vào AI tự giác. Là **workflow control**, không phải module phân quyền → vẫn nằm v1. |
| **L2** AI edit → AI khác không edit cùng file | **Per-file AI mutex** (lock service) | Khác file vẫn **song song** |
| **L3** Human edit khi AI đang edit = stop/pause | Human claim → **HALT** → agent dừng, bỏ branch dở | Con người **luôn thắng** |

**Branch protection cụ thể:**

| Setting | Giá trị |
|---|---|
| Disable direct push to `main` | ✅ |
| Require Pull Request | ✅ |
| Require approvals | ≥ 1 |
| Restrict merge (whitelist) | Team lead (+ người anh chỉ định) — **agent không có tên** |
| Dismiss stale approvals | ✅ |

**Defense-in-depth:** kể cả token agent bị lộ hoặc agent có bug, **nó vẫn không ghi được vào `main`** — Gitea từ chối ở tầng server.

### 7.2. Lock service

```
lock:<repo>:<path> → { holder_id, role: "human"|"ai", acquired_at, ttl }
```

- **Agent acquire:** set `ai_locked` nếu file chưa `ai_locked`. Nếu đang `human_locked` → agent hạ xuống **suggest-only**.
- **Human preempt:** claim `human_locked` → **HALT** agent đang giữ lock.
- **TTL + heartbeat:** agent chết → lock tự hết hạn.

### 7.3. Presence signal — mảnh duy nhất git không cho sẵn

File không tự báo "đang có người sửa".
- **v1:** explicit claim (lệnh CLI nhỏ) — team đã quen terminal nên chi phí thấp.
- **v2:** VS Code extension tự claim/release khi open/close file.

### 7.4. Binary: lock là lớp bảo vệ DUY NHẤT

Git merge được text, **không merge được** `.docx`/`.xlsx`/`.png`. Hai người sửa cùng file Office → phải **chọn nguyên một bản** → **công của một bên mất**.

- **md/code:** lock hụt → git merge vẫn là lưới an toàn.
- **binary:** **không có lưới**. Lock là lớp duy nhất.

→ **Lock không phải thủ tục hình thức** — nó là thứ duy nhất bảo vệ file Office. Và đó cũng là lý do format policy (§12) giữ binary ở vùng read-mostly.

---

## 8. RAG layer — RAG-Anything + chiến lược TIER

### 8.1. Vì sao RAG-Anything

- **Native multimodal** — text, image, table, equation cùng một pipeline. Đúng mix dữ liệu của team.
- **Knowledge graph (LightRAG)** — với tri thức chung (technique, playbook), **quan hệ chính là giá trị**.
- **Incremental update** — chỉ reprocess phần đã đổi khi merge.
- **Có MCP server** → kết nối IDE/Agent.

### 8.2. Sizing hạ tầng — `corpus_survey.py` (đã bàn giao)

**Là gì:** script quét repo, lấy mẫu từng loại file, đo **text thật sự extract được trên mỗi byte**, rồi ước lượng số chunk và thời gian GPU cho lần index đầu.

**Vì sao vẫn cần dù hạ tầng đủ mạnh:** *"5-10GB"* **không cho biết khối lượng index**. 5GB screenshot gần như **không có text**; 5GB source code là **5GB text**. Chênh nhau **cả bậc độ lớn**. Script biến *"chưa biết"* thành **con số để provision máy**.

> **Đây là công cụ SIZING, không phải cổng feasibility.** Hạ tầng do **công ty cấp** (§2.1), không phải máy cá nhân — nên câu hỏi không phải *"có làm nổi không"* mà **"cần bao nhiêu GPU, và lần index đầu chạy bao lâu"**.

**Chạy ngay khi có quyền truy cập data** → xuất ra: lượng text extract được, số token, số chunk, ước lượng thời gian cho **từng chiến lược** (graph vs vector), số ảnh, và **% PDF là bản scan** cần OCR. Dùng chính con số đó để **provision**, thay vì đoán.

**Hai phát hiện từ lúc test script** — giữ nguyên giá trị **bất kể phần cứng**:

- **PDF scan vẫn có thể *khai báo font* mà không có text layer** → chỉ kiểm `pdffonts` là **không đủ**; script kiểm thêm **số ký tự / trang**. (Phát hiện được khi test, không phải lý thuyết.)
- **Ảnh chiếm phần lớn chi phí index nhưng giá trị query thấp nhất** — corpus test: **126 ảnh = 51% tổng chi phí**. → dẫn tới §8.3.

**Ràng buộc còn lại (C1) — không đổi:** dù hạ tầng lớn đến đâu, **inference phải nằm trong hạ tầng công ty**. Không thuê GPU cloud công cộng cho dữ liệu security. **LiteLLM (§10) là chokepoint** để chứng minh điều đó bằng **một file config**.

### 8.3. → Chiến lược TIER — lý do chính là **CHÍNH SÁCH**, không phải chi phí

| Repo | Chiến lược | Vì sao |
|---|---|---|
| `security-kb` (tri thức chung) | **GraphRAG đầy đủ** | Technique/playbook **liên kết chằng chịt** → graph thật sự đáng tiền. Và **không compartmentalized** → không vướng need-to-know |
| `engagement-*` (findings, report) | **Vector-only** | **Lý do chính — chính sách:** với findings, query graph *giá trị nhất* là **xuyên engagement** (*"CVE này xuất hiện ở khách hàng nào"*) — mà đó **đúng là câu need-to-know phải cấm** (C3). Trong phạm vi **một** engagement, graph gần như **không có gì để nhai**. *Lý do phụ: rẻ hơn ~100 lần.* |
| `*/evidence/` (screenshot) | **v1 không index** (`.ragignore`) | **Giá trị query thấp nhất** — hiếm khi cần search ngữ nghĩa một screenshot Burp. Bật lại **nếu team thực sự cần**, không phải vì không đủ GPU |
| PDF scan | Wave 3 (§8.4) | OCR tốn thời gian; ưu tiên report quan trọng trước |

> **Ghi chú trung thực về lập luận:** ở các bản trước, tier được biện minh **trước hết bằng chi phí GPU**. Với hạ tầng cấp phòng ban, **lập luận chi phí yếu đi nhiều**. Nhưng **lập luận chính sách vẫn nguyên vẹn**: `engagement-*` **không nên** có graph xuyên khách hàng — **bất kể có bao nhiêu GPU**. Thiết kế cố ý đứng trên **hai chân độc lập**, nên khi một chân yếu đi, **kết luận không đổi**.

### 8.4. Ingest theo wave — chiến lược **GIAO HÀNG**, không phải khẩu phần GPU

| Wave | Nội dung | Khi nào |
|---|---|---|
| **1** | markdown + code (`security-kb`) | **v1** — có thứ dùng được **ngay tuần đầu** |
| **2** | PDF có text layer + csv/xlsx | v2 |
| **3** | PDF scan (OCR) + ảnh trong `docs/` | v2 |
| — | evidence screenshots | **không index** (§8.3) |

**Vì sao vẫn chia wave dù GPU đủ:** để **ship giá trị sớm**. Wave 1 là phần **rẻ nhất và giá trị cao nhất** (tri thức team tự viết) → team có thứ dùng được trong **tuần đầu**, thay vì chờ index xong toàn bộ mới thấy gì. Đây là **thứ tự giao hàng**, không phải khẩu phần tài nguyên.

**Sync job:** Gitea webhook on merge → update clone → **`git lfs pull` (BẮT BUỘC)** → re-index đúng repo.
> Thiếu `git lfs pull` → index nhận **LFS pointer text** thay vì nội dung thật. **Lỗi im lặng** — index vẫn "chạy", chỉ vô dụng. **Health check:** assert index có nội dung thật, không phải pointer.

---

## 9. MCP query layer

**Là gì:** expose RAG qua **MCP** để IDE/Agent của từng thành viên cắm vào (Cursor, VS Code Copilot agent mode, Claude Code, Cline…).

**Vì sao:** đúng nguyên văn R3. Pattern đã trưởng thành.

**Lựa chọn (chốt ở review):** RAG-Anything MCP server (**ưu tiên** — giữ multimodal) · LightRAG MCP servers (30/22/3 tools, query modes naive/local/global/hybrid/mix) · code-RAG MCP servers · wrapper tự viết (fallback).

> **v1 không có authz gateway** (Phase 2 cùng RBAC) → **ai cắm MCP cũng query được index đang có**. Xem §13.1 — giảm thiểu bằng cách **v1 chỉ index `security-kb`**.

---

## 10. AI Gateway — LiteLLM

**Là gì:** gateway OpenAI-compatible. RAG-Anything trỏ vào nó cho **embedding + LLM + vision**. Phía sau là local inference (llama.cpp) + **vision model**.

**Vì sao:**

1. **Chokepoint DUY NHẤT để chứng minh "không cloud"** — mọi lời gọi model qua đây → audit **một** file config là đủ. **Lý do mạnh nhất** với dữ liệu security.
2. **Một endpoint ổn định** cho RAG — đổi/route model không đụng config RAG.
3. **Virtual keys per consumer** → rate limit, thu hồi độc lập.
4. **Vision routing** — multimodal parsing cần VLM, route riêng slot.

> **LiteLLM không tự inference** — nó là proxy/gateway.

---

## 11. Luồng dữ liệu

```
   NGƯỜI DÙNG                              AI / AGENT
   ├─ Gitea web UI (browser)               (Gitea account riêng)
   ├─ VS Code + Foam                             │
   ├─ MS Office (docx/xlsx)                      │ chỉ mở PR
   └─ Obsidian / IDE khác                        │
        │ commit                                 │
        ▼                                        ▼
 ┌────────────────────────────────────────────────────────┐
 │  GITEA — LƯU TRỮ TẬP TRUNG  (§6)                       │
 │  main (branch protected): md/code/pdf/img/xlsx + LFS   │
 │  commit history = ai / cái gì / lúc nào / agent-người  │
 └──────────┬─────────────────────────────────────────────┘
            │ webhook on merge
            ▼
    ┌───────────────┐      ┌──────────────────┐      ┌──────────────┐
    │ SYNC JOB §8.4 │─────▶│ RAG-ANYTHING §8  │◀────▶│ LITELLM §10   │
    │ clone+lfs pull│      │ index/repo, tier │      │ local-only   │
    └───────────────┘      └────────┬─────────┘      └──────────────┘
                                    │
                            ┌───────▼────────┐     ┌──────────────┐
                            │  MCP  §9       │     │  LOCK  §7    │
                            │ → IDE / Agent  │     │  mutex+HALT  │
                            └────────────────┘     └──────────────┘
```

---

## 12. Data & Format policy

| Loại | Lưu | RAG | Ghi chú |
|---|---|---|---|
| **md** | text, merge được | ✅ tốt nhất | Định dạng hạng nhất — nội dung wiki |
| **code** | text, merge được | ✅ (chunk theo function/class) | |
| **csv** | text | ⚠️ bảng | |
| **pdf** | binary → **LFS** | ✅ (scan → OCR, đắt) | Read-mostly |
| **docx/xlsx** | binary → **LFS** | ✅ | **Không merge được** (§7.4) |
| **image** | binary → **LFS** | ⚠️ VLM đắt | `evidence/` → `.ragignore` |

**Born-digital → markdown** (tri thức team tự viết): diff/merge được, PR review có ý nghĩa, RAG parse chuẩn nhất, mọi tool edit được.
**Received artifacts → giữ nguyên binary gốc** (report khách hàng, PDF vendor): **evidence / chain-of-custody** — bản gốc là **bằng chứng**, không "chuyển thể" rồi vứt gốc. Và read-mostly → no-merge hiếm khi nổ.

**Git LFS:** đặt `.gitattributes` **TRƯỚC file binary đầu tiên** — rewrite history sau rất đau.

**Không bao giờ vào repo:** secrets/credentials (git giữ lịch sử **vĩnh viễn**) · **malware samples** (AV/EDR quarantine clone của mọi người) · raw capture chứa live credentials.
**`.gitignore`** = không vào repo. **`.ragignore`** = trong repo nhưng không vào index. Hai lớp, hai mối lo.

---

## 13. Phase 2 — Modules phân quyền (RBAC)

> Theo chỉ đạo *[lead's feedback]* + *[lead's feedback]*. **Không build v1.** Ghi lại để Phase 2 lắp vào không phải làm lại.

### 13.1. ⚠️ Rủi ro của v1 không phân quyền — team lead cần biết

> **Ở quy mô phòng ban, RBAC không còn là "để sau cũng được".** Nhiều team = **nhiều ranh giới need-to-know**. Dời sang Phase 2 là **hợp lý cho bản thử nghiệm ở một team**. Nhưng **trước khi mở rộng ra cả phòng, RBAC phải có**. Điểm quan trọng: **ranh giới repo dựng sẵn ở v1** (§6) chính là thứ khiến Phase 2 là **lắp thêm**, không phải **làm lại**.

**v1 = index không có ranh giới.** Ai cắm MCP cũng query được mọi thứ trong index.

**Giảm thiểu cho v1 (rẻ, không phải build module):**
- **v1 CHỈ index `security-kb`**, hoãn `engagement-*` sang Phase 2 → **không có dữ liệu khách hàng trong index thì không có gì để leak**.
- **Trùng khớp hoàn toàn với wave 1** (§8.4) và với budget GPU (§8.2) → **không mất gì thêm**.
- Gitea permission per-repo vẫn bật (có sẵn, không phải code) → chặn ở tầng đọc file.

→ **Khuyến nghị: v1 chỉ index `security-kb`.** Một quyết định, ba lợi ích.

### 13.2. Thiết kế RBAC Phase 2 (sẵn, chờ chốt)

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

**Ba điểm đã chốt sẵn:**

1. **Gitea đã là identity provider** — dùng Gitea PAT/OAuth2, **không cần dựng SSO riêng**.
2. **Authz-aware MCP gateway** — check live với Gitea (cache TTL ~60s), **không dùng static token**. Lý do: static token gây **revocation drift** — thu hồi quyền mà token cũ vẫn query được, **thất bại âm thầm**.
3. **Physical index separation, KHÔNG phải filtered retrieval** — với **graph**, entity bị **merge xuyên nguồn ngay lúc index**, community summary **trộn** nội dung → filter lúc query là **quá muộn**. Ranh giới phải dựng ở tầng **vật lý**.

> **Thuật ngữ — đã đóng:** yêu cầu ban đầu gọi là *"decentralization"*. Phản hồi *[lead's feedback]* xác nhận ý là **lưu trữ tập trung + phân quyền theo role (RBAC)**, không phải decentralization kỹ thuật (P2P/federated).

---

## 14. Triển khai Docker — 8 container

| # | Service | Vai trò |
|---|---|---|
| 1 | `gitea` | Lưu trữ tập trung **+ web UI + identity** |
| 2 | `gitea-db` | Postgres cho Gitea |
| 3 | `litellm` | AI Gateway, local-only |
| 4 | `rag` | RAG-Anything / LightRAG |
| 5 | `rag-mcp` | MCP server cho IDE/Agent |
| 6 | `sync-job` | webhook → clone + lfs pull → re-index |
| 7 | `lock-svc` | per-file mutex + preempt |
| 8 | `redis-lock` | state cho lock |

> **v0.5 là 10 container** (thêm `wikijs` + `wiki-db`). Bỏ lớp wiki → **8**, và **mất luôn dual-store + sync interval + rủi ro attribution**.

**Hạ tầng (đã sửa ở v0.8):** đây là dự án **cấp phòng ban** — hạ tầng do **công ty cấp**, không phải máy cá nhân. **8 container là tải nhẹ**, không phải vấn đề. Với **GPU cho inference**: chạy `corpus_survey.py` (§8.2) để **provision đúng bằng số đo**, thay vì đoán. Vẫn giữ nguyên tắc **tách inference khỏi compute path của wiki** — không co-schedule GPU-bound với CPU/IO-bound. Xem **Phụ lục A**.

---

## 15. Build scope — v1 chỉ tự viết 3 thứ (+1 fallback nếu cần)

> Phần lớn hệ thống là **wiring tool có sẵn**. Bảng dưới là **toàn bộ** inventory — cả phần tự viết lẫn phần chỉ cấu hình. **Số thứ tự khớp với sheet "Build scope" trong file tracker.**

| # | Thành phần | Tự viết hay cấu hình | Quy mô | Phase |
|---|---|---|---|---|
| 1 | **Lock service** (mutex + preempt + TTL/heartbeat) | **TỰ VIẾT** | Nhỏ (~200-300 dòng) | **v1** |
| 2 | **Sync/re-index job** (webhook → clone + `git lfs pull` → index) | **TỰ VIẾT** | Rất nhỏ (script) | **v1** |
| 3 | **Presence/claim command** | **TỰ VIẾT** | Nhỏ | **v1** |
| 4 | `corpus_survey.py` (đo corpus trước khi index) | **TỰ VIẾT** | ✅ **XONG — đã bàn giao** | **v1** |
| 5 | VS Code extension cho presence | TỰ VIẾT | Vừa | v2 |
| 6 | Convert docx/xlsx → HTML để xem trên browser | TỰ VIẾT | Nhỏ — **chỉ khi thật sự vướng** (§5.3) | v2 |
| 7 | **Authz-aware MCP gateway** | TỰ VIẾT | Nhỏ–vừa | **Phase 2** |
| 8 | MCP wrapper (nếu server sẵn thiếu coverage) | TỰ VIẾT (**fallback**) | Nhỏ, **có thể không cần** | v1 (fallback) |
| 9 | Gitea + LFS + branch protection + web UI | CẤU HÌNH | Tool có sẵn | v1 |
| 10 | RAG-Anything / LightRAG | CẤU HÌNH | Tool có sẵn | v1 |
| 11 | LiteLLM | CẤU HÌNH | Tool có sẵn | v1 |
| 12 | MCP server | CẤU HÌNH | Tool có sẵn | v1 |

**Đếm cho đúng:** v1 **chắc chắn** tự viết **#1-#3** (`corpus_survey.py` #4 đã xong). **#8 là fallback** — chỉ viết nếu MCP server chọn ở §9 thiếu coverage; nhiều khả năng **không cần**. Tất cả phần còn lại (#9-#12) là **cấu hình tool có sẵn**.

> **So với v0.5:** bỏ hẳn hạng mục *"patch attribution cho Wiki.js git module"* — **rủi ro Cao đó không còn tồn tại** vì đã bỏ Wiki.js.

## 16. Lộ trình

**v1 (MVP)**
1. Gitea + Git LFS + `.gitattributes` (**trước** binary đầu tiên) + **branch protection** (§7.1).
2. **Cho team lead xem Gitea web UI** → xác nhận cảm giác OK (§5.2). *Rẻ nhất, làm sớm nhất.*
3. LiteLLM (local-only) + backend llama.cpp + VLM.
4. RAG-Anything — **chỉ index `security-kb`, GraphRAG** (§13.1).
5. MCP server → cắm vào IDE của team.
6. Lock service + presence claim command.
7. Sync job (webhook + `git lfs pull` + health check).
8. **Chạy `corpus_survey.py` ngay khi có quyền truy cập data** → chốt timeline bằng số thật.

**v2**
- Wave 2/3 ingest (PDF, xlsx, ảnh `docs/`) theo budget GPU.
- VS Code extension cho presence.
- Code-aware chunking; scale storage backend (Postgres/Milvus/Neo4j).
- (Nếu vướng) convert docx/xlsx → HTML để xem trên browser.

**Phase 2**
- Chốt modules phân quyền với team lead & boss.
- Gitea Org/Team = role; branch protection theo role.
- Authz-aware MCP gateway (check live, cache TTL ~60s).
- Index `engagement-*` (vector-only) + physical separation.

---

## 17. Rejected alternatives — và vì sao

| Phương án | Vì sao loại |
|---|---|
| **Wiki.js** (có ở v0.5) | Điểm bán chính là **WYSIWYG** — nhưng **team đã quen markdown/terminal**, nên giá trị ≈ 0. Đổi lại phải trả: **2 container + Postgres**, **dual-store** (DB chính + git sync → sync interval, cửa sổ conflict), và **rủi ro Cao: có thể commit gộp vào 1 service account → mất attribution**, đúng thứ team lead quý nhất. **Trả giá kiến trúc cho một vấn đề cosmetic.** |
| **Static site (MkDocs/Docusaurus) + Gitea editor** | Đẹp hơn Gitea UI thật, attribution vẫn chuẩn — nhưng vẫn là **thêm một build pipeline + container** cho **QoL**. Gitea UI đã đủ. **Giữ lại làm phương án v2 nếu team thấy Gitea UI quá xấu.** |
| **OpenWiki (langchain-ai)** | **Không phải wiki** — là **CLI sinh documentation cho codebase**. (a) CLI → **thêm terminal**, không giải quyết *[lead's feedback]*; (b) **không có UI cho người viết**; (c) personal mode là `~/.openwiki/wiki` — **per-machine, không phải server cho team**; (d) connector là **cloud SaaS** (Gmail/Notion/X/Tavily) → phá C1; (e) **nhắm vào codebase**, mà corpus của team chủ yếu là findings/report/evidence → **không có gì để nhai**; (f) v0.1.2, 123 commit — rất sớm. *Ghi nhận: mô hình CI-mở-PR của nó **trùng khớp L1** — xác nhận thiết kế của mình đúng hướng.* |
| **Affine** | Store là **CRDT/DB, không phải file** → IDE/Agent không đọc trực tiếp; **không có PR model** → L1 không có chỗ enforce; self-hosted **thiếu native MCP**, ít integration (team lead đã gặp ở localhost); sync markdown↔CRDT là project riêng, lossy. |
| **Obsidian làm store/UI chung** | App **single-user**; `linuxserver/obsidian` là **KasmVNC remote desktop** — một session; **không có merge layer** → human + agent cùng file có thể **mất dữ liệu thẳng**. *(Vẫn OK làm editor cá nhân trên clone — R5.)* |
| **Dify** | RAG native **vector-only** → phải tự build pipeline per-type; orchestration phục vụ **chat app**, không phải query-KB spine. |
| **Nextcloud + OnlyOffice** | Thứ duy nhất edit Office trong browser — nhưng **storage riêng**, yếu với code, **không git-back sạch** → phá substrate. |
| **VS Code là surface DUY NHẤT** | Không giải được *[lead's feedback]*: local clone → phải `git pull`. Shared code-server → **chung git identity → mất attribution**. Per-user code-server → clone riêng → sync quay lại. *(Vẫn là surface chính cho engineer — R5.)* |
| **GraphRAG cho `engagement-*`** | **Không phải vì chi phí** (hạ tầng phòng ban đủ sức) mà vì **chính sách**: graph xuyên engagement chính là thứ **need-to-know phải cấm** (C3, §8.3). Trong một engagement, graph gần như không có gì để nhai. |
| **Index ảnh evidence ở v1** | **Giá trị query thấp nhất** — hiếm khi cần search ngữ nghĩa một screenshot Burp; và chiếm ~51% chi phí index. **Bật lại nếu team cần**, không phải vì thiếu GPU. |
| **Single flat index + metadata filtering** (Phase 2) | Với graph, entity **đã merge xuyên nguồn lúc index** → filter lúc query **quá muộn**. Phải physical separation. |
| **Decentralization kỹ thuật (P2P)** | Team lead chốt *[lead's feedback]* → đóng. |

---

## 18. Rủi ro & Giả định

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| **v1 không phân quyền** → index không ranh giới | **Cao** | **v1 chỉ index `security-kb`** (§13.1) — trùng wave 1 + budget GPU, không mất gì thêm |
| **Chưa sizing được hạ tầng** cho tới khi chạy `corpus_survey.py` — "5-10GB" không cho biết khối lượng index | Trung bình | **Chạy `corpus_survey.py` ngay khi có quyền truy cập data** (§8.2) → provision bằng số đo. Không phải rủi ro feasibility, là rủi ro **kế hoạch** |
| **Team lead vẫn thấy Gitea UI *[lead's feedback]*/xấu** | Trung bình | **Cho xem sớm** (roadmap #2) — rẻ nhất. Nếu không ổn → static site (§17) là fallback |
| **Presence phụ thuộc kỷ luật** (quên claim) | Trung bình | Team quen terminal → claim command chi phí thấp; v2 làm VS Code extension |
| **Quên `git lfs pull`** → index toàn LFS pointer | Trung bình (**lỗi im lặng**) | Health check: assert index có nội dung thật |
| **Đặt Git LFS threshold muộn** → phải rewrite history | Trung bình | `.gitattributes` **trước** binary đầu tiên |
| **RAG-Anything MCP maturity** (community) | Trung bình | Verify sớm; fallback LightRAG MCP hoặc wrapper (§9) |
| **Công ty có thể ĐÃ CÓ git platform** (GitLab/GitHub EE) → dựng thêm Gitea là thừa & vướng policy | Trung bình | **Xác nhận sớm** (§6). Thiết kế không phụ thuộc Gitea — chỉ cần PR + branch protection + webhook + permission |
| **Mở rộng quá 5-10GB** (team lead: *[lead's feedback]*) | Thấp (v1) | LightRAG hỗ trợ đổi backend (Postgres/Milvus/Neo4j — §8.1) mà **không đổi kiến trúc**. **Không làm scale engineering ở v1** — core trước |
| **PDF scan vẫn khai báo font** → `pdffonts` không đủ | Thấp–TB | `corpus_survey.py` đã kiểm thêm ký tự/trang (đã test) |
| **Không xem được docx/xlsx trên browser** | **Thấp** | Giới hạn **cấu trúc**, mọi phương án đều dính. VS Code xem được; RAG vẫn hỏi được (§5.3). Convert → v2 nếu vướng |

> **Đã biến mất so với v0.5:** rủi ro Cao *"Wiki.js commit gộp vào 1 service account → mất attribution"* — bỏ Wiki.js thì không còn.

---

## Phụ lục A — `docker-compose` (rút gọn, 8 service)

```yaml
networks:
  wiki-net: { external: true }   # docker network create wiki-net

services:
  gitea:                          # §6 — LƯU TRỮ TẬP TRUNG + WEB UI + IDENTITY
    image: gitea/gitea:latest
    environment:
      GITEA__database__DB_TYPE: postgres
      GITEA__database__HOST: gitea-db:5432
      GITEA__server__LFS_START_SERVER: "true"     # §12 — LFS bật từ đầu
    volumes: ["gitea-data:/data"]
    ports: ["3000:3000"]          # ← team lead mở cái này trên browser (§5.2)
    depends_on: [gitea-db]
    networks: [wiki-net]
    # Cấu hình sau khi lên: branch protection trên main (§7.1)

  gitea-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: gitea
      POSTGRES_USER: gitea
      POSTGRES_PASSWORD: ${GITEA_DB_PASSWORD}
    volumes: ["gitea-db-data:/var/lib/postgresql/data"]
    networks: [wiki-net]

  litellm:                        # §10 — AI GATEWAY, local-only
    image: ghcr.io/berriai/litellm:main-latest
    command: ["--config", "/app/config.yaml"]
    volumes: ["./litellm-config.yaml:/app/config.yaml:ro"]
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
    networks: [wiki-net]
    # backends (llama.cpp + VLM) trong config.yaml — KHÔNG có cloud provider

  rag:                            # §8 — RAG-Anything / LightRAG
    build: { context: ./rag }
    environment:
      LLM_BINDING: openai
      LLM_BINDING_HOST: http://litellm:4000
      EMBEDDING_BINDING_HOST: http://litellm:4000
      INDEX_MODE: per-repo                        # §8.3 — tier theo repo
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
    # Phase 2: đặt authz-aware gateway phía trước (§13.2)

  sync-job:                       # §8.4 — webhook → clone + lfs pull → re-index
    build: { context: ./sync-job }
    environment:
      GITEA_URL: http://gitea:3000
      GITEA_TOKEN: ${GITEA_SYNC_TOKEN}
      RAG_URL: http://rag:9621
      LFS_PULL: "true"                            # BẮT BUỘC — §8.4
    volumes: ["repo-clones:/data/repos"]
    depends_on: [gitea, rag]
    networks: [wiki-net]

  lock-svc:                       # §7 — per-file AI mutex + human preempt
    build: { context: ./lock-svc }
    environment:
      REDIS_URL: redis://redis-lock:6379
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

Human claim f:
  set human_locked{self}
  if ai_locked{agent} -> HALT agent; agent dừng + bỏ branch dở              [L3]

Release: agent sau khi mở PR hoặc bị HALT · human khi rời file
An toàn: AI-lock có TTL + heartbeat -> agent chết thì lock tự hết hạn
Ưu tiên: human_locked LUÔN thắng ai_locked                                  [Human > AI]

Lớp 1 (phối hợp): lock service
Lớp 2 (enforce)  : branch protection — agent KHÔNG merge được kể cả khi lock hỏng
```

## Phụ lục C — Luồng agent authoring

```
agent → acquire AI-lock(files)        [L2]
      → branch → soạn nội dung
      → mở PR ("suggest")             [L1 — branch protection chặn push thẳng main]
      → release AI-lock
human → review PR → merge             (agent KHÔNG trong merge whitelist)
Gitea → webhook on merge
sync  → clone + `git lfs pull`        [BẮT BUỘC] → health check
RAG   → incremental re-index (đúng index của repo đó)

Human claim file giữa chừng → HALT agent → branch bị bỏ.   [L3]
```

## Phụ lục D — Repo layout

```
security-kb/                     # tri thức chung          → index: GRAPH (v1)
├── .gitattributes               # LFS rules (§12)
├── .gitignore                   # secrets never enter (§12)
├── .ragignore                   # trong repo, không index (§12)
├── docs/                        # born-digital → markdown  ← NỘI DUNG WIKI
│   ├── playbooks/
│   ├── techniques/
│   └── postmortems/
├── code/                        # tools, scripts, PoC
├── data/                        # csv / xlsx
└── assets/                      # diagram, architecture (LFS)

engagement-<tên>/                # theo engagement          → index: VECTOR (Phase 2)
├── .gitattributes
├── .ragignore                   # evidence/ nằm ở đây
├── findings/                    # born-digital → markdown
├── reports/                     # received artifacts — giữ gốc (LFS)
└── evidence/                    # screenshots (LFS) — KHÔNG index (§8.3)

# Thư mục TRONG repo = tổ chức, KHÔNG phải security boundary.
# Boundary = repo.  Cần granularity nhỏ hơn → TÁCH REPO.
```

---

*Hết bản v0.8. Cần chốt ở review:*
1. **Cho team lead xem Gitea web UI** (§5.2) — *rẻ nhất, quyết định nhiều nhất*. OK → không build gì thêm. Không OK → static site (§17) là fallback.
2. **Xác nhận v1 chỉ index `security-kb`** (§13.1) — một quyết định, ba lợi ích.
3. Chọn bản MCP server (§9).
4. Ngôn ngữ lock-service — Rust hay Python (§15 #1).
5. Chạy `corpus_survey.py` ngay khi có quyền truy cập data (§8.2).
