# PHASE 03 HANDOFF — Unified Memory Model

## PHASE

03 — Unified Memory Model

## BASE_COMMIT

`ac9b20cc02bcee4fe4ec4a9e6221efabdf988378` (origin/main, main)

## FINAL_COMMIT

`519c99907679cb9ed2d5e2c3f18d165dba343dd4`
`feat(memory): unify canonical memory domain model`

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

## FILES_CHANGED

### New files created:
1. `tests/test_unified_memory_model.py`: 12 comprehensive unit and behavioral tests verifying canonical model invariants, orthogonal dimensions, legacy compatibility, SQLite round-trip, global memory representation, and absence of pipeline side-effects.
2. `docs/cognitive_architecture/contracts/memory.md`: Frozen architectural contract for memory domain model.
3. `docs/handoffs/PHASE_03_HANDOFF.md`: This handoff document.

### Existing files modified (strictly within allowed boundary):
1. `core/memory.py`:
   - Defined `MemoryType`, `MemoryStatus`, `MemoryTier`.
   - Enhanced `MemoryRecord` to canonical form with full temporal, provenance, and lifecycle fields.
   - Preserved backward-compatible properties (`.level`, `.kind`) and constructor signatures.
   - Added `to_dict()`, `from_dict()`, and normalization helpers.
   - Added bidirectional adapters `legacy_entry_to_memory_record` and `memory_record_to_legacy_entry`.
2. `core/memory_consolidation.py`:
   - Marked `MemoryLevel` and `MemoryEntry` with `[DEPRECATED / LEGACY COMPATIBILITY ONLY]`.
   - Added `.to_canonical()` and `.from_canonical()` methods to `MemoryEntry`.
   - Added `consolidate_session_records()` to `MemoryConsolidator` returning canonical `MemoryRecord` instances.
   - Maintained `consolidate_session()` and `promote_memories()` for zero-breakage backward compatibility.
   - Strictly ensured zero automatic persistence side effects.
3. `persistence/sqlite_memory_store.py`:
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
- `persistence.sqlite_memory_store.SQLiteMemoryStore`:
  - Internal serialization and deserialization seamlessly round-trips canonical `MemoryRecord`.

---

## LEGACY_TYPES_RETAINED

- `core.memory_consolidation.MemoryEntry`: Retained strictly for backward compatibility with existing tests and callers. Marked as `[DEPRECATED / LEGACY COMPATIBILITY ONLY]`.
- `core.memory_consolidation.MemoryLevel`: Retained strictly for legacy mapping. Marked as `[DEPRECATED / LEGACY COMPATIBILITY ONLY]`.

---

## LEGACY_ADAPTERS

1. `legacy_entry_to_memory_record(entry, project_id=None) -> MemoryRecord`:
   - Maps `L0_episodic` -> `(MemoryType.EPISODIC, MemoryLevel.L0)`
   - Maps `L1_working` -> `(MemoryType.PROJECT, MemoryLevel.L1)`
   - Maps `L2_semantic` -> `(MemoryType.SEMANTIC, MemoryLevel.L2)`
   - Maps `L3_long_term` -> `(MemoryType.SEMANTIC, MemoryLevel.L3)`
   - Preserves `key`, `source`, `recall_count`, and timestamps.
2. `memory_record_to_legacy_entry(record) -> MemoryEntry`:
   - Reconstructs legacy `MemoryEntry` without data loss.

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

## PERSISTENCE_BEHAVIOR_BEFORE

- Stored simple `MemoryRecord` with basic `kind` and `level`.
- Did not persist or restore temporal or lifecycle metadata.

## PERSISTENCE_BEHAVIOR_AFTER

- Fully persists and restores canonical `MemoryRecord` instances.
- Completely non-destructive to historical records.

---

## TESTS_ADDED

File: `tests/test_unified_memory_model.py` (12 tests)
- `test_01_one_canonical_record`: Verifies single canonical `MemoryRecord` resolution and dict serialization.
- `test_02_type_tier_separation`: Verifies orthogonal nature of `MemoryType` and `MemoryLevel`.
- `test_03_legacy_memory_record_compatibility`: Verifies positional and keyword constructor compatibility with pre-Phase-03 code.
- `test_04_memory_entry_compatibility`: Verifies bidirectional conversion between `MemoryEntry` and `MemoryRecord`.
- `test_05_existing_sqlite_row_read`: Verifies reading legacy raw rows from SQLite database.
- `test_06_canonical_sqlite_round_trip`: Verifies round-trip persistence of all canonical fields via SQLite store.
- `test_07_global_non_project_memory`: Verifies representation and storage of global/non-project memories (`project_id=None`).
- `test_08_status_structure`: Verifies `ACTIVE`, `SUPERSEDED`, `ARCHIVED` status representation without auto-mutations.
- `test_09_supersedes_structure`: Verifies structural reference to older memories without conflict resolution side effects.
- `test_10_consolidator_output_contract`: Verifies `MemoryConsolidator.consolidate_session_records` returns canonical records.
- `test_11_no_pipeline_side_effect`: Verifies consolidation does NOT write to SQLite database.
- `test_12_validation_invariants`: Verifies validation rules for `memory_id`, `content`, `importance`, `confidence`, and `project_id`.

---

## TESTS_RUN & TEST_RESULTS

- **Targeted memory tests:** 21 passed in 0.64s (`test_unified_memory_model.py` + `test_memory_backend.py` + `test_memory_consolidation.py` + `test_memory_tools.py`).
- **Full repository suite:** **733 passed in 13.67s** (721 baseline + 12 new Phase 03 tests). Zero failures, zero regressions.

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
