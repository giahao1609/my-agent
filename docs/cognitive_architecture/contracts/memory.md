# Contract: Memory Domain Model

**Frozen at Phase 03 — Unified Memory Model**  
**Source:** `core/memory.py`, `persistence/sqlite_memory_store.py`, `core/memory_consolidation.py`

---

## 1. Objective & Invariants

This contract defines the canonical domain model for all memory-related subsystems across the Houhou Cognitive Architecture.

### Core Invariants:
1. **Single Canonical Domain Model:** Every future cognitive phase MUST consume `MemoryRecord` exclusively from `core.memory`. No new competing memory classes may be introduced.
2. **Type vs. Tier Orthogonality:** Semantic memory type (`MemoryType`) and retention/storage tier (`MemoryTier` / `MemoryLevel`) are strictly orthogonal dimensions. They MUST NOT be conflated into a single enum.
3. **Additive Persistence Compatibility:** The existing SQLite `memories` table schema remains unchanged. Canonical fields are preserved via additive JSON packaging (`__canonical__` in `metadata_json`), ensuring zero data loss and seamless reading of historical rows.
4. **Boundary Guardrails:** Structural definition does NOT imply operational subsystem execution.
   - `PREFERENCE memory != UserModel`
   - `LESSON memory != Reflection system`
   - `PROCEDURE memory != Skill system`
   - `Canonical MemoryRecord existence != working long-term memory pipeline`

---

## 2. Canonical Import Path

All downstream consumers and future phases MUST import the canonical memory abstractions from `core.memory`:

```python
from core.memory import (
    MemoryRecord,
    MemoryType,
    MemoryLevel,
    MemoryTier,
    MemoryStatus,
    normalize_memory_type,
    normalize_memory_tier,
    normalize_memory_status,
)
```

Imports from `core.memory_consolidation.MemoryEntry` or `core.memory_consolidation.MemoryLevel` are strictly deprecated and reserved for legacy compatibility tests only.

---

## 3. Canonical Vocabulary

### 3.1 Semantic Memory Types (`MemoryType`)

| Type | String Value | Semantic Definition |
|---|---|---|
| `EPISODIC` | `"episodic"` | A specific interaction or event that occurred at a point in time. |
| `SEMANTIC` | `"semantic"` | A generalized factual, architectural, or conceptual piece of knowledge. |
| `PREFERENCE` | `"preference"` | A durable preference attributed to a specific subject (user, agent, etc.). |
| `PROJECT` | `"project"` | Project-specific decisions, conventions, constraints, or state worth retaining. |
| `LESSON` | `"lesson"` | A conclusion learned from a prior mistake, failure, or outcome. |
| `PROCEDURE` | `"procedure"` | A reusable sequence or standard workflow for performing an operational task. |

### 3.2 Retention Tiers (`MemoryTier` / `MemoryLevel`)

`MemoryTier` is a canonical alias for `MemoryLevel`:

| Tier | Value | Classification Intent | Scope |
|---|---|---|---|
| `L0` | `"l0"` | Short-term / ephemeral | Active chat session, transient messages |
| `L1` | `"l1"` | Working memory | Task / plan execution scope |
| `L2` | `"l2"` | Project durable memory | Project boundaries, codebase conventions |
| `L3` | `"l3"` | Long-term durable memory | Cross-project, system-wide durable knowledge |

### 3.3 Lifecycle Status (`MemoryStatus`)

| Status | String Value | Meaning |
|---|---|---|
| `ACTIVE` | `"active"` | Currently valid and active memory record. |
| `SUPERSEDED` | `"superseded"` | Outdated by a newer memory record referenced via `supersedes`. |
| `ARCHIVED` | `"archived"` | Preserved for historical audit, not considered active knowledge. |

---

## 4. Canonical `MemoryRecord` Specification

```python
@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    content: str
    project_id: str | None = None
    memory_type: MemoryType = MemoryType.SEMANTIC
    tier: MemoryLevel = MemoryLevel.L1
    importance: float = 0.5
    confidence: float = 1.0
    subject: str = "project"
    scope: str = "project"
    source: str = "runtime"
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
```

### 4.1 Backward Compatibility Properties
- `record.level`: Read-only property returning `record.tier` (`MemoryLevel`).
- `record.kind`: Read-only property returning legacy kind string (e.g. `"decision"`, `"note"`) or `record.memory_type.value`.

### 4.2 Field Semantics & Consistency Invariants
- `memory_id`: Non-empty stable unique identifier.
- `project_id`: Optional project scoping. Set to `None` for global, non-project memories.
- `scope`: Logical scope boundary.
  - **Consistency Invariant:** If `project_id` is `None`, default scope is `"global"` (never contradictory `"project"`). If caller passes `project_id=None` with `scope="project"`, it normalizes to `"global"`. If `project_id` is provided, default scope is `"project"`.
- `memory_type`: Semantic classification (`EPISODIC`, `SEMANTIC`, `PREFERENCE`, etc.).
- `tier`: Storage intent tier (`L0`, `L1`, `L2`, `L3`).
- `content`: Non-empty text content of the remembered information.
- `importance`: Float normalized in `[0.0, 1.0]`.
- `confidence`: Float normalized in `[0.0, 1.0]`, expressing certainty of accuracy.
- `subject`: Entity the memory concerns (e.g., `"user"`, `"houhou"`, `"project"`, `"system"`).
- `source`: Provenance of memory (e.g., `"conversation"`, `"runtime"`, `"manual"`, `"migration"`).
- `status`: Structural lifecycle state (`ACTIVE`, `SUPERSEDED`, `ARCHIVED`).
- `supersedes`: Identifier of an older memory replaced by this record (structural reference).
- `metadata`: Arbitrary user/system key-value metadata mapping.
- `created_at`: Creation timestamp with timezone UTC.
- `updated_at`, `last_accessed_at`, `valid_from`, `valid_until`: Optional ISO-8601 temporal fields.

---

## 5. Persistence & SQLite Compatibility Strategy

### 5.1 Storage Architecture
The existing SQLite `memories` table schema is preserved without destructive migrations:

```sql
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 5.2 Additive Packaging
To support canonical fields without altering SQLite columns:
- Canonical metadata fields (`memory_type`, `confidence`, `subject`, `scope`, `source`, `status`, `supersedes`, `updated_at`, etc.) are packaged into `metadata_json` under the key `__canonical__`.
- When `project_id` is `None` (global memory), it is written to SQLite column `project_id` as `""` (empty string) to satisfy SQLite `NOT NULL` constraint, while `None` is preserved in `__canonical__["project_id"]`.
- When reading rows:
  - If `__canonical__` exists, full canonical semantics are restored.
  - If `__canonical__` is absent (legacy row created prior to Phase 03), safe defaults are supplied: `tier=MemoryLevel(row["level"])`, `memory_type=normalize_memory_type(row["kind"])`, `confidence=1.0`, `status=ACTIVE`.

### 5.3 Reserved Metadata Namespace Policy
- The key `"__canonical__"` within `metadata` is strictly reserved for internal persistence packaging in `SQLiteMemoryStore`.
- Any caller-supplied `metadata` containing `"__canonical__"` is rejected with `ValueError` at both `MemoryRecord.__init__` and `SQLiteMemoryStore._serialize_metadata`.
- This ensures zero risk of silent caller data loss or collision.

---

## 6. Legacy Adapters (`core/memory_consolidation.py`)

Historical code used `MemoryEntry` with conflated levels (e.g. `L0_episodic`, `L2_semantic`).
Compatibility adapters are exposed:

```python
# Adapter 1: Legacy -> Canonical
canonical_record = legacy_entry_to_memory_record(legacy_entry, project_id=...)

# Adapter 2: Canonical -> Legacy
legacy_entry = memory_record_to_legacy_entry(canonical_record)
```

### 6.1 Legacy L1_WORKING Semantics
- `L1_WORKING` represents a retention tier (`MemoryLevel.L1`), NOT an unsupported semantic claim of `MemoryType.PROJECT`.
- If explicit semantic evidence is present in `entry.key` (e.g. `"decision"`, `"constraint"`), it is mapped accordingly.
- Otherwise, the adapter applies a conservative compatibility fallback: `MemoryType.SEMANTIC` with `tier=MemoryLevel.L1`, recording `legacy_compatibility_fallback: True` and `legacy_level: "l1_working"` in `metadata`.

### 6.2 Provenance & Information Preservation
For all four legacy levels (`L0_EPISODIC`, `L1_WORKING`, `L2_SEMANTIC`, `L3_LONG_TERM`), conversion preserves:
- Original legacy level string (in `metadata["legacy_level"]`)
- `key` (in `metadata["key"]` and `_custom_kind`)
- `source` (in `record.source` and `metadata["source"]`)
- `recall_count` (in `metadata["recall_count"]`)
- `created_at` and `updated_at`
- `content`
- `project_id` when available

`MemoryConsolidator.consolidate_session_records(context, messages)` outputs canonical `MemoryRecord` instances directly using these adapters.

---

## 7. Deferred Responsibilities (Phase 04+)

The following capabilities are intentionally NOT implemented in Phase 03:
1. **Durable Memory Pipeline:** Automatic persistence of consolidated session memories (Phase 04).
2. **Retrieval Engine & Ranking:** Vector search, semantic embeddings, hybrid BM25 + vector ranking (Phase 04).
3. **Decay & Forgetting:** Automated TTL eviction, access-based decay (Phase 04).
4. **Conflict Resolution:** Automated evaluation of contradictory statements and superseding transitions (Phase 04).
5. **User Model (`UserModel`):** User profiles, preferences reasoning (Phase 05).
6. **Context Builder (`ContextBuilder`):** Automatic memory injection into LLM prompts (Phase 06).

