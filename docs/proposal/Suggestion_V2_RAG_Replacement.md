# Đề xuất V2 — Thay RAG engine (RAG-Anything → backend có RBAC)

> **IMPLEMENTED/SUPERSEDED PROPOSAL.** PostgreSQL pgvector with fail-closed RLS
> is now the current backend. Preserve this file as decision history only; see
> `docs/ARCHITECTURE_STATUS.md`.

| | |
|---|---|
| **Loại** | Đề xuất kỹ thuật cho **V2** (KHÔNG đổi V1) |
| **Sinh ra từ** | Phiên validation trước khi vào Phase 2 (21/07/2026) |
| **Nguồn** | `Proposal_SNP_Memory_System_v2.md` §5 · `Scout_Blueprint.md` · khảo sát OSS trực tiếp 07/2026 |
| **Quyết định chốt** | (1) KHÔNG fork RAG-Anything cho privilege lock. (2) V1 giữ RAG-Anything nhưng làm **RAG engine thành khe cắm thay được** qua Scout. (3) V2 chung kết **2 lựa chọn**: **R2R** (turnkey, MIT) hoặc **MinerU + pgvector + Postgres RLS** (DB-enforced). |

> **Ranh giới:** V1 **không đổi** — vẫn RAG-Anything, single-team data, chứng minh cơ chế lõi. Doc này chỉ (a) chốt hướng V2 và (b) liệt kê những *hook* phải cài sẵn trong V1 để V2 không thành rewrite.

---

## 0. TL;DR

- **Tham vọng đã lớn hơn v2.2:** không chỉ team Security — nếu chạy tốt sẽ mở cho **cả phòng IT**; và V2 cần **privilege lock theo role**: RAG giữ **mọi** data, nhưng mỗi member (theo vai trò) **chỉ truy được phần role cho phép**.
- **Vấn đề cốt lõi:** privilege lock ở mức row/attribute **xung khắc với knowledge-graph gộp** của RAG-Anything (LightRAG merge mọi doc vào MỘT KG — mất provenance theo role).
- **Nhận ra quan trọng:** **Wiki đã là knowledge-graph** (curated, `[[wikilink]]`). Việc của RAG chỉ là *"đưa address → trả đoạn gốc + citation"* (`only_need_context=True`). ⟹ KG của RAG **giá trị biên thấp** trong kiến trúc này → bỏ KG để lấy RBAC là đánh đổi rẻ.
- **Điều kiện tiên quyết (làm ở V1):** Scout gọi RAG qua một **interface backend-agnostic** — giống wiki-engine đã là khe cắm thay được. Đổi RAG engine ⟹ **không đụng agent, không đụng wiki, không đụng frontmatter**.
- **Chung kết V2 (đều nền Postgres/pgvector):** **R2R** (adopt permission model có sẵn) hoặc **MinerU + pgvector + RLS** (tự enforce ở tầng DB — mạnh nhất, auditable). **KHÔNG fork RAG-Anything.**

---

## 1. Bối cảnh mới (khác v2.2 ở đâu)

v2.2 mặc định "team Security, RAG dùng chung, RBAC để V2". Hai điều làm rõ thêm đổi trọng số quyết định:

1. **Quy mô có thể lên cả phòng IT.** Không còn là một team — nhiều team/role, nhu cầu *need-to-know* thật (redteam ≠ blueteam ≠ appsec ≠ helpdesk).
2. **V2 yêu cầu privilege lock theo role.** Mô hình mong muốn: **một RAG giữ tất cả data + một lock lọc theo role của người gọi**. Đây là **access control ở mức row/attribute *bên trong* một store dùng chung**, KHÔNG phải "mỗi team một kho tách biệt".

Chính hai điều này biến lựa chọn RAG engine từ "chi tiết V2" thành "quyết định phải định hình từ bây giờ".

---

## 2. Vấn đề cốt lõi: RBAC xung khắc với merged KG

RAG-Anything/LightRAG **gộp mọi document vào MỘT KG + MỘT VDB** — và **merge entity xuyên tài liệu** (đó là *tính năng lõi*, xem proposal §2.3). Hệ quả: một khi entity của doc A (role-1) và doc B (role-2) đã **hoà vào cùng một node**, không còn trả lời sạch được câu "role này có được thấy cái này không?" — tri thức đã bị **fuse mất provenance**.

- **Phía VDB (chunks):** còn lọc được bằng metadata (gắn tag role vào chunk, lọc trước khi retrieve).
- **Phía KG (entities/edges):** **không** — muốn RBAC trên KG phải gắn role vào từng entity/edge **và** lọc traversal theo đó; LightRAG không làm.

⟹ **"một RAG giữ tất cả + lock theo role"** là **dễ** cho vector store thuần (metadata filter), **khó-tới-mâu-thuẫn** cho merged-KG RAG. Đây là lý do #1 để **không** đặt cược RBAC vào RAG-Anything.

---

## 3. Nhận ra: Wiki đã là knowledge-graph của ta

Trong kiến trúc SNP, KG của RAG **trùng vai** với thứ ta đã có:

- **LLM-Wiki** = tri thức đã compile, quan hệ do người/agent **curate** qua `[[wikilink]]` → đây *chính là* knowledge-graph, và nó tốt hơn KG auto-extract vì có con người kiểm.
- Việc thật sự của RAG = **`rag_fetch(address) → đoạn gốc + citation`** với `only_need_context=True`. Đó là **vector retrieval + filter**, KHÔNG phải graph reasoning.

⟹ KG auto của RAG-Anything **đóng góp biên thấp** trong kiến trúc này. Bỏ nó (để đổi lấy RBAC + đơn giản) **mất rất ít**. Đây là lý do #2 khiến hướng "vector store có RBAC" hấp dẫn hơn là cố giữ KG.

---

## 4. Điều kiện tiên quyết (LÀM Ở V1): Scout interface sạch → RAG swappable

Đây là phần **phải cài vào V1** để V2 chỉ là "đổi backend", không phải viết lại.

**Nguyên tắc:** mirror khe-cắm wiki-engine sang phía RAG. Scout **không** được phụ thuộc API riêng của RAG-Anything (`rag.query(QueryParam(mode="mix", only_need_context=True))`). Thay vào đó Scout gọi một **RAG-backend interface** tối thiểu:

```
# HỢP ĐỒNG RAG-BACKEND (Scout phụ thuộc cái này, KHÔNG phụ thuộc RAG-Anything)
rag_retrieve(hint, *, path=None, scope=None, k=…) 
    -> [ { text, file_path, loc, score, meta } ]

#  · path   : địa chỉ để giữ đúng nguồn (post-filter HOẶC pre-filter, tuỳ backend)
#  · scope  : {caller_roles / clearance / team}  ← HOOK RBAC (V1 bỏ qua, V2 enforce)
#  · meta   : mang team/role/classification của chunk (để cite + để lọc)
```

Mỗi engine là một **adapter** sau interface này:

| Backend | `path` xử lý thế nào | `scope` (RBAC) |
|---|---|---|
| **RAG-Anything (V1)** | **post-filter** `file_path` (vì không có `path_filter`) | bỏ qua (V1 chưa enforce) |
| **R2R (V2)** | collection/document filter (pre-filter) | permission theo user/collection |
| **pgvector+RLS (V2)** | `WHERE source_path=…` (pre-filter) | **Row-Level Security** theo role — DB enforce |

**Ba hệ quả cho V1 (chi phí gần 0, giá trị lớn):**
1. **Tách `rag_backend` khỏi `scout` core.** `scout/backends/rag_anything.py` hiện thực interface; core Scout chỉ gọi `rag_retrieve(...)`. Đổi backend = thêm file adapter mới.
2. **Thêm tham số `scope` vào chữ ký ngay từ V1** (V1 truyền `None`/bỏ qua). Không thêm sau ⟹ không phải sửa call-site khắp nơi ở V2.
3. **`meta` mang sẵn `team`/`department`.** `department` đã có trong frontmatter — cho nó chảy qua Scout vào `meta` ngay, để V2 chỉ việc *enforce* thứ đã *có mặt*.

> Đây đúng là điều "làm cho Scout interface sạch để RAG engine thay được" — và nó là **hook rẻ nhất, quan trọng nhất** của cả đề xuất này.

---

## 5. Khảo sát 4 lựa chọn (live research, 07/2026)

Không có tool OSS turnkey nào cho **đủ cả** "tool-belt multimodal của RAG-Anything **+** RBAC fine-grained OSS trên store dùng chung". Mỗi cái đánh đổi một thứ:

| | License | Parse multimodal | Mô hình RBAC | Hợp "1 RAG chung + lock theo role"? |
|---|---|---|---|---|
| **R2R** (SciPhi) | **MIT** | 40+ định dạng (PDF/ảnh/audio); nông hơn MinerU/RAGFlow | **Collections + document-level permissions, trong core OSS** ("Supabase for RAG") | ✅ **Có, và open-source** |
| **Onyx** (Danswer) | MIT (core) | multimodal chat; hướng connector, không deep-doc | **RBAC + doc-level = CHỈ Enterprise (trả phí)**; OSS chỉ auth + SSO | ⚠️ Chỉ khi mua Enterprise |
| **RAGFlow** (InfiniFlow) | **Apache-2.0** | **Mạnh nhất** (DeepDoc: bảng/layout/template) | **Chỉ isolation theo workspace/tenant** (owner/normal), KHÔNG per-member trong 1 KB chung | ❌ Hợp "mỗi team 1 KB", không hợp "1 RAG chung + lock" |
| **pgvector + Postgres RLS** | PostgreSQL (OSS) | Parser tuỳ chọn — **giữ được MinerU** | **Row-Level Security: DB enforce, theo role, auditable** — mạnh nhất | ✅ Có, enforce mạnh nhất (tự dựng pipeline) |

**Bị loại cho đúng nhu cầu của ta:**
- **Onyx** — mô hình permission đẹp nhất nhưng **tính năng ta cần bị khoá sau Enterprise (trả phí)**. Bản OSS self-host **không** có doc-level access control. Chỉ hợp nếu chấp nhận mua EE.
- **RAGFlow** — parser tốt nhất, Apache-2.0 thật, nhưng access control là **isolation theo workspace**, không phải "1 RAG chung + role lock". Hợp topology *mỗi team một KB tách biệt*, không hợp mô hình V2 ta mô tả.

---

## 6. Insight quyết định + khuyến nghị

Cả hai lựa chọn còn lại **đều nền Postgres + pgvector**: R2R chạy trên Postgres/pgvector; đường "decouple" *chính là* pgvector. ⟹ **substrate RBAC của V2 gần như chắc chắn là Postgres + pgvector** — chỉ khác nhau **lock nằm ở đâu**:

- **R2R** = dùng permission/collection ở **tầng application** (turnkey, ít code, MIT).
- **pgvector + RLS** = enforce ở **tầng database** bằng Row-Level Security (mạnh nhất, auditable, tự làm pipeline — và **giữ được MinerU** làm parser, không mất engine multimodal của RAG-Anything).

**Khuyến nghị:**
1. **KHÔNG fork RAG-Anything cho privilege lock.** Giờ rõ là đường *yếu nhất*: vẫn phải tự viết RBAC (công sức ngang pgvector+RLS) nhưng gắn lên codebase research mà **merged-KG chống lại row-level access**. Công cao, gánh nặng bảo trì bảo mật cao, hợp kém.
2. **V1 (bây giờ):** giữ RAG-Anything để chạy demo — nhưng **theo interface §4** để RAG engine thay được.
3. **V2 chung kết 2 (đều Postgres/pgvector):**
   - **R2R** nếu muốn permission turnkey và chấp nhận parse nông hơn.
   - **MinerU + pgvector + RLS** nếu muốn RBAC DB-enforced, auditable và giữ parse hạng nhất — **lean cho một phòng bảo mật**, vì đảm bảo truy cập do **database** ép, không phải code app.
4. **Cần verify tay trước khi chốt R2R:** xác nhận API collection/permission **nằm trọn trong bản self-host MIT**, không bị khoá cloud ngầm — trang docs khảo sát chưa kết luận rõ điểm này, và đây là mấu chốt độ-hợp của R2R.

---

## 7. Hook phải cài trong V1 để V2 không thành rewrite

| Hook | Làm gì ở V1 | Trả cho V2 |
|---|---|---|
| **RAG-backend interface** (§4) | Tách adapter; Scout chỉ gọi `rag_retrieve(...)` | Đổi RAG engine = thêm 1 adapter |
| **Tham số `scope`** trong chữ ký | Có mặt, truyền `None` | Chỗ cắm enforce RBAC — không phải sửa call-site |
| **`meta.team/department`** chảy qua Scout | Cho `department` (đã có) chảy vào kết quả | Pre-filter/enforce theo team-role sẵn dữ liệu |
| **`only_need_context` = luật, không phải API** | Ghi là "lấy đoạn gốc, không để RAG tự sinh" ở tầng interface | Backend nào cũng phải tôn trọng |
| **Không đặt cược vào KG** (§3) | Không xây tính năng V1 nào phụ thuộc KG của RAG | Bỏ KG ở V2 không mất gì |

> Ghi nhớ: hook đắt nhất mà rẻ nhất là **chữ ký `rag_retrieve(hint, *, path, scope, meta)`**. Định hình đúng nó ở V1 = V2 chỉ đổi backend.

---

## 8. Nguồn (khảo sát 07/2026)

- R2R — <https://github.com/SciPhi-AI/R2R> · <https://www.sciphi.ai/>
- Onyx access controls (EE gating) — <https://docs.onyx.app/security/architecture/access_controls>
- RAGFlow user/tenant management — <https://deepwiki.com/infiniflow/ragflow/7.3-user-and-tenant-management>
- RAGFlow releases — <https://ragflow.io/docs/v0.20.5/release_notes>
