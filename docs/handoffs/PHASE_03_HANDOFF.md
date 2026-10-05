# PHASE 03 HANDOFF — Unified Memory Model

## PHASE

03 — Unified Memory Model

## BASE_COMMIT

`ac9b20cc02bcee4fe4ec4a9e6221efabdf988378` (origin/main, main)

## INITIAL_PHASE_03_COMMIT

`50ec68fcacac91fa7722b53c281110aa3c003fc5`
`feat(memory): unify canonical memory domain model`

## COMMIT_DISCREPANCY_EXPLANATION

The human review observed that the reported CURRENT_COMMIT in chat (`50ec68f...`) differed from the string recorded inside `docs/handoffs/PHASE_03_HANDOFF.md` (`519c999...`).
**Root Cause:** In Git's Merkle tree model, a commit hash is computed over the entire tree state including all file contents. Updating the hash text inside `PHASE_03_HANDOFF.md` and running `git commit --amend` altered the tree contents, which deterministically generated a new commit SHA (`50ec68f...`). The commit history is linear and clean:
1. `b397a47`: initial Phase 03 implementation.
2. `519c999`: amend recording `b397a47`.
3. `50ec68f`: amend recording `519c999` (Actual HEAD reviewed).
4. Merge Gate Verification commit: applies hardening patches for Audits 1–4.

---

## OBJECTIVE

Establish ONE canonical memory domain model across the entire repository, strictly decoupling semantic memory type (`MemoryType`) from retention/storage tier (`MemoryTier` / `MemoryLevel`), maintaining full backward compatibility with legacy memory structures (`MemoryEntry`), and guaranteeing safe read/write compatibility with the existing SQLite persistence store without destructive migrations or data loss.

---

## PRE_PHASE_TEST_BASELINE

- 721 tests passing in 14.25s (from Phase 02 merge gate).
- Zero test failures or regressions.

---

## MEMORY_MODELS_FOUND_BEFORE

Prior to Phase 03, the repository suffered from architectural memory model fragmentation across two incompatible subsystems:
1. **`core/memory.py`:**
   - `MemoryLevel(StrEnum)`: `L0 = "l0"`, `L1 = "l1"`, `L2 = "l2"`, `L3 = "l3"`.
   - `MemoryRecord`: `memory_id, project_id, level, kind, content, importance, metadata, created_at`.
   - Used by `SQLiteMemoryStore`, `SQLiteMemoryBackend`, and coder runtime memory tools.
2. **`core/memory_consolidation.py`:**
   - `MemoryLevel(StrEnum)`: `L0_EPISODIC = "L0_episodic"`, `L1_WORKING = "L1_working"`, `L2_SEMANTIC = "L2_semantic"`, `L3_LONG_TERM = "L3_long_term"`.
   - `MemoryEntry`: `memory_id, level, key, content, source, recall_count, created_at, updated_at`.
   - Conflated semantic type and retention tier into a single enum; results from `consolidate_session()` were returned as in-memory `MemoryEntry` objects and incompatible with `SQLiteMemoryStore`.

---

## CANONICAL_MODEL_SELECTED

- **Canonical Record:** `core.memory.MemoryRecord`.
- **Properties:** Immutable slotted dataclass (`frozen=True, slots=True`), supporting complete temporal provenance, semantic typing, retention tiering, confidence scoring, lifecycle status, and superseded reference.

---

## CANONICAL_IMPORT_PATH

All future phases and memory consumers MUST import exclusively from:

```python
from core.memory import (
    MemoryRecord,
    MemoryType,
    MemoryLevel,
    MemoryTier,
    MemoryStatus,
    legacy_entry_to_memory_record,
    memory_record_to_legacy_entry,
    normalize_memory_type,
    normalize_memory_tier,
    normalize_memory_status,
)
```

---

## MEMORY_TYPE_VOCABULARY

Defined in `core.memory.MemoryType`:
- `EPISODIC` (`"episodic"`): Specific interaction or event that occurred.
- `SEMANTIC` (`"semantic"`): Generalized factual or conceptual knowledge.
- `PREFERENCE` (`"preference"`): Durable preference attributed to a subject.
- `PROJECT` (`"project"`): Project-specific decisions, conventions, constraints, or state.
- `LESSON` (`"lesson"`): Conclusion learned from an outcome or mistake.
- `PROCEDURE` (`"procedure"`): Reusable way or workflow for performing an operational task.

---

## MEMORY_TIER_VOCABULARY

Defined in `core.memory.MemoryLevel` (aliased as `MemoryTier`):
- `L0` (`"l0"`): Short-term / ephemeral session memory.
- `L1` (`"l1"`): Working task-scoped context.
- `L2` (`"l2"`): Project rules, codebase conventions, and architectural context.
- `L3` (`"l3"`): System-wide durable learned knowledge.

---

## TYPE_TIER_SEPARATION

Semantic type and retention tier are strictly orthogonal:
- A `PREFERENCE` memory can exist in `L2` (project scope) or `L3` (user global scope).
- An `EPISODIC` memory can be promoted from `L0` (chat message) to `L2` (durable milestone audit).
- A `LESSON` memory can be stored in `L1` (active task debugging) or `L3` (cross-project permanent rule).
No enum conflates these two concepts.

---

## L1_WORKING_MAPPING (AUDIT 1)

- **Before Verification:** `L1_WORKING` was unconditionally mapped to `MemoryType.PROJECT + MemoryLevel.L1`. This violated the `MEMORY TYPE != MEMORY TIER` invariant by conflating working context with project domain semantics.
- **After Verification:** `L1_WORKING` maps to `tier = MemoryLevel.L1`. For semantic type:
  - If `entry.key` contains explicit evidence (e.g. `"decision"`, `"constraint"`, `"architecture"`), it maps to `MemoryType.PROJECT`.
  - If `entry.key` contains explicit preference/lesson/procedure evidence, it maps to the matching type.
  - Otherwise, it maps to conservative fallback `MemoryType.SEMANTIC` with `metadata["legacy_compatibility_fallback"] = True`.
  - Original level string is always preserved in `metadata["legacy_level"] = "l1_working"`.

---

## LEGACY_INFORMATION_PRESERVATION (AUDIT 2)

All four legacy levels (`L0_EPISODIC`, `L1_WORKING`, `L2_SEMANTIC`, `L3_LONG_TERM`) preserve 100% of source information during roundtrip:
- `legacy_level`: stored in `metadata["legacy_level"]`
- `key`: stored in `metadata["key"]` and `_custom_kind`
- `source`: stored in `record.source` and `metadata["source"]`
- `recall_count`: stored in `metadata["recall_count"]`
- `created_at` and `updated_at`: preserved as datetimes
- `content`: preserved untouched
- `project_id`: preserved when provided

---

## CANONICAL_METADATA_NAMESPACE_POLICY (AUDIT 3)

- **Policy:** The key `"__canonical__"` within `metadata` is strictly reserved for internal persistence packaging by `SQLiteMemoryStore`.
- **Enforcement:** If a caller supplies `metadata` containing `"__canonical__"`, a `ValueError` is raised at `MemoryRecord.__init__` and verified again in `SQLiteMemoryStore._serialize_metadata`.
- **Guarantee:** Eliminates silent overwrite of caller metadata and prevents namespace collision.

---

## PROJECT_SCOPE_INVARIANT (AUDIT 4)

- If `project_id is None`: default scope is `"global"`. If caller passes contradictory `scope="project"`, it normalizes to `"global"`.
- If `project_id is not None`: default scope is `"project"`. Explicit custom scopes (e.g. `"workspace:ws-1"`) are preserved.

---

## FILES_CHANGED

### New files created:
1. `tests/test_unified_memory_model.py`: 16 comprehensive unit and behavioral tests verifying canonical model invariants, orthogonal dimensions, legacy compatibility, SQLite round-trip, global memory representation, reserved namespace validation, and scope consistency.
2. `docs/cognitive_architecture/contracts/memory.md`: Frozen architectural contract for memory domain model.
3. `docs/handoffs/PHASE_03_HANDOFF.md`: This handoff document.

### Existing files modified (strictly within allowed boundary):
1. `core/memory.py`:
   - Defined `MemoryType`, `MemoryStatus`, `MemoryTier`.
   - Enhanced `MemoryRecord` to canonical form with full temporal, provenance, and lifecycle fields.
   - Enforced reserved metadata key validation and scope consistency invariant.
   - Refined legacy adapters with conservative L1 fallback and bidirectional lossless conversion.
2. `core/memory_consolidation.py`:
   - Marked `MemoryLevel` and `MemoryEntry` with `[DEPRECATED / LEGACY COMPATIBILITY ONLY]`.
   - Added `.to_canonical()` and `.from_canonical()` methods to `MemoryEntry`.
   - Added `consolidate_session_records()` to `MemoryConsolidator` returning canonical `MemoryRecord` instances.
   - Maintained `consolidate_session()` and `promote_memories()` for zero-breakage backward compatibility.
   - Strictly ensured zero automatic persistence side effects.
3. `persistence/sqlite_memory_store.py`:
   - Added defensive check rejecting caller metadata collision with `"__canonical__"`.
   - Updated serialization to store canonical metadata inside `__canonical__` in `metadata_json`.
   - Handled non-project global memories (`project_id=None`) safely against SQLite `NOT NULL` constraint.
   - Implemented dual-path row deserializer restoring canonical records from `__canonical__` while gracefully reading historical rows.

---

## PUBLIC_INTERFACES_CREATED

- `core.memory.MemoryType` (StrEnum)
- `core.memory.MemoryTier` (Alias to MemoryLevel)
- `core.memory.MemoryStatus` (StrEnum)
- `core.memory.normalize_memory_type(val)`
- `core.memory.normalize_memory_tier(val)`
- `core.memory.normalize_memory_status(val)`
- `core.memory.legacy_entry_to_memory_record(entry, project_id=None)`
- `core.memory.memory_record_to_legacy_entry(record)`
- `core.memory_consolidation.MemoryConsolidator.consolidate_session_records(context, messages)`

---

## PUBLIC_INTERFACES_MODIFIED

- `core.memory.MemoryRecord`:
  - Added fields: `confidence`, `subject`, `scope`, `source`, `status`, `supersedes`, `updated_at`, `last_accessed_at`, `valid_from`, `valid_until`.
  - Added properties: `.level` (aliasing `.tier`), `.kind` (aliasing legacy kind or `.memory_type.value`).
  - Added methods: `to_dict()`, `from_dict()`.
  - Added validations: reserved `"__canonical__"` check, scope consistency normalization.
- `persistence.sqlite_memory_store.SQLiteMemoryStore`:
  - Internal serialization and deserialization seamlessly round-trips canonical `MemoryRecord` with reserved namespace guard.

---

## SQLITE_SCHEMA_CHANGES

- **NONE.**
- Zero schema migrations executed.
- No `DROP TABLE`, `DROP COLUMN`, or column renames.
- `memories` table schema remains:
  `memory_id, project_id, level, kind, content, importance, metadata_json, created_at`.

---

## SQLITE_COMPATIBILITY

- **Backward Compatibility (Old Rows):** Historical rows lacking `__canonical__` in `metadata_json` are deserialized with default canonical metadata (`confidence=1.0`, `status=ACTIVE`, normalized `memory_type` from `kind`).
- **Forward Compatibility (New Rows):** Canonical records are stored with standard column values (`level`, `kind`, `content`, `importance`) plus full canonical metadata in `__canonical__`.
- **Global Memory (`project_id=None`):** Written as empty string `""` in SQLite column to satisfy `NOT NULL` constraint, restored as `project_id=None` via `__canonical__`.

---

## MEMORY_CONSOLIDATOR_BEFORE

- Returned only `MemoryEntry` objects.
- Disconnected from `SQLiteMemoryStore`.
- No canonical model integration.

## MEMORY_CONSOLIDATOR_AFTER

- Retains `consolidate_session()` returning `MemoryEntry` for legacy callers.
- Adds `consolidate_session_records()` returning canonical `MemoryRecord` instances.
- Zero persistence side effects (operates strictly in-memory; wiring deferred to Phase 04).

---

## TESTS_ADDED

File: `tests/test_unified_memory_model.py` (16 tests)
- `test_01_one_canonical_record`: Verifies single canonical `MemoryRecord` resolution and dict serialization.
- `test_02_type_tier_separation`: Verifies orthogonal nature of `MemoryType` and `MemoryLevel`.
- `test_03_legacy_memory_record_compatibility`: Verifies positional and keyword constructor compatibility with pre-Phase-03 code.
- `test_04_memory_entry_compatibility`: Verifies bidirectional conversion between `MemoryEntry` and `MemoryRecord`.
- `test_05_existing_sqlite_row_read`: Verifies reading legacy raw rows from SQLite database.
- `test_06_canonical_sqlite_round_trip`: Verifies round-trip persistence of all canonical fields via SQLite store.
- `test_07_global_non_project_memory`: Verifies representation and storage of global/non-project memories (`project_id=None`).
- `test_08_status_structure`: Verifies `ACTIVE`, `SUPERSEDED`, `ARCHIVED` status representation without auto-mutations.
- `test_09_supersedes_structure`: Verifies structural reference to older memories without conflict resolution side effects.
- `test_10_consolidator_output_contract`: Verifies `MemoryConsolidator.consolidate_session_records` returns canonical records with conservative fallback.
- `test_11_no_pipeline_side_effect`: Verifies consolidation does NOT write to SQLite database.
- `test_12_validation_invariants`: Verifies validation rules for `memory_id`, `content`, `importance`, `confidence`, and `project_id`.
- `test_13_audit1_l1_working_conservative_fallback`: Verifies `L1_working` does not claim `PROJECT` without explicit evidence.
- `test_14_audit2_all_four_legacy_levels_preservation`: Verifies lossless roundtrip across all 4 legacy levels.
- `test_15_audit3_reserved_canonical_metadata_rejected`: Verifies rejection of reserved `"__canonical__"` key in metadata.
- `test_16_audit4_project_id_scope_consistency`: Verifies non-contradictory scope consistency for global and project memories.

---

## TESTS_RUN & TEST_RESULTS

- **Targeted memory tests:** 25 passed in 0.75s (`test_unified_memory_model.py` + `test_memory_backend.py` + `test_memory_consolidation.py` + `test_memory_tools.py`).
- **Full repository suite:** **737 passed in 14.52s** (721 baseline + 16 new Phase 03 tests). Zero failures, zero regressions.


---

## BEHAVIOR_VERIFIED

1. Exactly one canonical `MemoryRecord` exists in `core.memory`.
2. `MemoryType` and `MemoryTier` are decoupled, independent dimensions.
3. Historical rows in SQLite remain readable as canonical `MemoryRecord`.
4. Canonical `MemoryRecord` round-trips through SQLite without data loss.
5. Legacy `MemoryEntry` maps bidirectionally to `MemoryRecord`.
6. `MemoryConsolidator` produces canonical `MemoryRecord` in-memory without persistence side-effects.

---

## EXPLICIT QUESTIONS ANSWERED

- **Is there exactly one canonical MemoryRecord?** YES.
- **Is MemoryEntry still present?** YES.
- **If yes, why?** Retained exclusively for backward compatibility with existing tests and callers without breaking runtime contracts.
- **Is it marked legacy?** YES (`[DEPRECATED / LEGACY COMPATIBILITY ONLY]`).
- **Can old SQLite rows still be read?** YES (100% readable as valid canonical `MemoryRecord`).
- **Was any existing memory data destroyed?** NO.
- **Does MemoryConsolidator persist automatically now?** NO (strictly deferred to Phase 04).
- **Was ContextBuilder implemented?** NO (strictly deferred to Phase 06).
- **Was UserModel implemented?** NO (strictly deferred to Phase 05).
- **Was vector search implemented?** NO (strictly deferred to Phase 04).

---

## KNOWN_LIMITATIONS

- Memory search in SQLite continues using substring `LIKE` matching; semantic ranking and vector indexing are not yet present (deferred to Phase 04).
- `supersedes` field is purely structural; conflict resolution and automated invalidation are not executed in Phase 03.
- `MemoryConsolidator` does not persist consolidated records to disk (by architectural design; Phase 04 responsibility).

---

## BLOCKED_DEPENDENCIES

None.

---

## FOLLOW_UP_ISSUES

- In Phase 04: Connect `MemoryConsolidator` to `SQLiteMemoryStore` via durable lifecycle pipeline and implement retrieval ranking.
- In Phase 05: Build `UserModel` using `MemoryType.PREFERENCE` without polluting the memory domain model.
- In Phase 06: Implement `ContextBuilder` to inject retrieved memories into LLM prompt contexts.

---

## FORBIDDEN_FUTURE_ASSUMPTIONS

1. Do NOT assume that `PREFERENCE` memory type implies `UserModel` has been built.
2. Do NOT assume that `LESSON` memory type implies automated reflection/induction exists.
3. Do NOT assume that `PROCEDURE` memory type implies skill promotion exists.
4. Do NOT assume that `supersedes` field automatically deletes or updates older records.
5. Do NOT create a second memory representation in any future phase.

---

## NEXT_PHASE_EXPECTATIONS

The next phase after human review is:
**PHASE 04 — DURABLE MEMORY PIPELINE**
- Wire `MemoryConsolidator` to persistence.
- Implement memory retrieval service (ranking, filtering, search).
- Implement conflict/superseding transition lifecycle.
