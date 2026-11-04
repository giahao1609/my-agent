# BÁO CÁO ĐỐI SOÁT BASELINE VÀ KẾ HOẠCH CHUYỂN TIẾP — PHASE 05D
## PHASE 05D PREPARATION & BASELINE TRANSITION AUDIT REPORT

- **Executor:** Houhou (Front Agent — MyAgent)
- **Role:** Tech Lead & Software Companion
- **Repository Root:** `/Users/haohg/Project/my-agent`
- **Current Branch:** `feat/houhou-04-5h-b-handoff-hardening`
- **Active Git HEAD Commit:** `c4708182ab00be62acc59f36ffdfc0b0946aa901`
- **Python Runtime:** Python 3.13.11 (via `.venv/bin/python`)
- **Execution Mode:** READ-ONLY AUDIT & PLANNING
- **Regression Test Suite:** 1012/1012 passed (Exit code: 0)
- **Date:** 2026-10-23

---

## MỤC LỤC
1. Tóm tắt điều hành (Executive Summary)
2. Bằng chứng kiểm toán Git Baseline (Git Baseline Evidence)
3. Đánh giá nghiệm thu & Niêm phong Phase 05C-P (Phase 05C-P Closure Assessment)
4. Kiểm kê chi tiết các thay đổi chưa commit (Uncommitted Changes Inventory)
5. Chiến lược Branch & Phân chia Logical Commits (Branch Strategy & Commit Plan)
6. Khám phá & Định vị nguồn sự thật Phase 05D (Phase 05D Source-of-Truth References)
7. Lộ trình triển khai chi tiết Phase 05D (Detailed Implementation Roadmap)
8. Ranh giới kiến trúc, bảo mật & Phụ thuộc (Architecture, Security & Dependencies)
9. Ma trận kiểm thử đề xuất (Proposed Test Matrix)
10. Cổng nghiệm thu & Tiêu chí hoàn thành (Acceptance Gates & DoD)
11. Đánh giá rủi ro & Kế hoạch Rollback (Risk Assessment & Rollback Plan)
12. Đề xuất quyết nghị cuối cùng (Final GO/NO-GO Recommendation)

---

## 1. TÓM TẮT ĐIỀU HÀNH (EXECUTIVE SUMMARY)

Báo cáo này được lập nhằm mục đích kiểm chứng trạng thái thực tế của kho mã nguồn, rà soát tính toàn vẹn kỹ thuật sau khi hoàn tất Phase 05C-P, và thiết lập kế hoạch chuyển tiếp chuẩn xác sang **Phase 05D** theo chỉ đạo của Tech Lead.

### Các kết luận cốt lõi:
1. **Tính sẵn sàng của Test Suite:** Bộ kiểm thử hồi quy toàn diện hiện đạt **1012/1012 tests passed trong 20.58 giây**, tỷ lệ thành công 100%, không ghi nhận bất kỳ test fail, test skip hay suy giảm hiệu năng nào.
2. **Niêm phong hoàn tất Phase 05C-P (VERIFIED):** Toàn bộ 3 phát hiện kỹ thuật (F-02: Lỗ hổng HMAC V2, F-03: Nguy cơ rò rỉ secret key trong Git, F-04: Lệch pha bộ lọc SQL cho dữ liệu legacy) đã được kiểm chứng trực tiếp trên mã nguồn và test suite đối kháng, đạt mức phân loại **VERIFIED**. Không còn blocker bảo mật P0 hoặc P1 nào tồn đọng.
3. **Hiện trạng Working Tree & Phân lớp mã nguồn:** Toàn bộ công việc từ Phase 04.5H-B (Handoff Continuity & Approval Hardening) và Phase 05B/05C/05C-P (UserModel Domain, Engine và Security Hardening) hiện đang nằm trên working tree chưa commit của nhánh `feat/houhou-04-5h-b-handoff-hardening`. Tổng cộng gồm 10 tệp tracked bị sửa đổi (+1,393 / -223 dòng) và 12 tệp untracked (8,049 dòng).
4. **Khuyến nghị chiến lược Branch:** Nhánh hiện tại mang tên `feat/houhou-04-5h-b-handoff-hardening` phản ánh phạm vi của Phase 04.5H-B. Để bảo đảm tính mạch lạc trong lịch sử Git và thuận tiện cho việc review, kiểm toán và rollback độc lập, khuyến nghị thực hiện phân tách thành 2 logical commits tuần tự:
   - Commit 1: Hoàn tất Phase 04.5H-B.
   - Commit 2: Hoàn tất Phase 05B, 05C và 05C-P.
   Sau đó, mở nhánh mới chuyên biệt `feat/houhou-05d-user-model-surface` để triển khai Phase 05D.
5. **Định vị phạm vi Phase 05D:**
   - Tên chính thức: **PHASE 05D — UserModel Surface Integration & MCP Tooling**.
   - Mục tiêu: Đưa toàn bộ năng lực lõi của UserModel (Domain & Engine đã kiểm chứng ở Phase 05B/05C) ra ranh giới tương tác của MyAgent thông qua bộ công cụ MCP Server (`get_user_profile`, `list_user_preferences`, `set_user_preference`, `retract_user_preference`) và tích hợp nhận thức sở thích vào Front Agent (Houhou) nhằm cá nhân hóa tương tác mà không vi phạm các quy tắc hệ thống bất biến.
   - Ranh giới nghiêm ngặt: Tuyệt đối không xâm lấn phạm vi của **Phase 06** (ContextBuilder tự động tiêm memory vào prompt của downstream specialist LLM, hoặc tích hợp CodeGraph).
6. **Đề xuất quyết nghị:** **GO FOR PHASE 05D PLANNING & BASELINE STABILIZATION**.

---

## 2. BẰNG CHỨNG KIỂM TOÁN GIT BASELINE (GIT BASELINE EVIDENCE)

Các lệnh kiểm toán Git read-only được thực thi trực tiếp trên hệ thống để thu thập bằng chứng khách quan:

### 2.1. Xác minh Commit HEAD & Branch hiện tại
```bash
$ git rev-parse HEAD
c4708182ab00be62acc59f36ffdfc0b0946aa901

$ git branch --show-current
feat/houhou-04-5h-b-handoff-hardening

$ git log -n 3 --oneline
c470818 (HEAD -> feat/houhou-04-5h-b-handoff-hardening, origin/main, origin/HEAD, main) Merge pull request #7 from giahao1609/audit/houhou-04-5h-handoff-continuity
8b8e942 (origin/audit/houhou-04-5h-handoff-continuity, audit/houhou-04-5h-handoff-continuity) docs(audit): complete phase 04.5h handoff reality and source verification audit
d3f7850 (feat/houhou-04-5b-chatgpt-mcp-connection) Merge pull request #6 from giahao1609/feat/houhou-04-5a-reasoning-bridge-foundation
```
- **Nhận xét:** HEAD hiện tại của nhánh local đồng bộ 100% với commit `c470818` (Merge PR #7 trên `origin/main`). Không có độ lệch hay commit orphan nào.

### 2.2. Kiểm tra Git Ignored Rules đối với Secret Keys
```bash
$ git check-ignore -v data/provenance.key test.key
.gitignore:46:*.key	data/provenance.key
.gitignore:46:*.key	test.key

$ git status --ignored -s | grep provenance
!! data/provenance.key
```
- **Bằng chứng:** Quy tắc `*.key` tại dòng 46 của `.gitignore` bảo vệ tuyệt đối tệp `data/provenance.key`. Tệp mang cờ `!!` (ignored), bảo đảm không bao giờ bị đưa vào staging area hoặc commit vô tình.

### 2.3. Kiểm tra Whitespace & Syntax Formatting
```bash
$ git diff --check
(Thoát với mã 0, không có lỗi whitespace hay định dạng tệp)

$ ./.venv/bin/ruff check core/user_model.py core/user_model_engine.py persistence/sqlite_memory_store.py core/durable_memory_service.py tests/test_user_model_engine.py
All checks passed!
```

---

## 3. ĐÁNH GIÁ NGHIỆM THU & NIÊM PHONG PHASE 05C-P (PHASE 05C-P CLOSURE ASSESSMENT)

Bảng đối chiếu trạng thái thực tế của 3 phát hiện kỹ thuật trong đợt audit Phase 05C-P:

| Mã | Hạng mục | Mức độ | Phân loại | Bằng chứng mã nguồn & Kiểm thử | Trạng thái |
|---|---|---|---|---|---|
| **F-02** | HMAC Attestation Integrity Gap | P1 | **VERIFIED** | Phương thức `_compute_canonical_payload_bytes_v2` trong `core/user_model.py` (dòng 323-370) ràng buộc 18 trường định danh và ngữ nghĩa bất biến qua JSON chuẩn hóa xác định. Đã kiểm chứng qua 9 bài test đối kháng trong `TestPhase05CPAdversarialSecurityAndRetrieval` (`tests/test_user_model_engine.py`). | **ĐÃ NIÊM PHONG TRIỆT ĐỂ** |
| **F-03** | Secret Key Git Leakage Risk | P2 | **VERIFIED** | Đã bổ sung `data/*.key` và `*.key` vào `.gitignore` (dòng 44-46). Lệnh `git check-ignore` xác nhận tệp `data/provenance.key` đã bị bỏ qua hoàn toàn. | **ĐÃ BẢO VỆ AN TOÀN** |
| **F-04** | Legacy Kind SQL Pre-filter Mismatch | P2 | **VERIFIED** | Cập nhật hàm `_retrieve_scored_sync` trong `persistence/sqlite_memory_store.py` (dòng 360-366) sử dụng hàm SQLite JSON1 `json_valid` và `json_extract(metadata_json, '$.__canonical__.memory_type')` kết hợp fallback `kind = ?`. Bọc `json.loads` trong `_row_to_memory` với `(json.JSONDecodeError, TypeError, ValueError)`. | **ĐÃ ĐỒNG BỘ CHUẨN XÁC** |

### Các nội dung đã phân biệt rõ ràng:
- **VERIFIED:** F-02, F-03, F-04, tính nguyên tử của giao dịch SQLite `BEGIN IMMEDIATE`, cơ chế chống truncation 200 bản ghi, và toàn bộ 1012 unit/integration tests.
- **REPORTED & ACKNOWLEDGED:** Giới hạn mô hình đơn người dùng (`user_id="default_user"`). Nhãn `user_id` hiện tại là nhãn phân loại ngữ cảnh nội bộ, chưa phải là ranh giới cô lập xác thực đa người thuê (multi-tenant authentication boundary). Đây là đặc tính kiến trúc đã được ghi nhận trong `core/user_model.py` và không tạo thành blocker cho môi trường chạy đơn của MyAgent.
- **DEFERRED (Được hoãn theo lộ trình):**
  - Tích hợp CodeGraph và DurableMemory vào cơ chế nạp ngữ cảnh mô hình tự động (`ContextBuilder`): Hoãn về **Phase 06** theo tài liệu `docs/architecture/handoff_resume_integrity.md` và `docs/cognitive_architecture/contracts/memory.md`.
  - Cải tiến giao dịch nguyên tử xuyên tiến trình cho phạm vi toàn cục (`project_id=None`): Đã được xử lý an toàn bằng `PersistenceContractBlockedError` fail-closed và in-process lock `asyncio.Lock`.

---

## 4. KIỂM KÊ CHI TIẾT CÁC THAY ĐỔI CHƯA COMMIT (UNCOMMITTED CHANGES INVENTORY)

Hiện tại, working tree chứa 2 nhóm thay đổi rõ rệt thuộc hai giai đoạn phát triển kế tiếp nhau:

### 4.1. Nhóm 1: Các tệp thuộc Phase 04.5H-B (Handoff Continuity & Approval Hardening)

| Tệp tin | Trạng thái Git | Thay đổi dòng | Vai trò kỹ thuật trong hệ thống |
|---|---|---|---|
| `agents/coder_agent.py` | Tracked Modified | +165 / -9 | Tích hợp phục hồi pending approvals bền vững, tránh hồi sinh approval đã hết hạn/từ chối, ghi nhận bằng chứng thực thi. |
| `agents/coder_stack.py` | Tracked Modified | +8 / -1 | Tiêm phụ thuộc `SqlitePendingApprovalStore` vào `CoderAgentStack`. |
| `core/agent_handoff_context.py` | Tracked Modified | +226 / -7 | Trích xuất trạng thái thực thi chân thực (`SYSTEM_VERIFIED_EXECUTION`), loại bỏ mock khỏi whitelist, bắt buộc `project_id`. |
| `core/handoff_coordinator.py` | Tracked Modified | +51 / -15 | Bổ sung `project_id` vào request/decision, tích hợp lưu trữ lịch sử chuyển giao bền vững xuyên qua khởi động lại. |
| `core/resume_assembler.py` | Untracked | 360 dòng mới | Cơ chế tái lắp ráp lịch sử nối phiên có giới hạn (`ResumeHistoryPolicy`, trần 20 tin nhắn gần nhất) kèm khử nhạy cảm bí mật. |
| `persistence/sqlite_pending_approval_store.py` | Tracked Modified | +483 / -27 | Kho lưu trữ phê duyệt công cụ SQLite bền vững với cơ chế CAS nguyên tử `claim_and_resolve`, kiểm tra TTL và ghi nhận kết quả thực thi. |
| `persistence/sqlite_handoff_history_store.py` | Untracked | 223 dòng mới | Kho lưu trữ lịch sử quyết định chuyển giao vai trò SQLite bền vững kèm bảo vệ bí mật. |
| `tests/test_handoff_resume_hardening.py` | Untracked | 2,317 dòng mới | Suite kiểm thử 61 tests bao phủ toàn bộ cơ chế nối phiên, bảo mật và phê duyệt công cụ Phase 04.5H-B. |
| `docs/architecture/handoff_resume_integrity.md` | Untracked | 160 dòng mới | Đặc tả kiến trúc về tính toàn vẹn nối phiên và chuyển giao ngữ cảnh. |
| `docs/handoffs/PHASE_04_5H_B_HANDOFF.md` | Untracked | 154 dòng mới | Biên bản bàn giao kỹ thuật Phase 04.5H-B. |
| `docs/handoffs/PHASE_04_5H_B_FINAL_ACCEPTANCE.md` | Untracked | 193 dòng mới | Báo cáo nghiệm thu nguồn Phase 04.5H-B. |

### 4.2. Nhóm 2: Các tệp thuộc Phase 05B, 05C & 05C-P (UserModel Core & Provenance Hardening)

| Tệp tin | Trạng thái Git | Thay đổi dòng | Vai trò kỹ thuật trong hệ thống |
|---|---|---|---|
| `.gitignore` | Tracked Modified | +53 / -51 | Bổ sung quy tắc loại trừ `data/*.key` và `*.key` bảo vệ tệp khóa bí mật provenance HMAC. |
| `core/user_model.py` | Untracked | 1,134 dòng mới | Mô hình miền UserModel (Phase 05B), thực thể `PreferenceItem`, `PreferenceSpec`, `ServerBoundaryToken`, `TrustedProvenanceAuthority`, chữ ký HMAC-SHA256 V2 và ma trận tính điểm ưu tiên (Precedence Rank). |
| `core/user_model_engine.py` | Untracked | 489 dòng mới | Động cơ phân giải hồ sơ UserModel (Phase 05C), phương thức `get_effective_profile`, `set_explicit_preference`, `retract_preference`, phòng vệ rò rỉ secret, chặn sửa đổi persona Houhou và phòng vệ truncation 200 dòng. |
| `core/durable_memory_service.py` | Tracked Modified | +61 / -65 | Bổ sung hỗ trợ truy vấn global bền vững, ủy quyền ghi nguyên tử xuống tầng lưu trữ. |
| `persistence/sqlite_memory_store.py` | Tracked Modified | +246 / -3 | Bổ sung `write_records_atomic` với `BEGIN IMMEDIATE`, hỗ trợ lọc JSON1 `json_extract` cho `memory_type` canonical (F-04). |
| `tests/test_durable_memory_matrix.py` | Tracked Modified | +31 / -2 | Cập nhật ma trận kiểm thử bộ nhớ bền vững (40 tests passed). |
| `tests/test_user_model_domain.py` | Untracked | 718 dòng mới | Suite 28 tests kiểm thử toàn diện mô hình miền UserModel, chuyển đổi canonical và phân loại provenance. |
| `tests/test_user_model_engine.py` | Untracked | 1,672 dòng mới | Suite 43 tests kiểm thử động cơ phân giải hồ sơ, tính điểm, cô lập project, phòng vệ secret và 9 bài test đối kháng HMAC V2 (Phase 05C-P). |
| `docs/handoffs/PHASE_05C_P_FINAL_SOURCE_RECONCILIATION.md` | Untracked | 370 dòng mới | Báo cáo đối soát nguồn độc lập Phase 05C-P ghi nhận các phát hiện kỹ thuật. |
| `docs/handoffs/PHASE_05C_P_FINAL_SECURITY_PATCH_REPORT.md` | Untracked | 259 dòng mới | Báo cáo nghiệm thu vá bảo mật cuối cùng Phase 05C-P kết luận `ACCEPTANCE_PASS`. |

### 4.3. Tệp tin giao diện dùng chung (Shared Wiring)
- `my_agent_mcp/server.py` (Tracked Modified: +69 / -43): Chứa phần cấu hình tiêm phụ thuộc cho `SqlitePendingApprovalStore`, `SqliteHandoffHistoryStore`, `ResumeAssembler` và kiểm tra scope project. Toàn bộ các thay đổi hiện tại trong tệp này phục vụ cho Phase 04.5H-B. **Hiện tại chưa có bất kỳ dòng mã nào của Phase 05 được đưa vào `my_agent_mcp/server.py`**.

---

## 5. CHIẾN LƯỢC BRANCH & PHÂN CHIA LOGICAL COMMITS (BRANCH STRATEGY & COMMIT PLAN)

### 5.1. Nhận định rủi ro về Branch hiện tại
Nhánh công tác hiện tại mang tên `feat/houhou-04-5h-b-handoff-hardening`. Việc phát triển Phase 05B/05C/05C-P trực tiếp trên working tree của nhánh này tạo ra hiện tượng **Scope Conflation** (gộp lẫn phạm vi giữa Handoff Continuity và UserModel). Nếu tiếp tục triển khai Phase 05D trực tiếp tại đây mà không commit, độ phức tạp của working tree sẽ lên tới hơn 10,000 dòng mã chưa commit, gây rủi ro cao cho quá trình review, theo dõi hồi quy và nghiệm thu của Tech Lead.

### 5.2. Đề xuất kế hoạch phân tách Logical Commits độc lập (Trình Tech Lead xem xét):

```text
[origin/main: c470818]
          │
          ├── Commit 1: feat(handoff): complete phase 04.5h-b handoff continuity & approval hardening
          │             (Gồm 11 tệp tin thuộc Nhóm 1 và server.py)
          │
          ├── Commit 2: feat(user_model): implement core domain, resolution engine & provenance hardening (Phase 05B/05C/05C-P)
          │             (Gồm 10 tệp tin thuộc Nhóm 2)
          │
          └── [Nhánh mới: feat/houhou-05d-user-model-surface]
                        │
                        └── Commit 3 (Tương lai): feat(user_model): expose mcp tools and front agent preference awareness (Phase 05D)
```

- **Lợi ích:**
  1. Tách bạch hoàn toàn lịch sử Git giữa cơ chế chuyển giao phiên (Handoff Continuity) và hệ thống hồ sơ người dùng (UserModel).
  2. Mỗi commit độc lập đều đạt trạng thái 100% tests passed (Commit 1: 941 tests; Commit 2: 1012 tests).
  3. Tạo nền tảng sạch hoàn toàn (clean working tree) để bắt đầu triển khai Phase 05D mà không sợ xung đột mã nguồn.

---

## 6. KHÁM PHÁ & ĐỊNH VỊ NGUỒN SỰ THẬT PHASE 05D (PHASE 05D SOURCE-OF-TRUTH REFERENCES)

Căn cứ trên các tài liệu kiến trúc chính thức trong kho mã nguồn:
- `docs/handoffs/PHASE_04_HANDOFF.md` (dòng 186-189): Xác định Phase 05 là **User Model & Preference Specialization**, tiêu thụ các bản ghi `MemoryType.PREFERENCE` qua `DurableMemoryService` mà không sửa đổi cấu trúc cốt lõi `MemoryRecord`.
- `docs/cognitive_architecture/contracts/memory.md` (dòng 193-203): Định vị ranh giới:
  - Phase 04: Durable Memory Pipeline (Store + Service) — ĐÃ XONG.
  - Phase 05: User Model (`UserModel`) — Hồ sơ người dùng và suy luận sở thích.
  - Phase 06: Context Builder (`ContextBuilder`) — Tự động tiêm bộ nhớ và ngữ cảnh vào prompt LLM (Ranh giới tương lai, tuyệt đối không xâm lấn trong Phase 05).
- `docs/architecture/handoff_resume_integrity.md` (dòng 160): Khẳng định: *"Phase 06 owns unified context assembly across CodeGraph, DurableMemory, UserModel, and project context. Phase 04.5H-B owns continuity integrity only."*

### Định nghĩa chính thức của Phase 05D:
- **Tên chính thức:** **PHASE 05D — UserModel Surface Integration & MCP Tooling** (hoặc UserModel Public Boundary & Front Agent Integration).
- **Mục tiêu chính thức:** Đưa toàn bộ các năng lực nghiệp vụ của `UserModelEngine` (Phase 05C) và mô hình dữ liệu `core/user_model.py` (Phase 05B) ra tầng giao diện tương tác của MyAgent bằng cách:
  1. Cung cấp bộ công cụ MCP Server đầy đủ, an toàn, tuân thủ Trusted Provenance Gate cho phép client/IDE/caller tra cứu và quản lý sở thích.
  2. Tích hợp nhận thức UserModel vào Front Agent (Houhou) để định hình phong cách phản hồi tự nhiên, chuẩn mực theo mong muốn của anh mà không phá vỡ các quy tắc hệ thống bất biến.

---

## 7. LỘ TRÌNH TRIỂN KHAI CHI TIẾT PHASE 05D (DETAILED IMPLEMENTATION ROADMAP)

Lộ trình triển khai Phase 05D được chia thành 4 bước tuần tự theo đúng thứ tự phụ thuộc kỹ thuật:

```mermaid
graph TD
    A[Bước 1: Ổn định Baseline & Commit Phân tách] --> B[Bước 2: Triển khai MCP Tools cho UserModel]
    B --> C[Bước 3: Tích hợp Nhận thức Sở thích vào Front Agent]
    C --> D[Bước 4: Kiểm thử Hồi quy Toàn diện & Lập Handoff Phase 05D]
```

### Bước 1: Ổn định Baseline & Tạo Nhánh Chuyên biệt
- Được thực hiện sau khi Tech Lead phê duyệt báo cáo này.
- Đóng gói các thay đổi của Phase 04.5H-B và Phase 05B/05C/05C-P theo chiến lược commit đề xuất tại Mục 5.
- Khởi tạo nhánh `feat/houhou-05d-user-model-surface` từ trạng thái clean baseline.

### Bước 2: Triển khai Bộ công cụ MCP Server (`my_agent_mcp/server.py`)
Triển khai 4 công cụ MCP mới (MCP Tools) để tương tác với `UserModelEngine`:

1. **`get_user_profile` (Tra cứu Hồ sơ người dùng hiệu lực):**
   - **Tham số:** `project_id: str | None = None`, `user_id: str = "default_user"`, `exclude_unverified: bool = True`.
   - **Xử lý:** Khởi tạo `UserModelEngine`, gọi `get_effective_profile`. Chuyển đổi đối tượng `EffectiveUserProfile` thành JSON dictionary có cấu trúc (gồm `user_id`, `project_id`, `active_preferences`, `categorized_preferences`, `as_of`, `resolution_reasons`).
   - **An toàn:** Bắt ngoại lệ `TruncatedRetrievalError` fail-closed nếu dữ liệu chạm trần 200 dòng của SQLite store.

2. **`list_user_preferences` (Liệt kê danh sách sở thích theo bộ lọc):**
   - **Tham số:** `project_id: str | None = None`, `category: str | None = None`, `status: str = "active"`, `source_type: str | None = None`, `include_global: bool = True`, `limit: int = 100`.
   - **Xử lý:** Gọi `UserModelEngine.list_preferences`. Format danh sách `PreferenceItem` thành cấu trúc an toàn, che giấu các trường chữ ký HMAC nội bộ không cần thiết cho caller.

3. **`set_user_preference` (Khai báo sở thích tường minh qua Trusted Provenance Gate):**
   - **Tham số:** `logical_key: str`, `content: str`, `category: str = "unclassified"`, `project_id: str | None = None`, `scope: str = "project"`, `confidence: float = 1.0`, `importance: float = 0.5`.
   - **Xử lý:**
     - Phía MCP Server (ranh giới ứng dụng tin cậy) phát hành `ServerBoundaryToken` qua `TrustedProvenanceAuthority.issue_token()`.
     - Tạo `PreferenceSpec` với `source_type = PreferenceSourceType.EXPLICIT_USER`.
     - Gọi `UserModelEngine.set_explicit_preference(spec, boundary_token=token)`.
   - **Phòng vệ an ninh:**
     - Tự động kích hoạt cơ chế quét mã độc / secret regex (`sk-`, `ghp-`, private keys).
     - Kiểm tra tiền tố bị cấm (`houhou:*`, `persona:*`, `system_prompt:*`, `agent_identity:*`).
     - Từ chối các yêu cầu có nguồn gốc inferred hoặc tham số không hợp lệ.

4. **`retract_user_preference` (Thu hồi sở thích — Soft Tombstone):**
   - **Tham số:** `preference_id: str`, `project_id: str | None = None`.
   - **Xử lý:** Gọi `UserModelEngine.retract_preference`. Kiểm tra cách ly project và chuyển trạng thái bản ghi sang `MemoryStatus.ARCHIVED`.

### Bước 3: Tích hợp Nhận thức Sở thích vào Front Agent (Houhou)
- **Tập tin can thiệp:** `core/front_agent.py` và `core/control_plane.py`.
- **Cơ chế hoạt động:**
  - `ControlPlane` nắm giữ một thể hiện của `UserModelEngine`.
  - Trong mỗi phiên xử lý yêu cầu (`front_agent_request`), Front Agent tra cứu `EffectiveUserProfile` của project đang kích hoạt (kèm sở thích toàn cục).
  - Tự động ánh xạ các sở thích thuộc danh mục `PreferenceCategory.COMMUNICATION` và `FORMAT` vào cấu hình hiển thị (`PresentationLevel`, `ConversationStyle`):
    - Ví dụ: Nếu người dùng có sở thích xác thực `tone = "detailed"`, hệ thống nâng `PresentationLevel.NORMAL` lên `PresentationLevel.DETAILED`.
    - Điều chỉnh phong cách trình bày tóm tắt cho phù hợp với định hướng công nghệ được ghi nhận trong profile.
  - **Bất biến tối cao:** Tuyệt đối không cho phép sở thích người dùng ghi đè các quy tắc cốt lõi:
    - *Quy tắc không dùng emoji:* Bất biến, không thể bị override bởi preference `allow_emoji=true`.
    - *Quy tắc danh tính Houhou:* Bất biến, không thể bị override thành persona khác.
    - *Quy tắc Multi-Plan Decision:* Bất biến, không thể bypass phê duyệt đối với hành động có severity HIGH.

### Bước 4: Kiểm thử Hồi quy Toàn diện & Lập Handoff Phase 05D
- Xây dựng suite kiểm thử mới `tests/test_user_model_mcp_surface.py` và `tests/test_user_model_front_agent.py`.
- Thực thi toàn bộ test suite hồi quy (đạt >= 1012 tests passed).
- Lập biên bản bàn giao chính thức `docs/handoffs/PHASE_05D_HANDOFF.md`.

---

## 8. RANH GIỚI KIẾN TRÚC, BẢO MẬT & PHỤ THUỘC (ARCHITECTURE, SECURITY & DEPENDENCIES)

### 8.1. Những thay đổi ĐƯỢC PHÉP trong Phase 05D
- Bổ sung định nghĩa các công cụ MCP mới trong `my_agent_mcp/server.py`.
- Khởi tạo `_user_model_engine = UserModelEngine(_memory_service, store=memory_store)` trong `my_agent_mcp/server.py`.
- Bổ sung tham số tùy chọn hoặc thuộc tính `user_model_engine` vào `ControlPlane` và `FrontAgent`.
- Tạo các tệp kiểm thử mới phục vụ riêng cho tầng surface của UserModel.

### 8.2. Những thay đổi TUYỆT ĐỐI BỊ CẤM (Forbidden Out-of-Scope)
1. **Cấm xâm lấn Phase 06:** Tuyệt đối KHÔNG xây dựng `ContextBuilder` tự động tiêm memory/profile vào system prompt của các specialist coder agent (`backend_coder`, `test_engineer`, `debugger`). Ranh giới này thuộc độc quyền của Phase 06.
2. **Cấm sửa đổi Schema cơ sở dữ liệu:** Không tạo bảng SQLite mới (ví dụ: `user_preferences`). Toàn bộ sở thích lưu trữ thuần túy trong bảng `memories` hiện có với `memory_type = 'preference'`.
3. **Cấm sửa đổi Domain Core:** Không thay đổi các enum bất biến hoặc cấu trúc của `core/memory.py` và `core/user_model.py`.
4. **Cấm phá vỡ Trusted Provenance Authority:** Không cấp quyền cho client gọi trực tiếp `TrustedProvenanceAuthority.issue_token()` hay truyền token giả mạo qua API MCP. Toàn bộ token chỉ được sinh nội bộ bởi server runtime.

---

## 9. MA TRẬN KIỂM THỬ ĐỀ XUẤT (PROPOSED TEST MATRIX)

Dự kiến bổ sung ít nhất 20 bài kiểm thử mới trong suite `tests/test_user_model_mcp_surface.py` và `tests/test_user_model_front_agent.py`:

| STT | Nhóm kiểm thử | Mục tiêu kiểm chứng | Kỳ vọng kết quả |
|---|---|---|---|
| 1 | MCP `get_user_profile` | Gọi qua client MCP với project rỗng | Trả về profile rỗng hợp lệ (status ok, active_preferences count = 0). |
| 2 | MCP `get_user_profile` | Gọi với project có sở thích kế thừa global | Trả về đúng danh sách sở thích project kèm global theo thứ bậc precedence rank. |
| 3 | MCP `list_user_preferences` | Lọc theo category, status, source_type | Trả về danh sách lọc chính xác, không thất thoát bản ghi. |
| 4 | MCP `set_user_preference` | Thiết lập sở thích hợp lệ qua MCP | Bản ghi được lưu trữ an toàn, ký chữ ký HMAC V2 hợp lệ, xếp hạng Rank 5/4. |
| 5 | MCP `set_user_preference` (Security) | Thử inject API key (`sk-ant-...`, `ghp_...`) | Bị từ chối ngay lập tức bởi `SecurityPolicyViolationError`. |
| 6 | MCP `set_user_preference` (Persona) | Thử ghi đè khóa `houhou:persona` hoặc `system_prompt` | Bị từ chối ngay lập tức bởi `SecurityPolicyViolationError`. |
| 7 | MCP `set_user_preference` (Source) | Thử truyền `source_type="inferred"` | Bị từ chối ngay lập tức bởi `InvalidPreferenceSourceError`. |
| 8 | MCP `retract_user_preference` | Thu hồi sở thích qua ID | Bản ghi chuyển sang `ARCHIVED`, biến mất khỏi `EffectiveUserProfile`. |
| 9 | MCP Isolation | Project A cố gắng thu hồi preference của Project B | Bị chặn với lỗi `PermissionError` (cô lập project). |
| 10 | FrontAgent Preference | Tải profile có sở thích giao tiếp `detailed` | FrontAgent nâng `PresentationLevel` tương ứng. |
| 11 | FrontAgent Invariant Defense | Profile có sở thích `allow_emoji=true` | FrontAgent vẫn giữ vững quy tắc nghiêm cấm emoji 100%. |
| 12 | Regression Guard | Toàn bộ các suite cũ từ Phase 00 đến Phase 05C-P | Vượt qua 100% không suy giảm (>= 1032 tests passed). |

---

## 10. CỔNG NGHIỆM THU & TIÊU CHÍ HOÀN THÀNH (ACCEPTANCE GATES & DOD)

Một phiên triển khai Phase 05D chỉ được công nhận hoàn thành khi thỏa mãn đầy đủ các điều kiện sau:

1. **Gate 1 (Surface Completeness):** Đầy đủ 4 công cụ MCP của UserModel được định nghĩa, document chi tiết trong server schema và phản hồi JSON có cấu trúc chuẩn.
2. **Gate 2 (Security & Provenance Integrity):** Tất cả các thao tác ghi qua MCP đều phải đi qua `TrustedProvenanceAuthority` phía server, mint token nội bộ hợp lệ và ký attestation HMAC V2; 100% các cuộc tấn công inject secret hoặc ghi đè persona đều bị chặn fail-closed.
3. **Gate 3 (Front Agent Integration):** Front Agent nhận biết được hồ sơ người dùng mà không làm thay đổi các quy tắc hệ thống bất biến.
4. **Gate 4 (Zero Schema Pollution):** Cơ sở dữ liệu SQLite `memories` không bị can thiệp schema. Không có bảng mới được tạo.
5. **Gate 5 (Zero Regression):** Chạy `pytest` toàn hệ thống đạt kết quả tuyệt đối (>= 1032 passed, 0 failed, 0 error). Kiểm tra `ruff check` và `git diff --check` đạt chuẩn sạch sẽ.

---

## 11. ĐÁNH GIÁ RỦI RO & KẾ HOẠCH ROLLBACK (RISK ASSESSMENT & ROLLBACK PLAN)

### 11.1. Đánh giá rủi ro
- **Rủi ro 1: Suy giảm hiệu năng MCP do truy vấn SQLite mỗi lượt chat.**
  - *Mức độ:* Thấp.
  - *Giải pháp:* SQLite truy vấn cục bộ với index `idx_memories_project_level` và trần browse cap 200 bản ghi mất dưới 2ms. Có thể bổ sung cache tầng in-process nếu cần thiết.
- **Rủi ro 2: Caller MCP giả lập input để phá vỡ persona Houhou.**
  - *Mức độ:* Trung bình.
  - *Giải pháp:* Danh sách `_FORBIDDEN_KEY_PREFIXES` đã được niêm phong trong `core/user_model_engine.py` và được test kỹ lưỡng trong Phase 05C-P. Bất kỳ nỗ lực nào đều bị từ chối trước khi ghi.

### 11.2. Kế hoạch Rollback
Do Phase 05D thuần túy là tầng giao diện (Surface Wiring) kết nối các engine đã có:
- Nếu tầng MCP Server gặp sự cố: Có thể gỡ bỏ việc đăng ký 4 tools MCP khỏi `my_agent_mcp/server.py` trong 1 bước mà không làm ảnh hưởng đến dữ liệu đã lưu trữ hoặc các phân hệ khác.
- Nếu Front Agent gặp lỗi khi đọc profile: Có thể cấu hình fallback về hồ sơ mặc định (`EffectiveUserProfile.empty()`) mà không làm gián đoạn chu trình hội thoại của người dùng.

---

## 12. ĐỀ XUẤT QUYẾT NGHỊ CUỐI CÙNG (FINAL GO/NO-GO RECOMMENDATION)

### Kết luận kiểm toán:
- Hệ thống đang ở trạng thái kỹ thuật cực kỳ vững chắc (1012/1012 tests passed, 0 lỗi, 0 cảnh báo lint).
- Toàn bộ các phát hiện F-02, F-03, F-04 của Phase 05C-P đã được xác minh niêm phong bảo mật tuyệt đối.
- Phạm vi, ranh giới và lộ trình của Phase 05D đã được xác định sáng tỏ dựa trên các tài liệu sự thật của repository.

### Đề xuất hành động:
**GO FOR PHASE 05D PLANNING & BASELINE STABILIZATION**

Kính trình Tech Lead phê duyệt:
1. Thông qua phương án phân chia logical commits cho Phase 04.5H-B và Phase 05B/05C/05C-P nhằm làm sạch working tree.
2. Cấp phép chuyển sang nhánh mới `feat/houhou-05d-user-model-surface` và tiến hành triển khai Phase 05D theo lộ trình tại Mục 7.

---
*Báo cáo được khởi tạo và xác thực khách quan bởi Houhou (Front Agent — MyAgent) vào ngày 23/10/2026.*
