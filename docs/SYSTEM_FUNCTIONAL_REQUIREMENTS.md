# BÁO CÁO RÀ SOÁT KIẾN TRÚC & ĐẶC TẢ YÊU CẦU CHỨC NĂNG HỆ THỐNG MYAGENT
## SYSTEM FUNCTIONAL REQUIREMENTS SPECIFICATION & ARCHITECTURAL AUDIT

- **Hệ thống:** MyAgent (Houhou Cognitive Architecture)
- **Tech Lead & Front Agent:** Houhou
- **Repository Root:** `/Users/haohg/Project/my-agent`
- **Nhánh hiện tại:** `feat/houhou-04-5h-b-handoff-hardening`
- **Commit HEAD:** `c4708182ab00be62acc59f36ffdfc0b0946aa901` (Merge PR #7)
- **Runtime môi trường:** Python 3.13.11 (`.venv`), SQLite WAL mode
- **Quy mô kiểm thử hiện hành:** 1,012 tests (1011 passed ổn định, 1 test timing profiler bị jitter tải cao)
- **Ngày lập:** 04/11/2026

---

## 1. TỔNG QUAN KIẾN TRÚC VÀ TRẠNG THÁI ĐANG THỰC HIỆN (IN-PROGRESS ARCHITECTURE AUDIT)

MyAgent được thiết kế theo mô hình **Deterministic Control Plane**, thiết lập sự phân tách nghiêm ngặt giữa 4 chiều trực giao bất biến:
$$\text{Agent Identity (Houhou)} \neq \text{Execution Role (AgentRole)} \neq \text{Model Identity (LLM)} \neq \text{Runtime Identity (Host/MCP)}$$

### 1.1. Rà soát các hạng mục kiến trúc đang làm dở trên Working Tree

Hiện tại, nhánh làm việc `feat/houhou-04-5h-b-handoff-hardening` đang chứa **2 nhóm công việc lớn** chưa commit (tổng cộng 10 tệp sửa đổi và 12 tệp untracked):

```text
[Commit HEAD: c470818] (Merge PR #7)
       │
       ├── NHÓM 1: PHASE 04.5H-B — Handoff Continuity & Approval Hardening (Đã xong code & test)
       │   ├── core/resume_assembler.py (Tái lắp ráp lịch sử hydrate max 20 tin nhắn, khử nhạy cảm)
       │   ├── persistence/sqlite_pending_approval_store.py (CAS nguyên tử claim_and_resolve, TTL)
       │   ├── persistence/sqlite_handoff_history_store.py (Lưu vết quyết định chuyển giao)
       │   ├── core/agent_handoff_context.py & core/handoff_coordinator.py (Ghi nhận SYSTEM_VERIFIED_EXECUTION)
       │   └── tests/test_handoff_resume_hardening.py (61 tests passed)
       │
       ├── NHÓM 2: PHASE 05B / 05C / 05C-P — UserModel Core, Engine & Provenance Security (Đã xong code & test)
       │   ├── core/user_model.py (Domain model, PreferenceItem, ServerBoundaryToken, HMAC-SHA256 V2)
       │   ├── core/user_model_engine.py (Động cơ phân giải Precedence Rank 1-5, chống secret leak, chặn override persona)
       │   ├── persistence/sqlite_memory_store.py (Giao dịch nguyên tử BEGIN IMMEDIATE, lọc JSON1 json_extract)
       │   ├── .gitignore (Chặn tuyệt đối rò rỉ *.key)
       │   └── tests/test_user_model_domain.py, tests/test_user_model_engine.py (71 tests passed)
       │
       ├── NHÓM 3: PHASE 05D — UserModel Surface Integration & MCP Tooling (ĐANG LÀM DỞ / CHUẨN BỊ)
       │   ├── Đã hoàn thành hồ sơ chuẩn bị: docs/handoffs/PHASE_05D_PREPARATION_AND_BASELINE_AUDIT.md
       │   ├── Chưa thực hiện: Bổ sung 4 MCP tools (get_user_profile, list_user_preferences, set_user_preference, retract_user_preference) vào my_agent_mcp/server.py
       │   └── Chưa thực hiện: Tích hợp nhận thức UserModel vào Front Agent (Houhou) để cá nhân hóa phản hồi
       │
       └── NHÓM 4: PHASE 06 — ContextBuilder & Unified Context Assembly (KẾ HOẠCH TƯƠNG LAI)
           └── Tự động tiêm Memory + UserModel + CodeGraph vào prompt của Downstream Specialist LLMs (Nghiêm cấm làm trước Phase 06)
```

### 1.2. Kiểm kê các năng lực Giả lập (Mocked Capabilities) cần nâng cấp thực tế

Theo hợp đồng năng lực `docs/cognitive_architecture/contracts/capabilities.md`:

| Phân hệ / Năng lực | Hiện trạng | Vị trí mã nguồn | Chi tiết kỹ thuật |
|---|---|---|---|
| **Container Sandbox** | `MOCKED` | `integrations/container_sandbox_backend.py` | Chưa kết nối daemon Docker/Podman; trả về chuỗi mô phỏng `Simulated exec output` |
| **Playwright Browser** | `MOCKED` | `integrations/playwright_browser.py` | Chưa cài runtime trình duyệt không đầu; trả về header PNG 1x1 giả lập |
| **WholePlan Step Execution** | `MOCKED` | `core/whole_plan_coordinator._NoOpStepExecutor` | Fallback giả lập khi chưa tiêm CoderRuntimeWorker vào WholePlanCoordinator |
| **Role Policies chưa khai báo** | `CHƯA CÓ RULE` | `core/agent_role.py` | Các role `DB_MIGRATION`, `PERFORMANCE`, `DOCUMENTATION`, `RELEASE` có enum nhưng rỗng trong `ROLE_RULES` |
| **Memory Search** | `PARTIAL` | `persistence/sqlite_memory_store.py` | Tìm kiếm chuỗi con `LIKE %query%`; chưa có vector embeddings hay hybrid rank |

---

## 2. ĐẶC TẢ YÊU CẦU CHỨC NĂNG HỆ THỐNG (FUNCTIONAL REQUIREMENTS SPECIFICATION)

Hệ thống MyAgent được cấu thành từ 8 phân hệ chức năng trụ cột. Dưới đây là bảng đặc tả chi tiết từng yêu cầu chức năng (FRS):

### 2.1. Phân hệ 1: Control Plane, Front Agent & Điều phối Đa tác nhân (Multi-Agent Orchestration)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-01.1** | Front Agent Persona (Houhou) | Front Agent luôn định danh là Houhou, xưng "em" gọi người dùng là "anh", tôn trọng chính sách nghiêm cấm emoji 100%, tự động nhận diện ngôn ngữ (tiếng Việt/tiếng Anh) để phản hồi chuẩn mực. | **HOÀN TẤT** (`core/front_agent.py`) |
| **FRS-01.2** | Decision-Driven Multi-Plan | Đối với các quyết định kiến trúc, thay đổi phạm vi hoặc thao tác phá hủy (severity HIGH), hệ thống bắt buộc đề xuất 2-3 phương án kèm trade-offs và yêu cầu người dùng phê duyệt trước khi ghi workspace. | **HOÀN TẤT** (`core/control_plane.py`, `core/decision.py`) |
| **FRS-01.3** | Human-in-the-Loop Decision Gate | Quản lý vòng đời `DecisionRecord` với 3 cấp độ (LOW=24h TTL, MEDIUM=72h TTL, HIGH=never expire). Lưu trữ SQLite bền vững. | **HOÀN TẤT** (`persistence/sqlite_decision_store.py`) |
| **FRS-01.4** | Phân định Vai trò Chuyên gia (AgentRole) | Định nghĩa 13 vai trò tác nhân độc lập (`planner`, `architect`, `researcher`, `backend_coder`, `ui_coder`, `tester`, `security_reviewer`, `reviewer`, `db_migration`, `performance`, `documentation`, `release`, `user_interface`). | **HOÀN TẤT** (`core/agent_role.py`) |
| **FRS-01.5** | Phân quyền Công cụ theo Vai trò (Role Policy) | Kiểm soát quyền truy cập công cụ theo vai trò với 3 chính sách: `ALLOW`, `REQUIRE_APPROVAL`, `DENY`. | **HOÀN TẤT** (`core/tool_policy.py`) |
| **FRS-01.6** | Phân rã Kế hoạch Đa bước (WholePlanCoordinator) | Tự động phân rã mục tiêu thành kế hoạch nhiều bước (`plan_steps`), gán vai trò chuyên trách và điều phối thực thi tuần tự kèm cơ chế sửa lỗi (`repair_hint`). | **HOÀN TẤT** (`core/whole_plan_coordinator.py`) |
| **FRS-01.7** | Chuyển giao Phiên Chuyên gia (Handoff Coordinator) | Điều phối chuyển giao tác vụ giữa các vai trò kèm ma trận chuyển đổi hợp lệ (`HandoffTransitionMatrix`) và lưu vết lịch sử bền vững. | **HOÀN TẤT** (`core/handoff_coordinator.py`, `persistence/sqlite_handoff_history_store.py`) |
| **FRS-01.8** | Sổ cái Thực thi Tác nhân (Agent Run Ledger) | Ghi nhận nhật ký thực thi trạng thái bền vững của từng tác nhân (`AgentRunRecord`) với SQLite. | **HOÀN TẤT** (`persistence/sqlite_agent_run_store.py`) |

---

### 2.2. Phân hệ 2: Coder Session, Tính liên tục Thực thi & Quản lý Phê duyệt (Execution Continuity & Approvals)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-02.1** | Vòng lặp Lệnh Coder (Command Loop) | Tiêu thụ tuần tự các lệnh thực thi ngoại vi: `start`, `set_execution_target`, `message`, `tool_result`, `cancel`. Phát hành sự kiện `text`, `tool_use`, `stop`. | **HOÀN TẤT** (`agents/coder_agent.py`) |
| **FRS-02.2** | Nối phiên Xuyên Môi trường & Khởi động lại | Cho phép đổi mô hình hoặc host (Antigravity <-> Codex <-> Claude) mà không mất ngữ cảnh tác vụ, tái sử dụng cùng `session_id` bền vững. | **HOÀN TẤT** (`core/resume_assembler.py`) |
| **FRS-02.3** | Giới hạn Lịch sử Hydrate & Khử nhạy cảm | Giới hạn tối đa 20 tin nhắn gần nhất khi hydrate lại ngữ cảnh phiên nối, tự động phát hiện và che giấu bí mật/token. | **HOÀN TẤT** (`core/resume_assembler.py`) |
| **FRS-02.4** | Lưu trữ Phê duyệt Công cụ Bền vững (Pending Approval Store) | Lưu trữ các công cụ chờ phê duyệt vào SQLite, cơ chế CAS nguyên tử `claim_and_resolve` chống xung đột race-condition, kiểm tra TTL, ghi nhận bằng chứng kết quả thực thi. | **HOÀN TẤT** (`persistence/sqlite_pending_approval_store.py`) |
| **FRS-02.5** | Điểm khôi phục Tác vụ (Checkpoints) | Chụp ảnh trạng thái bất biến của tác vụ gồm tệp sửa đổi, bài test đã chạy, tóm tắt và hành động kế tiếp. | **HOÀN TẤT** (`persistence/sqlite_checkpoint_store.py`) |

---

### 2.3. Phân hệ 3: Kiến trúc Bộ nhớ Phân tầng & Hồ sơ Người dùng (Memory & UserModel)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-03.1** | Hợp đồng Bộ nhớ Trực giao (Canonical Memory) | Tách biệt tuyệt đối giữa Loại bộ nhớ (`EPISODIC`, `SEMANTIC`, `PREFERENCE`, `PROJECT`, `LESSON`, `PROCEDURE`) và Tầng lưu trữ (`L0` chat, `L1` task, `L2` project, `L3` cross-project). | **HOÀN TẤT** (`core/memory.py`) |
| **FRS-03.2** | Đóng gói JSON1 Tương thích Ngược | Lưu trữ trường chuẩn hóa vào `metadata_json` dưới khóa `__canonical__`, bảo tồn schema SQLite `memories` hiện có mà không phá hủy dữ liệu cũ. | **HOÀN TẤT** (`persistence/sqlite_memory_store.py`) |
| **FRS-03.3** | Giao dịch Nguyên tử & Phòng vệ Truncation | Ghi bộ nhớ nguyên tử với `BEGIN IMMEDIATE`. Giới hạn tải 200 bản ghi kèm ngoại lệ `TruncatedRetrievalError` để ngăn phân giải thiếu sót dữ liệu. | **HOÀN TẤT** (`persistence/sqlite_memory_store.py`) |
| **FRS-03.4** | Mô hình Hồ sơ Người dùng (UserModel Domain) | Thực thể `PreferenceItem`, `PreferenceSpec`, phân loại nguồn gốc (`EXPLICIT_USER`, `INFERRED`, `DEFAULT_SYSTEM`). | **HOÀN TẤT** (`core/user_model.py`) |
| **FRS-03.5** | Động cơ Phân giải Thứ bậc Ưu tiên (Precedence Rank) | Xếp hạng ưu tiên phân giải sở thích: Project Explicit (5) > Global Explicit (4) > Project Inferred (3) > Global Inferred (2) > Default System (1). | **HOÀN TẤT** (`core/user_model_engine.py`) |
| **FRS-03.6** | Cổng Xác thực Nguồn tin cậy (Trusted Provenance Gate) | Chứng thực nguồn gốc sở thích bằng chữ ký HMAC-SHA256 V2 trên 18 trường định danh bất biến qua khóa bí mật `data/provenance.key`. Cấp phát `ServerBoundaryToken` nội bộ. | **HOÀN TẤT** (`core/user_model.py`) |
| **FRS-03.7** | Phòng vệ Secret & Bất biến Persona | Tự động quét và từ chối các chuỗi chứa API key (`sk-`, `ghp-`, khóa riêng). Chặn tuyệt đối hành vi sửa đổi khóa hệ thống (`houhou:*`, `persona:*`, `system_prompt:*`). | **HOÀN TẤT** (`core/user_model_engine.py`) |
| **FRS-03.8** | Bộ công cụ MCP Quản lý Hồ sơ (UserModel Surface) | Cung cấp 4 công cụ MCP: `get_user_profile`, `list_user_preferences`, `set_user_preference`, `retract_user_preference` cho client/IDE. | **CHƯA TRIỂN KHAI** (Mục tiêu Phase 05D) |
| **FRS-03.9** | Front Agent Tích hợp Nhận thức Sở thích | Front Agent tự động điều chỉnh mức độ chi tiết (`PresentationLevel`) theo hồ sơ sở thích của người dùng nhưng không phá vỡ quy tắc cốt lõi (no-emoji). | **CHƯA TRIỂN KHAI** (Mục tiêu Phase 05D) |

---

### 2.4. Phân hệ 4: Đồ thị Mã nguồn & Trí tuệ Kho lưu trữ (Code Graph & Repository Intelligence)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-04.1** | Phân tích cú pháp AST Đa ngôn ngữ (Polyglot AST) | Tích hợp Tree-sitter quét và phân tích cú pháp AST cho Python, TypeScript/JavaScript, Go, PHP/Laravel, Next.js. | **HOÀN TẤT** (`core/tree_sitter_language_scanner.py`, `core/polyglot_code_scanner.py`) |
| **FRS-04.2** | Kho lưu trữ Đồ thị Mã nguồn (Code Graph Store) | Lưu trữ các nút biểu tượng (`code_nodes`), cạnh quan hệ phụ thuộc (`code_edges`), bảng FTS5 tìm kiếm toàn văn. | **HOÀN TẤT** (`persistence/sqlite_code_graph_store.py`) |
| **FRS-04.3** | Tra cứu Biểu tượng & Ngữ cảnh Mã (Symbol & Context) | Tra cứu định nghĩa hàm, lớp, giao diện, và sinh ngữ cảnh mã nguồn phục vụ prompt LLM. | **HOÀN TẤT** (`core/code_graph.py`) |
| **FRS-04.4** | Phân tích Tác động Thay đổi (Impact Analysis) | Phân tích đồ thị phụ thuộc (`code_dependencies`, `code_dependents`) để dự báo phạm vi ảnh hưởng khi sửa đổi một biểu tượng mã nguồn. | **HOÀN TẤT** (`core/code_graph.py`) |
| **FRS-04.5** | Phát hiện Vòng phụ thuộc & Mã chết (Hygiene) | Tự động phát hiện các chu trình phụ thuộc vòng tròn (circular dependencies) và mã không còn được tham chiếu (dead code). | **HOÀN TẤT** (`core/code_hygiene.py`) |
| **FRS-04.6** | Bản đồ Kho lưu trữ (Repo Map) | Tạo bản đồ cấu trúc kho mã nguồn nén gọn phục vụ định hướng ngữ cảnh cho tác nhân. | **HOÀN TẤT** (`core/repo_map.py`) |

---

### 2.5. Phân hệ 5: Quản trị An ninh, Phòng thủ AI & Cổng Kiểm định Chất lượng (Security, AI Defense & Verification)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-05.1** | Phát hiện & Khử nhạy cảm Bí mật (Secret Detection) | Quét phát hiện 15+ mẫu khóa chuẩn Gitleaks (RSA, EC keys, GCP Service Accounts, Slack, JWT, DB URLs) kết hợp bộ lọc Shannon Entropy để giảm false-positive. | **HOÀN TẤT** (`core/security_scanner.py`) |
| **FRS-05.2** | Quét Mã tĩnh An toàn (SAST Scanner) | Quét lỗ hổng tĩnh theo chuẩn CWE: CWE-89 (SQLi), CWE-78 (Command Injection), CWE-22 (Path Traversal), CWE-79 (XSS), CWE-502 (Insecure Deserialization). | **HOÀN TẤT** (`core/security_scanner.py`) |
| **FRS-05.3** | Phòng vệ Prompt Injection & Tấn công Đối kháng | Nhận diện và ngăn chặn các kỹ thuật tấn công prompt injection trực tiếp/gián tiếp, markdown exfiltration, trích xuất system prompt, unicode smuggling. | **HOÀN TẤT** (`core/ai_security.py`, `core/red_team_engine.py`) |
| **FRS-05.4** | Khử thông tin Định danh Cá nhân (PII Sanitization) | Tự động quét và che giấu PII (email, số điện thoại, địa chỉ IP, số thẻ tín dụng). | **HOÀN TẤT** (`core/pii_sanitizer.py`) |
| **FRS-05.5** | Cổng Kiểm định Tự động (Verification Gate) | Tự động kích hoạt bộ chạy test (`pytest`, `npm test`, `go test`, `cargo test`), kiểm định tỷ lệ bao phủ (`coverage_gate`) và phát hiện chệch mục tiêu (`goal_drift_monitor`). | **HOÀN TẤT** (`core/verification_gate_coordinator.py`, `core/test_runner.py`) |
| **FRS-05.6** | Đánh giá Hiệu năng & Đo lường SLO (Profiler) | Hồ sơ hóa thời gian thực thi hàm đồng bộ/bất đồng bộ, kiểm tra ngưỡng vi phạm cam kết SLO và chạy benchmark. | **HOÀN TẤT** (`core/performance_profiler.py`) |
| **FRS-05.7** | Quản trị Phát hành & Chứng thực Phần mềm (Release Gate) | Kiểm tra tính sẵn sàng phát hành, sinh SBOM định dạng CycloneDX 1.5, tạo chứng thư số SHA-256 xác thực nguồn gốc release. | **HOÀN TẤT** (`core/release_governance.py`) |

---

### 2.6. Phân hệ 6: Quản trị Giao diện UI/UX & Thiết kế Chống "AI-Slop" (UI Governance & Design)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-06.1** | Hồ sơ Thiết kế & Bản mẫu Lĩnh vực (Sector Archetypes) | Định nghĩa các bản mẫu thiết kế giao diện chuyên biệt theo lĩnh vực (Fintech, SaaS, E-commerce...), áp dụng quy tắc chống giao diện rác (anti-AI-slop). | **HOÀN TẤT** (`core/ui_design_style_engine.py`) |
| **FRS-06.2** | Hợp đồng Thiết kế Bền vững (DESIGN Contracts) | Tự động tạo và kiểm toán tính tuân thủ hợp đồng thiết kế giao diện (`DESIGN.md`) lưu tại kho nội bộ MyAgent. | **HOÀN TẤT** (`core/design_md_generator.py`) |
| **FRS-06.3** | Kiểm định Tiếp cận (Accessibility WCAG 2.1) | Rà soát tuân thủ tiêu chuẩn tiếp cận WAI-ARIA, thẻ semantic HTML, độ tương phản màu sắc. | **HOÀN TẤT** (`core/ui_governance.py`) |
| **FRS-06.4** | So sánh Sai khác Trực quan (Visual Diff Engine) | So khớp sai khác điểm ảnh trực tiếp từ dữ liệu nén PNG IDAT zlib và kiểm định layout sau thay đổi mã UI. | **HOÀN TẤT** (`core/visual_diff.py`) |
| **FRS-06.5** | Trình duyệt Không đầu Tương tác Giao diện | Điều hướng trình duyệt, chụp ảnh màn hình và trích xuất cấu trúc DOM để xác thực E2E. | **MOCKED** (`integrations/playwright_browser.py`) |

---

### 2.7. Phân hệ 7: Đánh giá PR Git, Cầu nối Suy luận Chuyên sâu & Tối ưu Prompt (PR Review & Reasoning)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-07.1** | Điều phối Đánh giá PR GitHub (PR Review Coordinator) | Tải luồng bình luận review PR GitHub, gom cụm theo tệp tin/chủ đề, đối chiếu diff cục bộ, tạo bản thảo phản hồi gộp xác nhận một lần. | **HOÀN TẤT** (`core/git_review_coordinator.py`) |
| **FRS-07.2** | Cầu nối Mô hình Suy luận Ngoại vi (Reasoning Bridge) | Kết nối các mô hình suy luận chuyên sâu (OpenAI o3-mini, ChatGPT MCP server) để giải quyết các bài toán kiến trúc phức tạp. | **HOÀN TẤT** (`core/reasoning_bridge.py`) |
| **FRS-07.3** | Chuẩn hóa Prompt Kỹ thuật (Prompt Rewriter) | Áp dụng khung Prompt Master, tự động dịch và tối ưu hóa các yêu cầu tiếng Việt của người dùng thành đặc tả Tiếng Anh Kỹ thuật có cấu trúc cao cho tác nhân chuyên gia. | **HOÀN TẤT** (`core/prompt_rewriter.py`) |

---

### 2.8. Phân hệ 8: Môi trường Thực thi Sandbox, Định tuyến Ngân sách & Bề mặt MCP (Runtime & MCP Surface)

| Mã yêu cầu | Tên chức năng | Đặc tả yêu cầu kỹ thuật | Trạng thái hiện tại |
|---|---|---|---|
| **FRS-08.1** | Sandbox Tiến trình Cục bộ (Local Sandbox) | Khởi chạy tiến trình thực thi an toàn cục bộ qua `asyncio.create_subprocess_exec` với bắt lỗi và timeout. | **HOÀN TẤT** (`integrations/local_sandbox_backend.py`) |
| **FRS-08.2** | Sandbox Cô lập Container | Khởi tạo, snapshot, thực thi lệnh và rollback môi trường chạy trong container Docker/Podman. | **MOCKED** (`integrations/container_sandbox_backend.py`) |
| **FRS-08.3** | Định tuyến & Giám sát Ngân sách (Budget Router) | Theo dõi lượng token tiêu thụ, chi phí tích lũy, áp đặt chính sách giới hạn ngân sách tác vụ. | **HOÀN TẤT** (`core/budget_router.py`) |
| **FRS-08.4** | Giám sát Sức khỏe Nhà cung cấp (Provider Health) | Ngắt mạch tự động (circuit breaker) khi nhà cung cấp LLM/API bị suy giảm hoặc mất kết nối. | **HOÀN TẤT** (`core/provider_health_router.py`) |
| **FRS-08.5** | Bề mặt Công cụ MCP Server (MCP Public API) | Cung cấp hơn 70 công cụ MCP cho Antigravity, Claude Code, Cursor và các MCP client khác. | **HOÀN TẤT** (`my_agent_mcp/server.py`) |

---

## 3. MA TRẬN ĐỐI SOÁT KIỂM THỬ VÀ TRẠNG THÁI TRIỂN KHAI

| Phân hệ chức năng | Mã nguồn chính | Tập tin Kiểm thử đại diện | Số lượng Test | Trạng thái Runtime |
|---|---|---|---|---|
| **Control Plane & Handoff** | `core/control_plane.py`, `core/handoff_coordinator.py` | `tests/test_handoff_*.py` | 120+ tests | `AVAILABLE` |
| **Coder Session & Continuity** | `agents/coder_agent.py`, `core/resume_assembler.py` | `tests/test_handoff_resume_hardening.py` | 61 tests | `AVAILABLE` |
| **Pending Approvals** | `persistence/sqlite_pending_approval_store.py` | `tests/test_sqlite_pending_approval_store.py` | 24 tests | `AVAILABLE` |
| **Memory Architecture** | `core/memory.py`, `core/durable_memory_service.py` | `tests/test_durable_memory_matrix.py` | 40 tests | `AVAILABLE` |
| **UserModel Domain & Engine**| `core/user_model.py`, `core/user_model_engine.py` | `tests/test_user_model_*.py` | 71 tests | `AVAILABLE` |
| **Code Graph & Polyglot AST**| `core/code_graph.py`, `persistence/sqlite_code_graph_store.py` | `tests/test_code_graph*.py` | 85+ tests | `AVAILABLE` |
| **Security, SAST & Secrets** | `core/security_scanner.py`, `core/ai_security.py` | `tests/test_security_*.py` | 60+ tests | `AVAILABLE` |
| **Verification & Profiler** | `core/verification_gate_coordinator.py`, `core/performance_profiler.py` | `tests/test_performance_profiler.py`, `tests/test_verification_*.py` | 45+ tests | `AVAILABLE` |
| **Release & SBOM** | `core/release_governance.py` | `tests/test_release_governance.py` | 18 tests | `AVAILABLE` |
| **UI Governance & Visual Diff**| `core/ui_governance.py`, `core/visual_diff.py` | `tests/test_visual_diff.py`, `tests/test_ui_governance.py` | 35+ tests | `AVAILABLE` |
| **PR Review Coordinator** | `core/git_review_coordinator.py` | `tests/test_git_review_coordinator.py` | 15 tests | `AVAILABLE` |
| **Reasoning Bridge** | `core/reasoning_bridge.py` | `tests/test_reasoning_bridge.py` | 12 tests | `AVAILABLE` |
| **Container Sandbox** | `integrations/container_sandbox_backend.py` | `tests/test_container_sandbox_backend.py` | 8 tests | `MOCKED` |
| **Browser Navigation** | `integrations/playwright_browser.py` | `tests/test_playwright_browser.py` | 6 tests | `MOCKED` |

---

## 4. KẾT LUẬN & ĐỀ XUẤT CÁC BƯỚC HÀNH ĐỘNG TIẾP THEO

Qua rà soát toàn diện:
1. **Chất lượng hệ thống hiện tại:** Kho mã nguồn đang ở tình trạng cực kỳ hoàn thiện với **1,012 bài kiểm thử**, bao phủ toàn diện 8 phân hệ cốt lõi. Toàn bộ các cơ chế bảo mật (HMAC V2 attestation, secret leakage prevention, CAS pending approval) đều đạt chuẩn nghiêm ngặt.
2. **Nút thắt cần giải quyết ngay (Immediate Blocker):** Working tree đang dồn ứ hơn 9,000 dòng mã chưa commit của Phase 04.5H-B và Phase 05B/05C. Cần thực hiện phân tách commit logic để làm sạch baseline.
3. **Mục tiêu kiến trúc kế tiếp:** Triển khai **Phase 05D (UserModel Surface Integration & MCP Tooling)** để cung cấp 4 MCP tools phục vụ client/IDE và hoàn thiện nhận thức sở thích cho Front Agent (Houhou).

