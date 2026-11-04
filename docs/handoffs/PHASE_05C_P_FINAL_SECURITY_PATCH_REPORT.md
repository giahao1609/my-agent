# BÁO CÁO HIỆU CHỈNH AN NINH CUỐI CÙNG — PHASE 05C-P
## FINAL SECURITY HARDENING & SOURCE RECONCILIATION REPORT

- **Executor:** Houhou (Front Agent — MyAgent)
- **Role:** Tech Lead & Software Companion
- **Repository:** `/Users/haohg/Project/my-agent`
- **Current Branch:** `feat/houhou-04-5h-b-handoff-hardening`
- **Active Git HEAD:** `c4708182ab00be62acc59f36ffdfc0b0946aa901`
- **Execution Mode:** Targeted Security Hardening & Adversarial Verification
- **Test Suite Results:** 1012/1012 passed (Exit code: 0)
- **Lint & Syntax:** `ruff check` passed, `git diff --check` passed (Exit code: 0)
- **Date:** 2026-10-14

---

## MỤC LỤC
1. Tóm tắt điều hành (Executive Summary)
2. Đối soát Git Baseline (Git Baseline Reconciliation)
3. Phân tích nguyên nhân gốc (Root Cause Analysis)
4. Danh mục tệp tin thay đổi (Files Changed)
5. Kiến trúc chữ ký HMAC V2 (HMAC V2 Architecture)
6. Kiểm thử bảo mật đối kháng (Provenance Security Tests)
7. Bảo vệ khóa bí mật Git (Git Secret Protection)
8. Tính tương thích truy xuất Legacy (Legacy Retrieval Compatibility)
9. Ma trận kiểm thử & Kết quả thực thi (Full Test Matrix & Execution Results)
10. Tính tương thích ngược (Backward Compatibility)
11. Đánh giá rủi ro còn lại (Remaining Risks)
12. Kết luận nghiệm thu (Final Verdict: ACCEPTANCE_PASS)
13. Đề xuất GO/NO-GO cho Phase 05D

---

## 1. TÓM TẮT ĐIỀU HÀNH (EXECUTIVE SUMMARY)

Thực hiện chỉ đạo của Tech Lead tại văn bản yêu cầu ngày 14/10/2026, Houhou đã hoàn thành trọn vẹn vòng hiệu chỉnh an ninh cuối cùng của Phase 05C-P. Toàn bộ 3 phát hiện kỹ thuật (F-02, F-03, F-04) ghi nhận trong báo cáo đối soát nguồn trước đó đã được khắc phục triệt để bằng mã nguồn thực tế:

1. **Khắc phục F-02 (P1 — HMAC Attestation Integrity):** Thiết kế và triển khai kiến trúc chữ ký số HMAC-SHA256 phiên bản V2 với định dạng serialization chuẩn hóa xác định (canonical deterministic JSON). Chữ ký V2 ràng buộc 100% các thuộc tính bất biến định danh bản ghi: `memory_id`, `logical_key`, `content_sha256`, `category`, `project_id`, `scope`, `subject`, `memory_type`, `user_id`, `source_type`, `confidence`, `importance`, `valid_from`, `valid_until` và mã băm unreserved metadata (`semantic_metadata_hash`). Mọi hành vi replay sang `memory_id` khác, tráo đổi `category` thành `CONSTRAINT`, hoặc cơi nới thời hạn hiệu lực đều bị từ chối xác thực fail-closed ngay lập tức. Toàn bộ attestation V1 cũ bị từ chối xác thực (fail-closed) và giáng cấp an toàn về `UNVERIFIED_CLAIM` khi kích hoạt `require_attestation=True`.
2. **Khắc phục F-03 (P2 — Provenance Secret Protection):** Cập nhật `.gitignore` để loại trừ `data/*.key` và `*.key`. Kiểm tra bằng `git check-ignore` và `git ls-files` xác nhận tệp `data/provenance.key` đã hoàn toàn được bảo vệ, biến mất khỏi danh sách `untracked files` của Git và không thể bị vô tình đưa vào kho mã nguồn qua lệnh `git add .`. Đồng thời xác minh lịch sử commit toàn bộ nhánh xác nhận tệp khóa chưa từng bị commit trong quá khứ.
3. **Khắc phục F-04 (P2 — Legacy Retrieval Correctness):** Hiệu chỉnh bộ lọc SQL trong `persistence/sqlite_memory_store.py` (`_retrieve_scored_sync`) sử dụng hàm SQLite JSON1 (`json_valid` và `json_extract`) để nhận diện chính xác loại bộ nhớ canonical `__canonical__.memory_type` trong `metadata_json`, ngay cả khi cột SQLite `kind` của các bản ghi legacy mang giá trị không đồng nhất. Đồng thời bọc khối xử lý `json.loads` trong `_row_to_memory` với cơ chế bắt lỗi `(json.JSONDecodeError, TypeError, ValueError)` an toàn, bảo đảm hệ thống không bị crash trước dữ liệu JSON hỏng.
4. **Kiểm thử đối kháng & Hồi quy:** Bổ sung 9 bài kiểm thử đối kháng mới trong test suite `TestPhase05CPAdversarialSecurityAndRetrieval`. Chạy toàn bộ test suite hồi quy của hệ thống đạt kết quả tuyệt đối: **1012/1012 tests passed trong 20.65 giây**. Kiểm tra `git diff --check` và `ruff check` đạt kết quả sạch hoàn toàn (exit code 0).

---

## 2. ĐỐI SOÁT GIT BASELINE (GIT BASELINE RECONCILIATION)

- **Repository Path:** `/Users/haohg/Project/my-agent`
- **Active Branch:** `feat/houhou-04-5h-b-handoff-hardening`
- **Verified Commit SHA:** `c4708182ab00be62acc59f36ffdfc0b0946aa901`
- **Commit Subject:** `Merge pull request #7 from giahao1609/audit/houhou-04-5h-handoff-continuity`
- **Trạng thái kỷ luật:** Tuân thủ tuyệt đối quy định không commit, không push, không merge, không rebase, không checkout và không stash trong suốt phiên hiệu chỉnh an ninh.

---

## 3. PHÂN TÍCH NGUYÊN NHÂN GỐC (ROOT CAUSE ANALYSIS)

### 3.1. F-02: Lỗ hổng thiếu sót trường ràng buộc trong HMAC Payload V1
- **Nguyên nhân gốc:** Thuật toán V1 trước đây ghép chuỗi tự do dạng `f"v1:{issuer}:{token_id}:{logical_key}:{content_sha256}:{pid_str}:{scope_str}:{user_id}:{st_str}"`. Các trường nghiệp vụ nhạy cảm gồm `memory_id`, `category`, `confidence`, `valid_from` và `valid_until` không nằm trong payload ký HMAC.
- **Hệ quả bảo mật:** Kẻ tấn công hoặc caller không tin cậy có thể sao chép attestation hợp lệ của một bản ghi `category = communication` sang một bản ghi khác có cùng `logical_key` và `content`, nhưng nâng cấp `category` thành `constraint` hoặc kéo dài `valid_until` đến năm 2035, khiến hệ thống cấp nhầm mức tin cậy `VERIFIED_EXPLICIT` (Rank 5 hoặc Rank 4).
- **Giải pháp:** Thay thế toàn bộ định dạng payload bằng cấu trúc JSON chính quy V2 (canonical deterministic serialization) với khóa sắp xếp tăng dần và separator chuẩn `(',', ':')`, tích hợp đầy đủ mọi trường định danh và ngữ nghĩa bất biến.

### 3.2. F-03: Nguy cơ rò rỉ tệp khóa bí mật vào Git
- **Nguyên nhân gốc:** Quy tắc trong `.gitignore` chỉ có `data/*.db`, thiếu mẫu loại trừ cho tệp khóa `data/*.key`.
- **Hệ quả bảo mật:** Tệp `data/provenance.key` xuất hiện dưới dạng `untracked file`, có nguy cơ bị `git add .` đẩy lên remote repository.
- **Giải pháp:** Thêm `data/*.key` và `*.key` vào `.gitignore`, xác minh qua `git check-ignore`.

### 3.3. F-04: Bộ lọc SQL loại bỏ nhầm bản ghi Legacy có cột kind khác biệt
- **Nguyên nhân gốc:** `_retrieve_scored_sync` lọc trực tiếp `WHERE kind = ?` trên cột bảng SQLite. Khi bản ghi lưu trữ theo chuẩn Phase 03/04 có `kind` cũ nhưng trường canonical trong `metadata_json` là `preference`, tầng SQL loại bỏ bản ghi trước khi tầng Python phân giải canonical record.
- **Hệ quả bảo mật/nghiệp vụ:** Thất thoát bản ghi sở thích lịch sử khi tổng hợp `EffectiveUserProfile`.
- **Giải pháp:** Cập nhật điều kiện SQL kiểm tra ưu tiên canonical memory type từ JSON khi JSON hợp lệ, fallback về cột `kind` khi không có canonical JSON hoặc JSON hỏng.

---

## 4. DANH MỤC TỆP TIN THAY ĐỔI (FILES CHANGED)

Toàn bộ các thay đổi được kiểm soát chặt chẽ, tối giản và chỉ chạm vào đúng các tệp liên quan trực tiếp đến 3 phát hiện:

| Tệp tin | Loại thay đổi | Mô tả tóm tắt |
|---|---|---|
| `.gitignore` | Cấu hình Git | Bổ sung `data/*.key` và `*.key`, loại bỏ hoàn toàn nguy cơ rò rỉ khóa bí mật. |
| `core/user_model.py` | Core Domain | Nâng cấp `create_attestation` và `verify_record_attestation` lên V2 với canonical payload JSON, kiểm tra 100% trường ràng buộc, xử lý fail-closed V1. |
| `core/user_model_engine.py` | Service Boundary | Cập nhật `set_explicit_preference` sinh `pref_id` trước và truyền đầy đủ trường định danh/thời gian/metadata vào attestation V2. Bổ sung `import uuid`. |
| `core/durable_memory_service.py` | Storage Domain | Cập nhật `_rebuild_with_supersedes` bảo tồn `record.memory_id` hiện hữu để duy trì tính hợp lệ của chữ ký attestation khi superseding. |
| `persistence/sqlite_memory_store.py` | Persistence Store | Cập nhật `_retrieve_scored_sync` lọc SQL nhận diện canonical memory_type trong `metadata_json`; bảo tồn `memory_id` trong `_rebuild_with_supersedes`; bọc bắt lỗi malformed JSON trong `_row_to_memory`. |
| `tests/test_user_model_engine.py` | Test Suite | Cập nhật helper `make_pref_record`; bổ sung test suite `TestPhase05CPAdversarialSecurityAndRetrieval` với 9 kiểm thử đối kháng toàn diện. |

---

## 5. KIẾN TRÚC CHỮ KÝ HMAC V2 (HMAC V2 ARCHITECTURE)

### 5.1. Định dạng Payload Serialization Chuẩn hóa (Canonical Deterministic JSON)
Chữ ký HMAC V2 chấm dứt việc nối chuỗi tự do, sử dụng định dạng JSON chuẩn hóa với các quy chuẩn bất biến:
- Sắp xếp khóa theo thứ tự từ điển (`sort_keys=True`).
- Loại bỏ khoảng trắng thừa giữa các token (`separators=(',', ':')`).
- Chuẩn hóa số thực thành chuỗi có 4 chữ số thập phân (`f"{float(val):.4f}"`).
- Chuẩn hóa thời gian theo định dạng ISO-8601 UTC.
- Mã hóa nhị phân UTF-8 đồng nhất.

```json
{
  "cat": "<normalized_category>",
  "conf": "<confidence_4dp>",
  "csha": "<sha256_of_content>",
  "imp": "<importance_4dp>",
  "iss": "<issuer_identity>",
  "lk": "<logical_key>",
  "mhash": "<sha256_of_unreserved_metadata>",
  "mid": "<memory_id>",
  "mt": "<canonical_memory_type>",
  "pid": "<project_id_or___global__>",
  "sbj": "<subject>",
  "scp": "<scope>",
  "st": "<source_type>",
  "tid": "<server_boundary_token_id>",
  "uid": "<user_id>",
  "v": "2.0",
  "vf": "<valid_from_iso_or_empty>",
  "vu": "<valid_until_iso_or_empty>"
}
```

### 5.2. Phân tách rõ ràng giữa Trường Bất biến và Trạng thái Lifecycle
- **Trường bất biến được bảo vệ bằng chữ ký:** `memory_id`, `logical_key`, `content_sha256`, `category`, `project_id`, `scope`, `subject`, `memory_type`, `user_id`, `source_type`, `confidence`, `importance`, `valid_from`, `valid_until`, `semantic_metadata_hash`.
- **Trạng thái Lifecycle có thể biến đổi hợp lệ (Không đưa vào payload ký):** `status` (`ACTIVE`, `SUPERSEDED`, `ARCHIVED`), `supersedes`, `updated_at`, `last_accessed_at`.
- **Lợi ích kiến trúc:** Khi một bản ghi sở thích bị thay thế (`SUPERSEDED`) hoặc thu hồi (`ARCHIVED`), bản ghi vẫn giữ nguyên chữ ký attestation hợp lệ để phục vụ truy vết kiểm toán nguồn gốc lịch sử (provenance audit trail), không bị mất tính xác thực.

### 5.3. Xử lý An toàn Bản ghi Attestation Cũ V1 (Fail-Closed Policy)
- Bất kỳ attestation nào mang `version: "1.0"` hoặc thiếu trường bắt buộc của V2 đều bị hàm `verify_record_attestation` trả về `False` ngay lập tức.
- Khi gọi `memory_record_to_preference_item(record, require_attestation=True)`, bản ghi có attestation V1 bị giáng cấp an toàn thành `ProvenanceStatus.UNVERIFIED_CLAIM`.
- Hệ thống tuyệt đối không tự động nâng cấp dữ liệu V1 chưa qua kiểm chứng từ trusted boundary.

---

## 6. KIỂM THỬ BẢO MẬT ĐỐI KHÁNG (PROVENANCE SECURITY TESTS)

Đã xây dựng lớp kiểm thử mới `TestPhase05CPAdversarialSecurityAndRetrieval` trong `tests/test_user_model_engine.py` gồm 9 bài test đối kháng độc lập:

1. `test_tampered_category_fails_attestation_and_degrades_claim`: Ký attestation với `category=COMMUNICATION`, sau đó sửa bản ghi thành `category=CONSTRAINT`. Kết quả: Xác minh attestation thất bại (`False`), bản ghi bị giáng cấp về `UNVERIFIED_CLAIM` và `is_verified == False`.
2. `test_tampered_validity_window_fails_attestation`: Ký attestation với `valid_until=2026-12-31`, sau đó sửa bản ghi thành `valid_until=2035-01-01`. Kết quả: Xác minh thất bại.
3. `test_tampered_confidence_fails_attestation`: Ký attestation với `confidence=0.5`, sau đó sửa bản ghi thành `confidence=1.0`. Kết quả: Xác minh thất bại.
4. `test_replayed_memory_id_transplant_fails_attestation`: Sao chép attestation của bản ghi `orig_01` sang bản ghi `trans_02`. Kết quả: Xác minh thất bại do không khớp `memory_id`.
5. `test_v1_legacy_attestation_fails_closed`: Đưa vào attestation V1 cũ. Kết quả: Xác minh fail-closed (`False`), giáng cấp thành `UNVERIFIED_CLAIM`.
6. `test_lifecycle_transition_preserves_attestation_validity`: Chuyển đổi trạng thái bản ghi từ `ACTIVE` sang `SUPERSEDED` rồi sang `ARCHIVED`. Kết quả: Xác minh attestation vẫn thành công 100%.
7. `test_restart_persistence_and_authority_key_recovery`: Xóa cache in-memory của secret (`set_secret(None)`), nạp lại từ đĩa `data/provenance.key`. Kết quả: Mọi bản ghi đã lưu vẫn xác thực thành công tuyệt đối.
8. `test_legacy_kind_retrieval_with_canonical_preference_f04`: Chèn trực tiếp bản ghi có cột `kind = "legacy_custom_note"` nhưng metadata canonical là `"preference"`. Kết quả: Truy vấn `retrieve(memory_type_filter=PREFERENCE)` tìm thấy chính xác bản ghi.
9. `test_malformed_json_fallback_in_retrieval_f04`: Chèn bản ghi có trường `metadata_json` bị hỏng cú pháp. Kết quả: Truy vấn thực thi an toàn, không ném ngoại lệ, trả về bản ghi qua cơ chế fallback.

---

## 7. BẢO VỆ KHÓA BÍ MẬT GIT (GIT SECRET PROTECTION)

- **Cấu hình `.gitignore`:**
  ```gitignore
  # Database & local data
  *.db
  *.db-wal
  *.db-shm
  data/*.db
  data/*.key
  *.key
  ```
- **Xác minh thực tế bằng công cụ Git:**
  ```bash
  $ git check-ignore -v data/provenance.key
  .gitignore:46:*.key	data/provenance.key

  $ git ls-files data/provenance.key
  (Không có kết quả trả về — xác nhận tệp chưa từng bị theo dõi)

  $ git log --all -- data/provenance.key
  (Không có lịch sử commit — xác nhận khóa chưa từng bị lộ ra ngoài)
  ```
- **Phân quyền tệp tin:** Tệp `data/provenance.key` được duy trì phân quyền POSIX nghiêm ngặt `0o600` (`-rw-------`).

---

## 8. TÍNH TƯƠNG THÍCH TRUY XUẤT LEGACY (LEGACY RETRIEVAL COMPATIBILITY)

Cập nhật câu lệnh SQL điều kiện lọc tại `persistence/sqlite_memory_store.py`:
```sql
(
    (json_valid(metadata_json) AND json_extract(metadata_json, '$.__canonical__.memory_type') IS NOT NULL
     AND json_extract(metadata_json, '$.__canonical__.memory_type') = ?)
    OR
    ((NOT json_valid(metadata_json) OR json_extract(metadata_json, '$.__canonical__.memory_type') IS NULL)
     AND kind = ?)
)
```
- Khi dữ liệu có metadata JSON hợp lệ: Hệ thống ưu tiên tuyệt đối loại bộ nhớ canonical `$.__canonical__.memory_type`.
- Khi dữ liệu là hàng legacy thuần túy hoặc JSON bị hỏng: Tự động lùi về so khớp trực tiếp trên cột `kind`.
- Hàm giải mã `_row_to_memory` được bảo vệ bằng khối `try ... except (json.JSONDecodeError, TypeError, ValueError)` bảo đảm tính ổn định tuyệt đối.

---

## 9. MA TRẬN KIỂM THỬ & KẾT QUẢ THỰC THI (FULL TEST MATRIX & EXECUTION RESULTS)

### 9.1. Targeted Test Suite
```bash
$ ./.venv/bin/pytest tests/test_user_model_domain.py tests/test_user_model_engine.py tests/test_durable_memory_matrix.py -q
111 passed in 5.82s
```
- `tests/test_user_model_domain.py`: 28 passed
- `tests/test_user_model_engine.py`: 43 passed (gồm 34 tests gốc + 9 tests đối kháng mới)
- `tests/test_durable_memory_matrix.py`: 40 passed
- **Tổng cộng:** 111 passed, 0 failed, 0 skipped (Exit code: 0).

### 9.2. Full Regression Test Suite
```bash
$ ./.venv/bin/pytest -q
1012 passed in 20.65s
```
- **Tổng cộng:** 1012 passed, 0 failed, 0 skipped (Exit code: 0).
- Tăng trưởng vững chắc từ 1003 tests lên 1012 tests với 100% tỷ lệ vượt qua.

### 9.3. Linting, Syntax & Git Sanity
```bash
$ ./.venv/bin/ruff check core/user_model.py core/user_model_engine.py persistence/sqlite_memory_store.py core/durable_memory_service.py tests/test_user_model_engine.py
All checks passed!

$ git diff --check
(Thoát với mã 0, không có lỗi whitespace thừa hay định dạng tệp)
```

---

## 10. TÍNH TƯƠNG THÍCH NGƯỢC (BACKWARD COMPATIBILITY)

1. **Phase 03/04 Memory Contracts:** Bảo tồn 100% cấu trúc `MemoryRecord`, các enum `MemoryType`, `MemoryLevel`, `MemoryStatus`.
2. **Schema SQLite Bất biến:** Không thay đổi bất kỳ bảng, cột hay index nào của cơ sở dữ liệu SQLite hiện có.
3. **Lineage Đơn nhánh:** Duy trì nguyên tắc `supersedes: str | None`, loại bỏ hoàn toàn nguy cơ sinh đồ thị phân nhánh phức tạp.
4. **Soft Tombstone Semantics:** `ARCHIVED` được xử lý nhất quán dưới dạng trạng thái lịch sử không hoạt động, không xóa vật lý khỏi cơ sở dữ liệu.

---

## 11. ĐÁNH GIÁ RỦI RO CÒN LẠI (REMAINING RISKS)

- **Mức độ rủi ro P0/P1:** 0 (Không còn bất kỳ blocker P0 hoặc P1 nào).
- **Rủi ro lý thuyết (Low):** Sự tranh chấp khóa ghi trong SQLite (`SQLITE_BUSY`) khi có hàng trăm tiến trình đồng thời ghi dữ liệu. Rủi ro này đã được giảm thiểu triệt để bằng cơ chế `BEGIN IMMEDIATE` kèm thời gian chờ `timeout=30.0` giây của SQLite.
- **Ranh giới cô lập Multi-User:** UserModel hiện tại chạy cục bộ đơn người dùng (`user_id="default_user"`). Nhãn `user_id` chỉ mang tính chất định danh ngữ cảnh, chưa đóng vai trò ranh giới cô lập phân quyền đa người thuê (multi-tenant authentication boundary).

---

## 12. KẾT LUẬN NGHIỆM THU (FINAL VERDICT)

Căn cứ trên các bằng chứng kiểm thử tự động và đối soát kỹ thuật:
- Lỗ hổng P1 về tính thiếu sót ràng buộc HMAC của Provenance Attestation (F-02) đã được niêm phong hoàn toàn bằng chuẩn V2.
- Nguy cơ P2 về rò rỉ khóa trong Git (F-03) đã được giải quyết triệt để trong `.gitignore`.
- Độ lệch P2 trong bộ lọc SQL của bản ghi Legacy (F-04) đã được đồng bộ hóa với canonical memory type.
- Toàn bộ 1012/1012 tests đã passed với exit code 0.

### Kết luận:
**ACCEPTANCE_PASS**

---

## 13. ĐỀ XUẤT GO/NO-GO CHO PHASE 05D

**Đề xuất:** **GO FOR PHASE 05D**

Toàn bộ nền tảng cốt lõi của UserModel Domain (Phase 05B), UserModel Engine & Resolution (Phase 05C), cùng Persistence & Provenance Security Hardening (Phase 05C-P) đã sẵn sàng 100%, bảo đảm cơ sở kỹ thuật vững chắc để bước vào triển khai Phase 05D.
