# PHASE 04 HANDOFF — Durable Memory Pipeline

## PHASE

04 — Durable Memory Pipeline

## BASE_COMMIT

`d0aa10fb42bdf326bbee6b1c558c8855ccac026a` (origin/main, main after Phase 03 merge)

## BRANCH

`feat/houhou-04-durable-memory-pipeline`

---

## OBJECTIVE

Establish a durable memory service for persistence, retrieval, and lifecycle
management (superseding / archiving) of canonical `MemoryRecord` objects,
building on the Phase 03 domain model without modifying any frozen contract.

Explicit non-goals (respected throughout):
- No vector database or embedding model.
- No background daemon or automatic memory extraction.
- No UserModel, ContextBuilder, Reflection, or any Phase 05+ concern.
- No destructive SQLite migrations (zero data loss guarantee maintained).

---

## PRE_PHASE_TEST_BASELINE

- 737 tests passing (Phase 03 merge gate).
- Zero test failures or regressions.

---

## DEFINITION_OF_DONE — STATUS

| ID | Requirement | Status |
|----|-------------|--------|
| D1 | `write()` persists records to SQLite | PASS |
| D2 | `retrieve()` returns ACTIVE records with relevance scoring | PASS |
| D3 | Superseding transition is atomic (old=SUPERSEDED, new=ACTIVE) | PASS |
| D4 | `logical_key` conflict detection auto-supersedes stale ACTIVE records | PASS |
| D5 | Persistence survives store re-instantiation | PASS |
| D6 | Tier, memory_type, status, min_importance filters work correctly | PASS |
| D7 | `archive()` transitions record to ARCHIVED, hides from ACTIVE retrieval | PASS |
| D8 | `write([])` is a no-op | PASS |
| D9 | `retrieve()` without project_id raises ValueError | PASS |
| D10 | `supersede()` rejects mismatched supersedes reference | PASS |
| D11 | `retrieve(limit=0)` returns empty tuple | PASS |
| D12 | Pre-Phase-04 legacy rows survive additive schema migration | PASS |

All 14 Definition of Done tests: **PASSED**.
Full repository suite: **751 passed, 0 failed** (737 baseline + 14 new).

---

## FILES_CHANGED

### New files created:
1. `core/durable_memory_service.py` — `DurableMemoryService` class (single orchestration boundary).
2. `tests/test_durable_memory_pipeline.py` — 14 DoD tests.
3. `docs/handoffs/PHASE_04_HANDOFF.md` — This document.

### Existing files modified:
1. `persistence/sqlite_memory_store.py`:
   - Additive schema migration: `ALTER TABLE memories ADD COLUMN status` (DEFAULT `'active'`) and `ADD COLUMN logical_key` (nullable). Idempotent via `try/except OperationalError`.
   - Two new indexes: `idx_memories_status`, `idx_memories_logical_key`.
   - `add_many()`: now writes `status` and `logical_key` columns alongside existing columns.
   - `retrieve()` + `_retrieve_sync()`: filtered + scored retrieval (status, tier, min_importance SQL filters; memory_type post-filter; phrase/term/importance/confidence scoring).
   - `supersede()` + `_supersede_sync()`: atomic UPDATE+INSERT in one SQLite transaction.
   - `get_by_logical_key()` + `_get_by_logical_key_sync()`: lookup by explicit logical_key.
   - `_archive_record()` + `_archive_record_sync()`: status UPDATE to ARCHIVED.

---

## ARCHITECTURE_DECISIONS

### D1 — Single Service Boundary
`DurableMemoryService` is the one and only orchestration point for all durable
memory operations.  Neither `SQLiteMemoryStore` nor any caller bypasses it.
`SQLiteMemoryBackend` (integration layer) remains unchanged and continues to
serve legacy `capture/recall` operations unchanged.

### D2 — Additive Migration Pattern
No `DROP TABLE`, no `DROP COLUMN`, no destructive migrations.
The two new columns (`status`, `logical_key`) are added via `ALTER TABLE … ADD COLUMN`
guarded by `try/except OperationalError`, making `initialize()` fully idempotent.
Pre-Phase-04 rows inherit `status = 'active'` from the column DEFAULT.

### D3 — Atomic Superseding
The `_supersede_sync()` method performs both the UPDATE (mark old as SUPERSEDED)
and the INSERT (new record) within the same `sqlite3.Connection` context manager
(implicit transaction).  If the INSERT fails, the UPDATE is rolled back.

### D4 — Relevance Scoring (No Vector Database)
Scoring formula per record:
```
score = phrase_bonus(0.5) + term_bonus(sum 0.1/term) + importance*0.4 + confidence*0.1
```
Candidate set: `min(limit * 4, 200)` rows from SQL (ordered by importance DESC),
then Python-side re-ranked by score.  This provides relevance ordering without
any external embedding model.

### D5 — Conflict Resolution via `logical_key`
Conflict detection is explicit and opt-in:
- Caller includes `{"logical_key": "some_key"}` in `record.metadata`.
- `write()` queries `get_by_logical_key()` for ACTIVE records with the same key.
- If found, atomic supersede is executed; otherwise plain insert.
- No natural language inference or automatic key generation.

---

## SQLite SCHEMA AFTER PHASE 04

```sql
CREATE TABLE memories (
    memory_id     TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    level         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    content       TEXT NOT NULL,
    importance    REAL NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- Phase 04 addition
    logical_key   TEXT                              -- Phase 04 addition (nullable)
);

CREATE INDEX idx_memories_project      ON memories(project_id, created_at);
CREATE INDEX idx_memories_lookup       ON memories(project_id, kind, importance);
CREATE INDEX idx_memories_status       ON memories(project_id, status, importance);   -- Phase 04
CREATE INDEX idx_memories_logical_key  ON memories(project_id, logical_key);          -- Phase 04
```

---

## PUBLIC_INTERFACES_CREATED

### `core.durable_memory_service.DurableMemoryService`

```python
class DurableMemoryService:
    def __init__(self, store: SQLiteMemoryStore) -> None: ...

    async def write(
        self,
        records: Sequence[MemoryRecord],
        *,
        resolve_conflicts: bool = True,
    ) -> tuple[str, ...]: ...

    async def retrieve(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 10,
        status_filter: MemoryStatus | None = MemoryStatus.ACTIVE,
        tier_filter: MemoryLevel | None = None,
        memory_type_filter: MemoryType | None = None,
        min_importance: float = 0.0,
    ) -> tuple[MemoryRecord, ...]: ...

    async def archive(
        self,
        project_id: str,
        memory_id: str,
    ) -> None: ...
```

### `persistence.sqlite_memory_store.SQLiteMemoryStore` — new methods:

```python
async def retrieve(project_id, query, *, limit, status_filter, tier_filter,
                   memory_type_filter, min_importance) -> tuple[MemoryRecord, ...]
async def supersede(old_memory_id, new_record) -> None
async def get_by_logical_key(project_id, logical_key, *, status_filter) -> tuple[MemoryRecord, ...]
```

---

## INVARIANTS_PRESERVED

1. `MemoryRecord` canonical model (Phase 03) is consumed exclusively.
2. `memories` table schema extended additively only — zero data loss.
3. Pre-Phase-03 legacy rows (no `__canonical__` in `metadata_json`) remain readable.
4. Pre-Phase-04 rows (no `status` / `logical_key` columns) readable via DEFAULT migration.
5. `SQLiteMemoryBackend.capture()` / `.recall()` continue to work unchanged.
6. `MemoryConsolidator` has no persistence side effects (unchanged from Phase 03).
7. Reserved `__canonical__` metadata namespace policy (Phase 03) is intact.

---

## TESTS_RUN

- **Phase 04 targeted:** 14 passed in 0.34s (`tests/test_durable_memory_pipeline.py`).
- **Full repository:** **751 passed in 12.54s**. Zero failures. Zero regressions.

---

## KNOWN_LIMITATIONS

- Memory search candidate pre-fetching is capped at 200 rows. For very large
  stores (> 200 ACTIVE records), the highest-importance records may dominate
  the candidate set, potentially missing lower-importance phrase matches.
  This is acceptable for the current scale and deferred to Phase 04+ optimisation.
- `retrieve()` does not hard-exclude records with zero query-term matches;
  if the candidate set is small, all ACTIVE records are returned and scored.
  This is by design for the no-vector-database constraint.

---

## BLOCKED_DEPENDENCIES

None.

---

## FOLLOW_UP_ISSUES

- Phase 05: Build `UserModel` using `MemoryType.PREFERENCE` records via `DurableMemoryService`.
- Phase 06: Build `ContextBuilder` to inject retrieved canonical memories into LLM prompt contexts.
- Phase 04+: Consider BM25 scoring upgrade or Tantivy-based FTS for large stores.

---

## FORBIDDEN_FUTURE_ASSUMPTIONS

1. Do NOT assume `DurableMemoryService.write()` performs automatic memory
   extraction from conversations (that is a caller concern).
2. Do NOT assume `logical_key` conflict resolution uses NLP or embedding similarity.
3. Do NOT introduce a second memory pipeline or competing service class.
4. Do NOT modify the `memories` table schema destructively.
5. Do NOT assume `SUPERSEDED` records are deleted — they are preserved for audit.

---

## NEXT_PHASE_EXPECTATIONS

**PHASE 05 — User Model**
- Build `UserModel` consuming `MemoryType.PREFERENCE` records from `DurableMemoryService`.
- No modification of `MemoryRecord`, `DurableMemoryService`, or SQLite schema required.
