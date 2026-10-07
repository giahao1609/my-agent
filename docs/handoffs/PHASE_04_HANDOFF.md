# PHASE 04 HANDOFF — Durable Memory Pipeline (Final Completion Gate)

## PHASE

04 — Durable Memory Pipeline

## BRANCH

`feat/houhou-04-durable-memory-pipeline`

## BASE_COMMIT

`d0aa10fb42bdf326bbee6b1c558c8855ccac026a` (origin/main, main after Phase 03 merge)

## COMMIT_POLICY

A Phase 04 commit already exists (`569964c feat(memory): implement durable memory pipeline (Phase 04)`).
Per strict Critical Commit Policy, no `git commit`, `git commit --amend`, `git push`, `git merge`, or `git rebase` was executed during this verification and hardening pass. All final gate implementations and test matrix additions are preserved in the working tree for explicit human review.

---

## PYTHON_ENVIRONMENT

- **PYTHON_VERSION:** Python 3.13.11
- **PYTHON_EXECUTABLE:** `/Users/haohg/Project/my-agent/.venv/bin/python`
- **PYTHON_RUNTIME_CHANGED:** False (system and venv already standard on Python 3.13.11)

---

## ARCHITECTURE & AUDIT IMPLEMENTATIONS

### DURABLE_MEMORY_SERVICE
`core.durable_memory_service.DurableMemoryService` is the single, authoritative orchestration boundary for all durable memory operations (write, retrieve, archive, consolidate_and_persist). It exclusively consumes canonical `MemoryRecord` instances from Phase 03.

### CONSOLIDATION_PERSISTENCE_PATH
- `DurableMemoryService.consolidate_and_persist(consolidator, context, session_messages, resolve_conflicts=True)`:
  Explicit durable bridge from `MemoryConsolidator` to SQLite persistence.
- Pure `MemoryConsolidator.consolidate_session_records(...)` remains 100% side-effect free and in-memory.

### MCP_CONSOLIDATE_MEMORY_BEHAVIOR
`my_agent_mcp.server.consolidate_memory` is wired through `DurableMemoryService.consolidate_and_persist()`. When successful consolidation is reported, canonical records exist durably in SQLite and survive process/store re-instantiation.

### IDEMPOTENCY_POLICY & CANONICAL SEMANTIC EQUIVALENCE
- Canonical helper `core.memory.are_canonically_equivalent(a, b)`:
  Audits `memory_id`, `project_id`, `memory_type`, `tier`, `content`, `importance` (tolerance 1e-6), `confidence` (tolerance 1e-6), `subject`, `scope`, `source`, `status`, `supersedes`, `valid_from`, `valid_until`, and relevant metadata (including `logical_key`).
- **Operational Non-Semantic Metadata Allowlist:** Blanket `__*` prefix filtering is replaced with explicit `NON_SEMANTIC_OPERATIONAL_METADATA_KEYS = frozenset({"__status_updated_at"})`. All other caller metadata (including arbitrary `__*` keys like `__business_flag`) is strictly semantic.
- **Operational Timestamp Exclusion:** Operational lifecycle timestamps (`created_at`, `updated_at`, `last_accessed_at`) are excluded.
- **Idempotent Success:** Same `memory_id` + identical canonical semantics accepted idempotently without duplication.
- **Conflict Rejection:** Same `memory_id` + changed canonical semantics (subject, scope, tier, confidence, metadata, etc.) is rejected with explicit `ValueError`; silent overwrite is strictly forbidden.

### LEGACY_GLOBAL_SENTINEL_POLICY
- **Canonical Representation:** `project_id=None` and `scope="global"`.
- **SQLite Write Representation:** `project_id=""`.
- **Reserved Legacy Sentinel:** `__global__` is reserved exclusively for backward-compatible reads of pre-Phase-03 rows.
- **Write Rejection:** New durable writes (`SQLiteMemoryStore.add_many`, `SQLiteMemoryStore.supersede`, `DurableMemoryService.write`) and MCP `consolidate_memory` with explicit `project_id="__global__"` are strictly rejected with `ValueError("RESERVED_SENTINEL: ...")`.
- **Legacy Read Deserialization:** Existing legacy rows with SQLite `project_id="__global__"` are deserialized to canonical `project_id=None`.

### SQL_INSERT_POLICY & INTEGRITY ERROR HANDLING
- Broad SQLite `INSERT OR IGNORE` is completely removed from durable writes.
- Normal `INSERT INTO memories` is executed.
- Narrow `IntegrityError` handling specifically intercepts `memory_id` primary key / unique constraint collisions, re-reads the existing row, and applies `are_canonically_equivalent()`.
- Unexpected `IntegrityError` exceptions propagate immediately.

### MCP_PROJECT_IDENTITY_POLICY
- `my_agent_mcp.server.consolidate_memory` resolves project context deterministically:
  1. Explicit `project_id` if provided.
  2. Registered active project via `projects.get_active()`.
  3. Otherwise fails explicitly raising `ValueError("PROJECT_CONTEXT_REQUIRED: ...")`.
  Fabricating `project_id` from `session_id` is strictly forbidden.

### CONFLICT_IDENTITY
Composite conflict identity is strictly defined as `(project_id, subject, scope, memory_type, logical_key)`.
Different subjects, scopes, or memory types with the same `logical_key` never supersede each other. Conflict resolution requires explicit opt-in via non-empty `logical_key`. Natural language similarity alone never triggers superseding.

### SUPERSEDING_TRANSACTION & ROLLBACK_BEHAVIOR
`SQLiteMemoryStore.supersede()` performs both status UPDATE (`old_record` -> `SUPERSEDED`) and INSERT of `new_record` (referencing `old_record.memory_id` via `supersedes`) inside a single atomic SQLite transaction (`with self._connect() as connection:`). If INSERT fails, the transaction automatically rolls back, leaving `old_record` in its original `ACTIVE` state.

### ARCHIVE_BEHAVIOR
`DurableMemoryService.archive(project_id, memory_id)` transitions records to `ARCHIVED` status. Archived and superseded records remain physically stored in SQLite for auditing and are never deleted.

### RETRIEVAL_QUERY_CONTRACT
- `retrieve(project_id, query, *, limit=10, status_filter=MemoryStatus.ACTIVE, tier_filter=None, memory_type_filter=None, min_importance=0.0, include_global=False) -> tuple[MemoryMatch, ...]`.
- `project_id` is mandatory; querying with empty `project_id` raises `ValueError`.

### GLOBAL_MEMORY_BEHAVIOR & PROJECT_ISOLATION
- Preserves frozen Phase 03 canonical global memory contract: `project_id=None` and `scope="global"`.
- Internal SQLite column storage sentinel (`""` or `"__global__"`) is translated exclusively at the persistence boundary.
- Project A memories are strictly isolated; Project B can never retrieve Project A memories.
- `include_global=False`: returns only memories for the specified `project_id`.
- `include_global=True`: retrieves and re-ranks eligible memories from both `project_id` and global memories (`project_id=None`).

### RETRIEVAL_RESULT_CONTRACT & RANKING_MODEL
- Retrieval returns `MemoryMatch(record: MemoryRecord, score: float, reasons: tuple[str, ...])`.
- Ranking score combines lexical phrase bonus (0.5), token overlap bonus (0.1/term), importance (0.4), and confidence (0.1).
- Reasons provide deterministic facts only (e.g., `"exact_phrase"`, `"token_overlap:2"`, `"importance:0.85"`, `"project_scope"`).
- Ergonomic proxy properties and `__getattr__` delegate transparently to `record` for backwards compatibility.

### ZERO_MATCH_POLICY
Fail-closed policy using explicit boolean evidence:
Records with zero lexical match (`has_lexical_match = bool(has_exact_phrase or len(matched_terms) > 0)` is `False`) are strictly excluded for non-empty queries. No floating-point score comparisons are used for match detection. Pass empty string `""` for browse/filter mode.

### LARGE_STORE_RETRIEVAL_BEHAVIOR
Candidate preselection covers exact phrase OR any individual query token (`(content LIKE ? OR kind LIKE ?)` for each term) without cap, ensuring low-importance non-contiguous multi-token matches are never discarded behind unrelated high-importance records in stores with >200 memories.

### SQLITE_SCHEMA_CHANGES & MIGRATION_ERROR_POLICY
- Additive columns `status TEXT NOT NULL DEFAULT 'active'` and `logical_key TEXT`.
- Migration error policy catches `OperationalError` strictly for duplicate column conditions (`"duplicate column name"` / `"already exists"`). All unexpected SQLite errors propagate immediately.

### LEGACY_COMPATIBILITY
Pre-Phase-03 rows and Pre-Phase-04 rows lacking `status`/`logical_key` columns remain readable without data resets. `row["status"]` is the authoritative source of truth.

---

## REQUIRED BEHAVIORAL TEST MATRIX (40/40 PASSED)

Matrix tests in `tests/test_durable_memory_matrix.py` verify all core and hardening requirements:
- Survives re-instantiation, explicit consolidation, pure side-effect-free consolidation.
- Idempotent write on identical canonical semantics; rejection on altered subject, scope, tier, confidence, metadata, valid range.
- Direct store idempotency and IntegrityError propagation without broad INSERT OR IGNORE.
- Explicit conflict superseding and atomic rollback on failure.
- Status filters (ACTIVE, SUPERSEDED, ARCHIVED) and metadata/tier/importance filters.
- Project isolation and canonical global memory lifecycle (`project_id=None, scope="global"`).
- Non-semantic metadata allowlist (`__status_updated_at` allowed, arbitrary `__*` rejected).
- Rejection of reserved legacy sentinel `__global__` on new durable writes and MCP consolidate_memory.
- Backward-compatible deserialization of legacy rows with `project_id="__global__"` to `None`.
- Deterministic ranking with scores and reasons.
- Large-store >200 record retrieval for exact phrase and non-contiguous multi-token queries.
- Zero-match fail-closed filtering via explicit boolean evidence.
- MCP consolidate_memory durable persistence and strict project identity validation (`PROJECT_CONTEXT_REQUIRED`).
- Legacy row compatibility and physical storage retention.
- No ContextBuilder or UserModel side effects.

---

## TEST RESULTS

- **durable memory matrix:** **40 tests** (all passed)
- **unified memory model:** **17 tests** (all passed)
- **combined focused pair:** **57 tests** (all passed in 3.53s)
- **Full repository regression:** **792 passed in 14.62s**, 0 failed, 0 regressions.
- **git diff --check:** Completely clean (0 exit code).

---

## FILES_CHANGED

1. `core/memory.py`:
   - Canonical semantic equivalence helper `are_canonically_equivalent()` auditing all persistence-relevant fields with documented operational timestamp exclusion.
   - Explicit `NON_SEMANTIC_OPERATIONAL_METADATA_KEYS = frozenset({"__status_updated_at"})` allowlist replacing blanket `__*` filtering.
2. `core/durable_memory_service.py`:
   - Idempotency semantic equivalence check on write.
   - Canonical global memory sentinel `GLOBAL_PROJECT_ID = None`.
   - Rejection of reserved legacy sentinel `project_id="__global__"` on durable writes.
   - Retrieve federation using canonical `None` for global memory.
3. `persistence/sqlite_memory_store.py`:
   - Removal of broad `INSERT OR IGNORE`; narrow `IntegrityError` handling with re-read and canonical equivalence check.
   - Rejection of reserved legacy sentinel `project_id="__global__"` in `_add_many_sync` and `_supersede_sync`.
   - Backward-compatible deserialization of legacy rows where `project_id` is `"__global__"` to `None`.
   - Support `project_id: str | None` in `retrieve()`, `retrieve_scored()`, `get_by_logical_key()`, `_archive_record()`.
   - Multi-token candidate pre-selection in `_retrieve_scored_sync()`.
   - Explicit boolean lexical-match evidence for zero-match filtering.
4. `my_agent_mcp/server.py`:
   - Enforce explicit `project_id` or active project in `consolidate_memory()`; disallow `session_id` fallback and raise `PROJECT_CONTEXT_REQUIRED` / `ValueError`.
   - Rejection of explicit `project_id="__global__"` with `ValueError("RESERVED_SENTINEL: ...")`.
   - Durable persistence bridge via `_memory_service.consolidate_and_persist()`.
5. `tests/test_unified_memory_model.py`:
   - `test_17_canonical_semantic_equivalence_policy` unit tests verifying all 15 canonical semantic fields, operational allowlist (`__status_updated_at`), and semantic rejection of arbitrary `__*` keys (`__business_flag`).
6. `tests/test_durable_memory_matrix.py`:
   - Complete 40-test behavioral matrix verifying all 28 required gates and hardening specifications.
7. `docs/handoffs/PHASE_04_HANDOFF.md`:
   - Documented Phase 04 hardening and micro-hardening policies, test counts, and verification status.

---

## KNOWN_LIMITATIONS

- Lexical candidate scoring uses SQL LIKE and token overlap (pure SQLite, zero vector database dependencies).
- Memory consolidation is explicit via caller API; automatic background conversation extraction is intentionally deferred.

## BLOCKED_DEPENDENCIES

None.

## NEXT_PHASE_EXPECTATIONS

**PHASE 05 — User Model & Preference Specialization**
- Consume `MemoryType.PREFERENCE` records through `DurableMemoryService`.
- No modifications needed to canonical `MemoryRecord` or `SQLiteMemoryStore`.
