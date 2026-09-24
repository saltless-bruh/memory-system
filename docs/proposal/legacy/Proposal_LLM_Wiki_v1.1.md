# Đề xuất công nghệ: LLM-Wiki cho team Security

| | |
|---|---|
| **Version** | 1.1 — đề xuất công nghệ |
| **Tác giả** | saltless-bruh |
| **Ngày** | 16/07/2026 |
| **Mục đích** | **Đề xuất một hướng công nghệ để anh quyết**, không phải bản thiết kế chi tiết. Chi tiết triển khai sẽ chốt sau khi hướng được duyệt. |
| **Cần gì ở anh** | Một câu trả lời: **hướng này có đáng làm không?** (§8) |

---

## 1. Vấn đề

Team đang có ~5-10GB tài liệu: report pentest, advisory, evidence, code, sheet. Ai cần gì thì tự đi tìm. AI/Agent của mỗi người cũng vậy.

Nếu chỉ dựng **RAG thuần**, ta được một cỗ máy tìm kiếm ngữ nghĩa. Nhưng nó có một điểm yếu căn bản:

> **RAG tái khám phá tri thức từ đầu ở MỖI câu hỏi.** Không có tích luỹ.

Hỏi một câu cần tổng hợp từ 5 tài liệu → RAG phải tìm và ghép lại 5 mảnh đó **mỗi lần hỏi**. Lần sau hỏi lại → làm lại từ đầu. Không có gì được xây lên. Tri thức của team không dày lên theo thời gian, nó chỉ nằm đó chờ được tìm lại.

Và với dữ liệu security, còn hai vấn đề nữa:

- **Không có ngữ cảnh team.** RAG trả về "CVE-2026-1234 là gì" từ advisory, nhưng không biết *"team mình đã gặp cái này ở Acme, xử lý thế này, kết quả ra sao"*.
- **Không có nguồn gốc rõ ràng.** Chunk trả về là chunk. Muốn kiểm chứng phải tự lần ngược.

---

## 2. Đề xuất: LLM-Wiki

Thay vì để AI tìm lại từ đầu mỗi lần, ta **compile tri thức một lần** thành một wiki, rồi **giữ nó luôn mới**.

Ý tưởng gốc là **LLM Wiki pattern của Andrej Karpathy** (04/2026, 5.000+ sao trong vài ngày): LLM đọc nguồn, trích thông tin, viết thành trang wiki liên kết chéo, và tự bảo trì. Wiki trở thành **artifact bồi đắp** thay vì một đống note chết.

**Mô hình ba lớp — đúng như anh mô tả:**

```
   LLM   =  CPU       →  agent của từng người, làm việc chính
   Wiki  =  RAM       →  tri thức đã compile, người + AI cùng sửa
   RAG   =  STORAGE   →  raw/, nguồn gốc, "cái tủ hồ sơ" — agent đọc
```

**Wiki theo phòng ban. RAG dùng chung.**

Cái khác biệt so với RAG thuần: khi hỏi *"kerberoasting ở Acme thế nào"*, agent không đi lục 10GB. Nó mở trang wiki về kerberoasting — trang đó **đã có sẵn** tổng hợp, liên kết, và **địa chỉ trỏ ngược** về đúng trang 12-14 của report Acme. Cần chi tiết gốc thì mới xuống RAG lấy.

Cross-reference đã có sẵn. Mâu thuẫn đã được đánh dấu. Tổng hợp đã phản ánh mọi nguồn đã đọc. **Compile một lần, dùng nhiều lần.**

---

## 3. Điểm mấu chốt: **Scout** — model nhúng đi tìm giùm

Đây là phần đáng chú ý nhất của đề xuất, và là thứ chưa dự án nào làm.

**Vấn đề:** wiki lớn dần. Agent muốn biết có gì thì phải đọc index. Index 300 trang ≈ **13.000 token** — mỗi phiên, mỗi người. Đó là token đắt, nằm trong context của model chính.

**Giải pháp:** đặt một **model nhỏ chạy local ngay cạnh wiki** làm nhiệm vụ tìm kiếm. Agent chính không tự đi tìm — nó **hỏi Scout**.

```
   [Agent chính]  ──"kerberoasting ở Acme?"──►  [SCOUT]
                                                    │ đọc index (13K token — của Scout)
                                                    │ chọn đúng trang
                                                    │ lần theo address → raw/
                                                    │ (nếu cần) gọi RAG có path filter
   [Agent chính]  ◄──payload cô đặc + trích dẫn──┘
```

**Giống như giao việc tra cứu cho một model rẻ thay vì tự làm.** Chi phí tìm kiếm chuyển từ **context đắt** sang **GPU local rẻ**.

**Đã dựng prototype và đo được:**

| | Token |
|---|---|
| Scout tốn để định tuyến | **578** — chạy trên model local |
| Agent chính thật sự nhận | **401** — chỉ payload + trích dẫn |

Chi phí index **không bao giờ** chạm context của agent chính. Với 300 trang, con số đó là 13.000 token/phiên/người được giữ ở phía Scout.

---

## 4. Vì sao Scout không phải "nice to have" — với team mình

Đây là số đo từ dự án `obsidian-second-brain` (vault ~2.350 note):

| Cách tìm | Recall |
|---|---|
| Keyword, câu hỏi trùng từ khoá | 1.0 |
| Keyword, câu hỏi diễn giải lại | thấp |
| **Keyword, câu hỏi KHÔNG phải tiếng Anh** | **≈ 0** |
| **Model multilingual (bge-m3)** | **63%** |

**Team mình hỏi bằng tiếng Việt.** Hỏi *"cách lấy mật khẩu tài khoản dịch vụ trong AD"* — không có từ nào trùng với trang "Kerberoasting". Keyword search trả về **gần như không gì**.

→ **Với tiếng Việt, model embedding không phải tối ưu hoá. Nó là điều kiện để hệ thống chạy được.** Đây là lập luận mạnh nhất cho Scout, và nó là số đo, không phải phỏng đoán.

---

## 5. Công nghệ đề xuất

**Không build từ đầu.** Sau khi khảo sát, có **ba dự án open-source MIT** đã làm phần lớn việc này:

| Dự án | Quy mô | Ta lấy gì |
|---|---|---|
| **obsidian-second-brain** | 3.2k★, v0.12, Python | **BASE** — vault người sửa được, chạy trên 6 CLI khác nhau, có sẵn semantic search (Ollama + bge-m3) + MCP + fallback keyword |
| **llm-wiki-compiler** (llmwiki) | 1.8k★, v1.0, TypeScript | **Lấy ý tưởng**: runtime gates (luật enforce ở code, không phải ở prompt), trích dẫn tới từng dòng |
| **MehmetGoekce/llm-wiki** | 115★, Shell | **Lấy ý tưởng**: prune/LRU-demote — trang nguội bị đẩy khỏi index để index luôn nhỏ |

**Vì sao chọn `obsidian-second-brain` làm base:**

- **Người sửa được.** Anh yêu cầu *[lead's feedback]*. Đây là vault người sở hữu, AI phụ bảo trì. (llmwiki ngược lại — nó *compile* wiki từ nguồn, người không sửa tự do.)
- **Đã có ~70% của Scout**: semantic search local, index tăng dần, MCP connector, và *"model không với được thì tự tụt về keyword, không bao giờ treo"*.
- **Chạy được với model local qua endpoint OpenAI-compatible** → cắm LiteLLM → **dữ liệu không rời hạ tầng**.
- **Đa CLI** — mỗi người dùng agent mình quen (Claude Code, Cursor, Codex, Gemini CLI…). Đúng yêu cầu *[lead's feedback]*.
- Python — cùng ngôn ngữ với phần ta tự viết.

**Phần còn lại của stack:**

| Lớp | Công nghệ | Vì sao |
|---|---|---|
| Storage/RAG | **RAG-Anything** | Đọc được PDF/ảnh/bảng — đúng mix dữ liệu của team |
| Model gateway | **LiteLLM** | Một chỗ duy nhất để chứng minh dữ liệu không ra cloud |
| Lưu trữ | **Git** (GitLab nếu công ty đã có) | Lịch sử: ai sửa, sửa gì, lúc nào, agent hay người |
| Scout | **bge-m3** qua LiteLLM | Multilingual — lý do ở §4 |

---

## 6. Ta tự viết cái gì

Ngắn — và đó là điểm mạnh của hướng này:

| # | Phần | Ghi chú |
|---|---|---|
| 1 | **Scout** — model nhúng tìm giùm agent | Phần lớn là **nối** search có sẵn của base + thêm cầu nối xuống RAG. Prototype đã chạy. |
| 2 | **Lock** — hai agent không ghi cùng file | **Khoảng trống cả ba dự án đều bỏ ngỏ.** MehmetGoekce nói thẳng: *"nhiều phiên Claude ghi cùng lúc sẽ conflict — coi file wiki là tài nguyên dùng chung."* Không ai giải. |
| 3 | **`AGENTS.md`** — luật vận hành cho agent | Văn bản, không phải code. Nhưng là thứ quyết định wiki có kỷ luật hay thành đống markdown. |

Còn lại là **cấu hình tool có sẵn**.

---

## 7. Những gì chưa chắc — nói thẳng

Đây là đề xuất hướng đi, chưa phải cam kết kỹ thuật. Các điểm sau **chưa verify**, và cần thử trước khi tin:

- **Recall tiếng Việt thật.** Số 63% là của dự án khác, vault khác, ngôn ngữ khác. Phải tự đo trên dữ liệu team.
- **`obsidian-second-brain` vốn thiết kế cho cá nhân.** Anh muốn **lưu tập trung server**, nhiều người dùng chung. Phải thử xem nó chịu được không, hay phải sửa.
- **Một Scout dùng chung cho nhiều phòng ban** → cần scope, nếu không agent phòng A hỏi một câu có thể nhận nội dung phòng B. Prototype đã có sẵn chỗ cắm cho việc này, nhưng chưa bật.
- **Prompt injection.** Dữ liệu `raw/` chứa payload do kẻ tấn công kiểm soát (report pentest, phishing sample). Nguyên tắc: Scout **chỉ trích dẫn, không diễn giải rồi ra lệnh**. Cần test thật.
- **Chi phí index lần đầu.** Chưa có quyền truy cập data nên chưa đo được. Đã viết sẵn script để đo ngay khi có.

---

## 8. Cần anh quyết

**Câu hỏi chính:**

> **Hướng này có đáng làm không?** — LLM-Wiki (compile tri thức, tích luỹ) + Scout (model nhúng tìm giùm) + RAG (kho nguồn), dựng trên `obsidian-second-brain` thay vì build từ đầu.

**Nếu OK, ba việc tiếp theo:**

1. **Xin quyền truy cập data** → đo chi phí index thật, và đo recall tiếng Việt thật.
2. **Dựng thử `obsidian-second-brain` + Scout + LiteLLM** trên một server → xem nó có chịu được nhiều người không.
3. **Demo cho anh xem**: mở IDE, hỏi một câu tiếng Việt, agent tự tìm ra đúng trang và trích dẫn được nguồn gốc.

**Câu hỏi phụ cần anh trả lời:**

- Công ty **đã chạy GitLab chưa**? Nếu rồi thì dùng luôn, khỏi dựng thêm.
- Phòng ban nào **thử trước**? (quyết định wiki đầu tiên viết về cái gì)
- Lúc cao điểm có **bao nhiêu người + agent** hỏi cùng lúc? (quyết định cấu hình server cho Scout)

---

*Đề xuất này chỉ nêu **hướng công nghệ**. Kiến trúc chi tiết, cấu hình, và kế hoạch triển khai sẽ làm sau khi anh duyệt hướng.*
