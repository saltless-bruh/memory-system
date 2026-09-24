# Đề xuất (v0.9): Team Security LLM-Wiki — Git + AI Gateway + RAG + MCP

| | |
|---|---|
| **Version** | 0.9 (Draft) — thay thế v0.1 → v0.8 |
| **Tác giả** | saltless-bruh |
| **Ngày** | 15/07/2026 |
| **Trạng thái** | Draft — chờ team lead review |
| **Thay đổi so với v0.8** | **(A) Tiếp thu 4 phát hiện từ review độc lập:** §9.1 **Seed Memory** (chống *MCP Client Amnesia* — agent không biết KB có gì nên không gọi tool); §8.4 **Gatekeeper assertion** cho LFS; §7.5 **quy tắc ghi THAY THẾ thay vì APPEND** (chống ảo giác do file tự mâu thuẫn); §8.2 **sizing chiều Query** (trước chỉ sizing chiều Ingest). **(B) Chốt `lock-svc` = Python** (§15). **(C) Ghi lại 3 đề xuất từ review bị TỪ CHỐI + lý do** (§17) để không bị nêu lại. **(D) Rà soát *mơ hồ*:** mọi *"verify sớm" / "chốt ở review" / "nếu cần"* được thay bằng **tiêu chí PASS/FAIL và điều kiện kích hoạt cụ thể**. |

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

## 3. Chỉ đạo team lead & cách v0.9 đáp ứng

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

**Vì sao mục này tồn tại:** trả lời **R5** và hai phản hồi của team lead (*[lead's feedback]*, *[lead's feedback]*) — **mà không phải xây thêm một app nào**.

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

> **Ở quy mô phòng ban — thiết kế KHÔNG phụ thuộc Gitea.** Nó phụ thuộc vào *một **git platform self-hosted** có **PR + branch protection + webhook + permission***. Nếu công ty **đã có GitLab / GitHub Enterprise / Bitbucket**, **dùng cái đó** — đỡ dựng thêm một hệ thống, đỡ vướng policy hạ tầng, và đỡ phải xin thêm identity provider. Toàn bộ §6-§7 giữ nguyên, chỉ đổi tên platform. **Điều kiện kích hoạt:** đây là **roadmap #0** — việc đầu tiên, **trước** khi dựng bất cứ thứ gì. Nếu công ty đã có GitLab/GitHub EE **và** nó có đủ 4 thứ (PR · branch protection · webhook · permission per-repo) → **dùng cái đó, không dựng Gitea**. Chỉ dựng Gitea nếu **thiếu ≥1** trong 4.

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

**Vì sao mục này tồn tại:** đây là **yêu cầu riêng của team lead** (L1/L2/L3 — §2.3) và là **mảnh DUY NHẤT git không cho sẵn**. Mọi thứ khác trong đề xuất là cấu hình tool có sẵn; mục này là lý do §15 có code phải tự viết. **§7.1-§7.4 chặn hai bên ghi cùng lúc. §7.5 chặn một agent ghi một mình nhưng làm file tự mâu thuẫn** — hai lỗi khác nhau, hai cơ chế khác nhau.

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


### 7.5. Quy tắc ghi của Agent: **THAY THẾ**, không **APPEND**

**Vì sao mục này tồn tại:** §7.1-§7.4 chặn *hai người ghi cùng lúc*. Mục này chặn một lỗi khác hẳn — **một agent ghi một mình, đúng luật, nhưng làm file tự mâu thuẫn**.

**Vấn đề:** agent cập nhật một sự thật đã đổi (IP đổi `10.0.0.1` → `10.0.0.5`) bằng cách **chèn thêm dòng** vào cuối file → file chứa **cả cũ lẫn mới**. RAG retrieve **cả hai** (cả hai đều tương đồng ngữ nghĩa cao) → LLM nhận context mâu thuẫn → **ảo giác**.

> **Lỗi nằm ở FILE, không phải INDEX.** Re-index **không cứu được** — file *thật sự* chứa hai sự thật trái nhau. Không có cách nào để retrieval "đoán" cái nào đúng.

| Loại thay đổi | Cách ghi bắt buộc |
|---|---|
| **Sự thật thay thế** (IP đổi, trạng thái lỗ hổng đổi, cấu hình đổi) | **Đọc toàn file → sửa tại chỗ → ghi đè toàn bộ file.** Sự thật cũ phải **biến mất** |
| **Sự thật bổ sung** (một finding mới, độc lập với cái cũ) | Append OK |

**Vì sao whole-file rewrite:** nó **idempotent** — ghi lại cùng nội dung hai lần cho **cùng** kết quả. Một chuỗi append thì **không**.

> **Lịch sử:** nguyên tắc này có từ **v0.1** (khi sink còn là Affine, mọi lần ghi được mô hình hoá là **atomic whole-document publish**). Khi chuyển sang git nó **bị rơi mất**. v0.9 đưa lại — **PR review (§7.1) bắt được vi phạm, nhưng phòng vẫn hơn chữa.**

**Tiêu chí Done:** review một PR của agent sửa một sự thật đã tồn tại → **diff phải hiện dòng cũ bị xoá** (`-`), không phải chỉ thêm dòng mới (`+`).

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

**Hai chiều sizing — đừng nhầm:**

| Chiều | Đo cái gì | Công cụ | Quyết định cái gì |
|---|---|---|---|
| **Ingest** (lần đầu + incremental) | Khối lượng text → số chunk → giờ GPU | `corpus_survey.py` ✅ | Provision GPU cho lần index đầu |
| **Query** (liên tục, giờ cao điểm) | **Số session đồng thời** (kỹ sư + agent) → QPS → độ trễ | ⚠️ **chưa có — cần số từ team lead** | Cấu hình queue `litellm`, tài nguyên `rag-mcp` |

> **Chiều Query trước v0.9 bị bỏ sót.** Nó quan trọng vì **coding agent chạy vòng lặp tự trị** gọi tool liên tục — **tải khác hẳn** con người gõ tay. Không sizing → **HTTP 429/503** lúc cao điểm, và triệu chứng sẽ giống "RAG chậm/hỏng" chứ không giống "thiếu tài nguyên".

**Hai câu cần team lead trả lời trước khi provision (roadmap #8b):**
1. Lúc đỉnh tải: bao nhiêu **kỹ sư** + bao nhiêu **agent** chạy đồng thời?
2. Agent chạy **autonomous mode** (vòng lặp liên tục, gọi tool dày) hay **chat-assist** (theo lượt)?

**Ràng buộc còn lại (C1) — không đổi:** dù hạ tầng lớn đến đâu, **inference phải nằm trong hạ tầng công ty**. Không thuê GPU cloud công cộng cho dữ liệu security. **LiteLLM (§10) là chokepoint** để chứng minh điều đó bằng **một file config**.

### 8.3. → Chiến lược TIER — lý do chính là **CHÍNH SÁCH**, không phải chi phí

| Repo | Chiến lược | Vì sao |
|---|---|---|
| `security-kb` (tri thức chung) | **GraphRAG đầy đủ** | Technique/playbook **liên kết chằng chịt** → graph thật sự đáng tiền. Và **không compartmentalized** → không vướng need-to-know |
| `engagement-*` (findings, report) | **Vector-only** | **Lý do chính — chính sách:** với findings, query graph *giá trị nhất* là **xuyên engagement** (*"CVE này xuất hiện ở khách hàng nào"*) — mà đó **đúng là câu need-to-know phải cấm** (C3). Trong phạm vi **một** engagement, graph gần như **không có gì để nhai**. *Lý do phụ: rẻ hơn ~100 lần.* |
| `*/evidence/` (screenshot) | **v1 không index** (`.ragignore`) | **Giá trị query thấp nhất** — hiếm khi cần search ngữ nghĩa một screenshot Burp. **Điều kiện bật lại:** có **≥3 yêu cầu thật** từ thành viên team dạng *"tôi cần tìm screenshot theo nội dung"* mà `docs/` không đáp ứng được → khi đó bật VLM cho **thư mục cụ thể đó**, không bật toàn bộ |
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

**Sync job:** Gitea webhook on merge → update clone → **`git lfs pull` (BẮT BUỘC)** → **Gatekeeper assertion** → re-index đúng repo → sinh `index.md` cho Seed Memory (§9.1).

**Gatekeeper assertion — bắt buộc, không phải tuỳ chọn:**

```
sau git lfs pull:
  lấy ngẫu nhiên N=5 file đã track LFS
  nếu nội dung bắt đầu bằng "version https://git-lfs.github.com/spec/v1"
      → đó là LFS POINTER, không phải file thật
      → DỪNG pipeline ngay, KHÔNG index, phát alert
```

> **Vì sao phải assert thay vì tin `git lfs pull`:** LFS pointer là **text hợp lệ**. RAG parser sẽ **index nó thành công** như một tài liệu bình thường, **không ném exception**. Không có assert thì lỗi này **im lặng** — index vẫn "chạy", chỉ chứa rác, và **không ai biết** cho tới khi query trả về vô nghĩa. Đây là lý do nó nguy hiểm hơn một lỗi crash.

---

## 9. MCP query layer

**Là gì:** expose RAG qua **MCP** để IDE/Agent của từng thành viên cắm vào (Cursor, VS Code Copilot agent mode, Claude Code, Cline…).

**Vì sao mục này tồn tại:** đây là **R3 nguyên văn** — *"IDE và Agent của từng thành viên kết nối để query"*. Không có lớp này thì toàn bộ §8 (RAG) **không ai dùng được**.

### 9.1. Seed Memory — chống "MCP Client Amnesia"

**Vấn đề:** agent kết nối qua MCP chỉ thấy **tên hàm** (`query`, `search`) mà **không biết trong KB có gì**. Hệ quả: model trả lời bằng **trọng số gốc** của nó thay vì gọi tool — trừ khi người dùng **ép thủ công bằng prompt**.

> **Đây là cách R3 thất bại trong THỰC TẾ, không phải trên giấy.** Một RAG hoàn hảo mà agent **không bao giờ gọi** thì giá trị bằng **0**.

**Giải pháp — deterministic, không cần LLM:**

1. `sync-job` (§8.4) sau mỗi lần re-index thành công → sinh **`index.md`** cho repo: bản đồ các phân vùng tri thức (`playbooks/`, `techniques/`, `postmortems/`, `code/`…) + số file + chủ đề chính từng thư mục.
2. `rag-mcp` đọc `index.md` lúc thiết lập session → **inject vào trường `instructions` của MCP** và vào `description` của tool query.
3. Client biết **KB chứa gì** *trước khi* quyết định có gọi tool hay không.

**Vì sao rẻ:** `sync-job` **đã** duyệt repo rồi, và **cấu trúc thư mục ở Phụ lục D CHÍNH LÀ bản đồ**. Chỉ là sinh file + inject — **deterministic, không tốn GPU**.

**Tiêu chí Done (đo được):** mở một IDE **mới**, hỏi một câu thuộc `security-kb` **mà không nhắc tên tool** → agent **tự** gọi `rag_query`. Nếu phải ép bằng prompt thủ công → **Seed Memory chưa đạt**.

### 9.2. Chọn MCP server — tiêu chí PASS/FAIL

**Ứng viên, theo thứ tự ưu tiên:**

| # | Ứng viên | Ưu tiên vì |
|---|---|---|
| 1 | **RAG-Anything MCP server** | Giữ **multimodal** nguyên vẹn từ index → client. Dữ liệu team có PDF/image/xlsx → đây là **mặc định** |
| 2 | LightRAG MCP servers (bản 30/22/3 tools) | Nhiều tool + query modes, nhưng tối ưu chủ yếu cho **text-based** |
| 3 | code-RAG MCP servers | Chỉ hợp nếu KB nghiêng hẳn về code — **không phải trường hợp này** (corpus là findings/report) |
| 4 | **Wrapper tự viết** (§15 #8) | **Chỉ khi #1-#3 đều trượt** tiêu chí dưới |

**Tiêu chí PASS/FAIL — chạy ở roadmap #5, TRƯỚC khi viết dòng code nào:**

| # | Tiêu chí | Vì sao bắt buộc |
|---|---|---|
| 1 | Query trả về nội dung **trích từ PDF/ảnh** đã index, không chỉ text thuần | **R4** — multimodal retrieval |
| 2 | Cho phép **set `instructions` / tool `description`** lúc runtime | **§9.1** — Seed Memory không có chỗ inject thì vô dụng |
| 3 | Chạy **HTTP transport** + bearer token trong Docker | **§14** — deploy model |
| 4 | Trỏ được vào **nhiều index** (một index / repo) | **§13.2** — Phase 2 physical separation |
| 5 | Cắm được vào **≥2 client** (VS Code Copilot agent mode + Claude Code) | **R3** — "IDE của từng thành viên", không phải một IDE |

**Điều kiện kích hoạt build item #8 (wrapper):** **bất kỳ ứng viên #1-#3 nào cũng trượt ≥1 tiêu chí bắt buộc** → viết wrapper mỏng quanh HTTP API của LightRAG server (~150-250 dòng). **Nếu #1 đạt cả 5 → KHÔNG viết wrapper**, xoá item #8 khỏi build scope.

> **v1 không có authz gateway** (Phase 2 cùng RBAC) → **ai cắm MCP cũng query được index đang có**. Giảm thiểu: **v1 chỉ index `security-kb`** (§13.1).

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

**Vì sao mục này tồn tại:** §5-§10 mô tả **từng lớp riêng lẻ**. Mục này cho thấy **chúng nối vào nhau thế nào** — một chỗ để kiểm tra rằng không lớp nào bị treo lơ lửng.

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

**Vì sao mục này tồn tại:** §6 nói *lưu ở đâu*; mục này nói **lưu dưới dạng gì và vì sao**. Nó tồn tại vì **một sự thật kỹ thuật**: git **merge được text, không merge được binary** (§7.4). Mọi quy tắc dưới đây đều dẫn xuất từ đó — không phải sở thích định dạng.

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

**Vì sao mục này tồn tại:** chỉ đạo team lead là *[lead's feedback]*. Mục này chứng minh **8 container là ĐỦ** cho toàn bộ §5-§11 — và mỗi container **map 1-1 với một lớp có lý do tồn tại** ở §4 (design spine). Không container nào ở đây mà không trả lời một yêu cầu.

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

**Hạ tầng:** đây là dự án **cấp phòng ban** — hạ tầng do **công ty cấp**, không phải máy cá nhân. **8 container là tải nhẹ**, không phải vấn đề. Với **GPU cho inference**: chạy `corpus_survey.py` (§8.2) để **provision đúng bằng số đo**, thay vì đoán. Vẫn giữ nguyên tắc **tách inference khỏi compute path của wiki** — không co-schedule GPU-bound với CPU/IO-bound. Xem **Phụ lục A**.

---

## 15. Build scope — v1 chỉ tự viết 3 thứ (+1 fallback có điều kiện)

**Vì sao mục này tồn tại:** đây là **lập luận về rủi ro giao hàng**. Bề mặt code tự viết càng nhỏ → càng ít bug, càng dễ bàn giao. Mục này chứng minh **hầu hết hệ thống là cấu hình, không phải code**.

> Phần lớn hệ thống là **wiring tool có sẵn**. Bảng dưới là **toàn bộ** inventory — cả phần tự viết lẫn phần chỉ cấu hình. **Số thứ tự khớp với sheet "Build scope" trong file tracker.**

| # | Thành phần | Tự viết hay cấu hình | Quy mô | Phase |
|---|---|---|---|---|
| 1 | **Lock service** (mutex + preempt + TTL/heartbeat) — **Python** (FastAPI + async redis) | **TỰ VIẾT** | Nhỏ (~200-300 dòng) | **v1** |
| 2 | **Sync/re-index job** (webhook → clone + `git lfs pull` → index) | **TỰ VIẾT** | Rất nhỏ (script) | **v1** |
| 3 | **Presence/claim command** | **TỰ VIẾT** | Nhỏ | **v1** |
| 4 | `corpus_survey.py` (đo corpus trước khi index) | **TỰ VIẾT** | ✅ **XONG — đã bàn giao** | **v1** |
| 5 | VS Code extension cho presence | TỰ VIẾT | Vừa | v2 |
| 6 | Convert docx/xlsx → HTML để xem trên browser | TỰ VIẾT | Nhỏ — **chỉ khi thật sự vướng** (§5.3) | v2 |
| 7 | **Authz-aware MCP gateway** | TỰ VIẾT | ~300-400 dòng (proxy mỏng) | **Phase 2** |
| 8 | MCP wrapper — **chỉ nếu §9.2 trượt tiêu chí** | TỰ VIẾT (**có điều kiện**) | ~150-250 dòng | v1 (**có điều kiện**) |
| 9 | Gitea + LFS + branch protection + web UI | CẤU HÌNH | Tool có sẵn | v1 |
| 10 | RAG-Anything / LightRAG | CẤU HÌNH | Tool có sẵn | v1 |
| 11 | LiteLLM | CẤU HÌNH | Tool có sẵn | v1 |
| 12 | MCP server | CẤU HÌNH | Tool có sẵn | v1 |

**Đếm cho đúng:** v1 **chắc chắn** tự viết **#1-#3** (`corpus_survey.py` #4 đã xong). **#8 có điều kiện** — chỉ viết **nếu và chỉ nếu** §9.2 cho kết quả trượt. Phần còn lại (#9-#12) là **cấu hình tool có sẵn**.

**Chốt ngôn ngữ `lock-svc` = Python** (FastAPI + async redis). Ba lý do:
1. **Redis lo phần khó, không phải ngôn ngữ.** Tính nguyên tử nằm ở `SET NX PX` + Lua compare-and-delete — service chỉ là lớp bọc mỏng. Rust **không mua thêm** tính đúng đắn ở đây.
2. **Bus factor** — đây là hệ thống **cấp phòng ban**. Một service Rust mà **chỉ một người sửa được** là nợ vận hành. Kỹ sư security trong team đọc/sửa Python được ngay.
3. **Cùng hệ sinh thái với agent** — logic HALT/preempt phải khớp với framework agent, vốn hầu hết là Python.

> **So với v0.5:** bỏ hẳn hạng mục *"patch attribution cho Wiki.js git module"* — **rủi ro Cao đó không còn tồn tại** vì đã bỏ Wiki.js.

## 16. Lộ trình

**Vì sao mục này tồn tại:** biến §5-§15 thành **thứ tự thi công có phụ thuộc**. Nguyên tắc sắp xếp: **việc rẻ nhất nhưng quyết định nhiều nhất làm trước** (#0 git platform, #2 cho lead xem UI) — vì làm sai thứ tự thì phải đập đi làm lại.

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
| **Token-bucket / process-state heartbeat cho lock** *(review đề xuất)* | **Over-engineering.** Rủi ro có thật (agent nghẽn GPU → heartbeat trễ → TTL hết → nhả khóa sớm → vỡ L2), nhưng **thiết kế đã có lớp 2**: branch protection (§7.1). Khóa nhả sớm → hai agent cùng mở PR trên một file → **git báo conflict → người review thấy**. Cái mất là **công của agent + một PR rác**, **không phải hỏng dữ liệu**. **TTL rộng rãi + heartbeat dày** là đủ. |
| **`STALE_CONTEXT` trong `lock-svc`** *(review đề xuất)* | **Sai chỗ về kiến trúc.** `lock-svc` **không nằm trên đường query** — agent query qua MCP → RAG, và **mỗi lời gọi MCP đã trả về trạng thái index hiện tại**. Không có staleness để sửa ở đó. Phiên bản *có thật* của lo ngại này là **branch của agent bị cũ khi PR khác merge** — đó là **git merge conflict thông thường**, git đã xử lý sẵn ở PR. |
| **Enrich-Before-Create bắt buộc ở v1** *(review đề xuất)* | **Nice-to-have, không phải hardening.** "Junk drawer" (agent tạo file mới vô tội vạ) **đã bị PR review chặn** (§7.1) — người review thấy *"agent muốn tạo 47 file mới"* thì từ chối. Quy tắc này **giảm tải review**, có giá trị → **cân nhắc ở v2**, không bắt buộc v1. |

---

## 18. Rủi ro & Giả định

**Vì sao mục này tồn tại:** ghi lại **những gì có thể sai và dấu hiệu nhận biết** — để khi nó xảy ra, team **nhận ra ngay** thay vì debug mò. Ba rủi ro mức **Cao** đều là **lỗi im lặng**: hệ thống vẫn "chạy", chỉ là kết quả vô nghĩa (index rác, agent không gọi tool, context tự mâu thuẫn).

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| **v1 không phân quyền** → index không ranh giới | **Cao** | **v1 chỉ index `security-kb`** (§13.1) — trùng wave 1 + budget GPU, không mất gì thêm |
| **Chưa sizing được hạ tầng** cho tới khi chạy `corpus_survey.py` — "5-10GB" không cho biết khối lượng index | Trung bình | **Chạy `corpus_survey.py` ngay khi có quyền truy cập data** (§8.2) → provision bằng số đo. Không phải rủi ro feasibility, là rủi ro **kế hoạch** |
| **Team lead vẫn thấy Gitea UI *[lead's feedback]*/xấu** | Trung bình | **Roadmap #2** — cho xem trên staging, thao tác 3 bước (mở → Edit → Save). **Tiêu chí:** sửa xong một trang mà **không mở terminal**. Không đạt → static site (§17) là fallback |
| **Presence phụ thuộc kỷ luật** (quên claim) — với **binary** thì lock là lớp bảo vệ **DUY NHẤT** (§7.4) | Trung bình | Team quen terminal → claim command chi phí thấp (§7.3). **Điều kiện làm VS Code extension (v2):** có **≥2 sự cố** ghi đè binary do quên claim. Format policy (§12) giữ binary ở vùng read-mostly để giảm tần suất |
| **Quên `git lfs pull`** → index toàn LFS pointer | Trung bình (**lỗi im lặng**) | Health check: assert index có nội dung thật |
| **Đặt Git LFS threshold muộn** → phải rewrite history | Trung bình | `.gitattributes` **trước** binary đầu tiên |
| **RAG-Anything MCP maturity** (community server, coverage chưa kiểm chứng) | Trung bình | **Chạy 5 tiêu chí PASS/FAIL ở §9.2 tại roadmap #5** — trước khi viết code. Trượt ≥1 → tụt xuống ứng viên #2, hết → build item #8 (wrapper, ~150-250 dòng) |
| **Công ty có thể ĐÃ CÓ git platform** (GitLab/GitHub EE) → dựng thêm Gitea là thừa & vướng policy | Trung bình | **Roadmap #0 — việc đầu tiên.** Kiểm 4 thứ: PR · branch protection · webhook · permission per-repo. Đủ 4 → dùng cái sẵn có. Thiếu ≥1 → dựng Gitea |
| **Mở rộng quá 5-10GB** (team lead: *[lead's feedback]*) | Thấp (v1) | **Ngưỡng chuyển backend:** khi index `security-kb` > **~5M chunk** hoặc p95 query > **2s**. Khi đó đổi LightRAG backend sang Postgres/Milvus/Neo4j (§8.1) — **không đổi kiến trúc**. **Không làm scale engineering ở v1** |
| **Agent bỏ qua MCP tool** vì không biết KB có gì (*MCP Client Amnesia*) | **Cao** | **§9.1 Seed Memory** — `sync-job` sinh `index.md`, `rag-mcp` inject vào `instructions`. **Tiêu chí Done:** IDE mới hỏi câu thuộc `security-kb` **không nhắc tên tool** → agent tự gọi. Không đạt → R3 thất bại trên thực tế dù RAG chạy đúng |
| **Agent APPEND thay vì THAY THẾ** → file tự mâu thuẫn → RAG trả context trái nhau → ảo giác | **Cao** | **§7.5 quy tắc ghi.** Lỗi ở **file**, không phải index — re-index không cứu được. PR review kiểm: diff phải có dòng `-` khi sửa sự thật cũ |
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

*Hết bản v0.9 — các quyết định cần chốt, kèm **tiêu chí** và **ai chốt**:*

| # | Quyết định | Tiêu chí để chốt | Ai chốt | Khi nào |
|---|---|---|---|---|
| 1 | **Dùng git platform sẵn có hay dựng Gitea?** | Platform sẵn có đủ 4 thứ: PR · branch protection · webhook · permission per-repo → dùng nó. Thiếu ≥1 → dựng Gitea (§6) | Team lead + IT hạ tầng | **Roadmap #0 — trước mọi thứ** |
| 2 | **Gitea/GitLab web UI có "đủ đẹp" không?** | Sửa xong một trang **mà không mở terminal** (§5.2). Không đạt → static site là fallback (§17) | Team lead | Roadmap #2 |
| 3 | **Chọn MCP server nào?** | Chạy **5 tiêu chí PASS/FAIL** ở §9.2. #1 đạt cả 5 → dùng, **xoá build item #8** | Laz | Roadmap #5 |
| 4 | **v1 chỉ index `security-kb`?** | Khuyến nghị **có** — một quyết định, ba lợi ích (§13.1) | Team lead | Roadmap #4 |
| 5 | **Số concurrency để sizing chiều Query** | Bao nhiêu kỹ sư + agent đồng thời lúc đỉnh? Autonomous hay chat-assist? (§8.2) | Team lead | Roadmap #9 |
| 6 | **Ai là agent sẽ ghi vào KB?** | Quyết định §7.5 (quy tắc ghi) phải enforce **trong code của mình** hay **thoả thuận với team khác** | Team lead | Trước khi code #1-#3 |

> **Đã chốt, không cần bàn lại:** `lock-svc` = **Python** (§15) · RBAC = **Phase 2** (§13) · store = **git tập trung** (§6) · *"decentralization"* = **RBAC**, không phải P2P (§13.2) · 3 đề xuất từ review bị **từ chối** kèm lý do (§17).
