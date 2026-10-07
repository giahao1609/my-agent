from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.context import ExecutionContext
from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryTier,
    MemoryType,
    legacy_entry_to_memory_record,
    memory_record_to_legacy_entry,
    normalize_memory_status,
    normalize_memory_tier,
    normalize_memory_type,
)
from core.memory_consolidation import (
    MemoryConsolidator,
    MemoryEntry,
    MemoryLevel as LegacyMemoryLevel,
)
from persistence.sqlite_memory_store import SQLiteMemoryStore


def test_01_one_canonical_record() -> None:
    """TEST 1 — ONE CANONICAL RECORD:
    
    Verify future-facing memory components resolve to one canonical MemoryRecord model.
    """
    record = MemoryRecord(
        memory_id="mem-can-1",
        content="Canonical representation test",
        memory_type=MemoryType.SEMANTIC,
        tier=MemoryLevel.L1,
        project_id="proj-1",
    )

    assert isinstance(record, MemoryRecord)
    assert record.memory_id == "mem-can-1"
    assert record.content == "Canonical representation test"
    assert record.memory_type == MemoryType.SEMANTIC
    assert record.tier == MemoryLevel.L1
    assert record.level == MemoryLevel.L1
    assert record.tier is MemoryTier.L1

    # Verify serialization roundtrip on canonical record
    serialized = record.to_dict()
    assert serialized["memory_type"] == "semantic"
    assert serialized["tier"] == "l1"
    restored = MemoryRecord.from_dict(serialized)
    assert restored == record


def test_02_type_tier_separation() -> None:
    """TEST 2 — TYPE / TIER SEPARATION:
    
    Verify semantic type and retention tier are strictly orthogonal dimensions.
    """
    # Preference in durable L3 tier
    pref_l3 = MemoryRecord(
        memory_id="pref-1",
        content="User prefers strict typing",
        memory_type=MemoryType.PREFERENCE,
        tier=MemoryLevel.L3,
    )
    assert pref_l3.memory_type == MemoryType.PREFERENCE
    assert pref_l3.tier == MemoryLevel.L3

    # Episodic event in project-level L2 tier
    ep_l2 = MemoryRecord(
        memory_id="ep-1",
        content="Architecture discussion on 2026-10-05",
        memory_type=MemoryType.EPISODIC,
        tier=MemoryLevel.L2,
    )
    assert ep_l2.memory_type == MemoryType.EPISODIC
    assert ep_l2.tier == MemoryLevel.L2

    # Procedure in session-level L0 tier
    proc_l0 = MemoryRecord(
        memory_id="proc-1",
        content="Scratch debug command sequence",
        memory_type=MemoryType.PROCEDURE,
        tier=MemoryLevel.L0,
    )
    assert proc_l0.memory_type == MemoryType.PROCEDURE
    assert proc_l0.tier == MemoryLevel.L0

    # Lesson in L1 working tier
    lesson_l1 = MemoryRecord(
        memory_id="les-1",
        content="Avoid blocking event loops during file I/O",
        memory_type=MemoryType.LESSON,
        tier=MemoryLevel.L1,
    )
    assert lesson_l1.memory_type == MemoryType.LESSON
    assert lesson_l1.tier == MemoryLevel.L1


def test_03_legacy_memory_record_compatibility() -> None:
    """TEST 3 — LEGACY MEMORYRECORD COMPATIBILITY:
    
    Verify pre-Phase-03 instantiation signatures (positional and keyword) work seamlessly.
    """
    dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    # Positional instantiation matching old signature:
    # MemoryRecord(memory_id, project_id, level, kind, content, importance, metadata, created_at)
    legacy_pos = MemoryRecord(
        "mem-leg-pos",
        "proj-alpha",
        MemoryLevel.L2,
        "decision",
        "Use SQLite for local persistence",
        0.85,
        {"author": "tech-lead"},
        dt,
    )
    assert legacy_pos.memory_id == "mem-leg-pos"
    assert legacy_pos.project_id == "proj-alpha"
    assert legacy_pos.level == MemoryLevel.L2
    assert legacy_pos.tier == MemoryLevel.L2
    assert legacy_pos.kind == "decision"
    assert legacy_pos.memory_type == MemoryType.PROJECT
    assert legacy_pos.content == "Use SQLite for local persistence"
    assert legacy_pos.importance == 0.85
    assert legacy_pos.metadata == {"author": "tech-lead"}
    assert legacy_pos.created_at == dt

    # Keyword instantiation matching old signature
    legacy_kw = MemoryRecord(
        memory_id="mem-leg-kw",
        project_id="proj-beta",
        level=MemoryLevel.L0,
        kind="note",
        content="Temporary scratchpad note",
        importance=0.3,
    )
    assert legacy_kw.memory_id == "mem-leg-kw"
    assert legacy_kw.project_id == "proj-beta"
    assert legacy_kw.level == MemoryLevel.L0
    assert legacy_kw.kind == "note"
    assert legacy_kw.memory_type == MemoryType.SEMANTIC
    assert legacy_kw.importance == 0.3


def test_04_memory_entry_compatibility() -> None:
    """TEST 4 — MEMORYENTRY COMPATIBILITY:
    
    Legacy MemoryEntry maps explicitly to canonical MemoryRecord and back, preserving both type and tier.
    """
    entry = MemoryEntry(
        memory_id="entry-100",
        level=LegacyMemoryLevel.L0_EPISODIC,
        key="chat_summary",
        content="Discussed phase roadmap",
        source="session:s-1",
        recall_count=4,
    )

    canonical = legacy_entry_to_memory_record(entry, project_id="proj-100")
    assert canonical.memory_id == "entry-100"
    assert canonical.content == "Discussed phase roadmap"
    assert canonical.memory_type == MemoryType.EPISODIC
    assert canonical.tier == MemoryLevel.L0
    assert canonical.source == "session:s-1"
    assert canonical.metadata["key"] == "chat_summary"
    assert canonical.metadata["recall_count"] == 4

    # Convert back via adapter
    restored_entry = memory_record_to_legacy_entry(canonical)
    assert restored_entry.memory_id == entry.memory_id
    assert restored_entry.level == LegacyMemoryLevel.L0_EPISODIC
    assert restored_entry.key == "chat_summary"
    assert restored_entry.content == entry.content
    assert restored_entry.source == entry.source
    assert restored_entry.recall_count == 4

    # Method-based conversion on entry instance
    canonical_from_method = entry.to_canonical(project_id="proj-100")
    assert canonical_from_method.memory_type == MemoryType.EPISODIC
    assert canonical_from_method.tier == MemoryLevel.L0


@pytest.mark.asyncio
async def test_05_existing_sqlite_row_read(tmp_path: Path) -> None:
    """TEST 5 — EXISTING SQLITE ROW READ:
    
    Insert a raw row matching the pre-Phase-03 memories table schema.
    SQLiteMemoryStore must return a valid canonical MemoryRecord without data loss or reset.
    """
    db_path = tmp_path / "legacy_test.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()

    # Raw insert simulating data written by pre-Phase-03 code
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO memories (
                memory_id,
                project_id,
                level,
                kind,
                content,
                importance,
                metadata_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-row-1",
                "legacy-proj",
                "l2",
                "decision",
                "Use Rust for CPU-intensive modules.",
                0.9,
                json.dumps({"original_author": "alice"}),
                "2026-02-01T10:00:00+00:00",
            ),
        )

    # Read through SQLiteMemoryStore
    results = await store.search("legacy-proj", "Rust", limit=10)
    assert len(results) == 1
    record = results[0]

    assert isinstance(record, MemoryRecord)
    assert record.memory_id == "legacy-row-1"
    assert record.project_id == "legacy-proj"
    assert record.level == MemoryLevel.L2
    assert record.tier == MemoryLevel.L2
    assert record.kind == "decision"
    assert record.memory_type == MemoryType.PROJECT
    assert record.content == "Use Rust for CPU-intensive modules."
    assert record.importance == 0.9
    assert record.confidence == 1.0
    assert record.metadata == {"original_author": "alice"}
    assert record.status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_06_canonical_sqlite_round_trip(tmp_path: Path) -> None:
    """TEST 6 — CANONICAL SQLITE ROUND TRIP:
    
    Write a canonical MemoryRecord through the store and read it back.
    Verify all persistable canonical semantics survive.
    """
    db_path = tmp_path / "canonical_roundtrip.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()

    record = MemoryRecord(
        memory_id="mem-roundtrip-1",
        content="Prefer explicit error handling over panics",
        project_id="proj-secure",
        memory_type=MemoryType.PREFERENCE,
        tier=MemoryLevel.L3,
        importance=0.92,
        confidence=0.88,
        subject="coding_standards",
        scope="project:proj-secure",
        source="code_review",
        status=MemoryStatus.ACTIVE,
        supersedes="mem-old-preference",
        metadata={"reviewer": "houhou", "tags": ["style", "safety"]},
    )

    await store.add_many((record,))

    results = await store.search("proj-secure", "error handling", limit=10)
    assert len(results) == 1
    restored = results[0]

    assert restored.memory_id == "mem-roundtrip-1"
    assert restored.project_id == "proj-secure"
    assert restored.content == "Prefer explicit error handling over panics"
    assert restored.memory_type == MemoryType.PREFERENCE
    assert restored.tier == MemoryLevel.L3
    assert restored.level == MemoryLevel.L3
    assert restored.kind == "preference"
    assert abs(restored.importance - 0.92) < 1e-5
    assert abs(restored.confidence - 0.88) < 1e-5
    assert restored.subject == "coding_standards"
    assert restored.scope == "project:proj-secure"
    assert restored.source == "code_review"
    assert restored.status == MemoryStatus.ACTIVE
    assert restored.supersedes == "mem-old-preference"
    assert restored.metadata["reviewer"] == "houhou"
    assert restored.metadata["tags"] == ["style", "safety"]


@pytest.mark.asyncio
async def test_07_global_non_project_memory(tmp_path: Path) -> None:
    """TEST 7 — GLOBAL / NON-PROJECT MEMORY:
    
    Verify the canonical model can represent a global memory (project_id=None).
    Verify persistence contract stores it safely without violating NOT NULL constraint.
    """
    db_path = tmp_path / "global_mem.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()

    global_record = MemoryRecord(
        memory_id="mem-global-1",
        content="System-wide security policy: TLS 1.3 only",
        project_id=None,
        memory_type=MemoryType.LESSON,
        tier=MemoryLevel.L3,
        scope="global",
        subject="system",
    )
    assert global_record.project_id is None
    assert global_record.scope == "global"

    await store.add_many((global_record,))

    # Direct DB inspection: ensure column project_id is written safely as empty string
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM memories WHERE memory_id = ?", ("mem-global-1",)).fetchone()
        assert row is not None
        assert row["project_id"] == ""

    # Reconstructed canonical record should have restored project_id as None
    record_restored = store._row_to_memory(row)
    assert record_restored.project_id is None
    assert record_restored.memory_type == MemoryType.LESSON
    assert record_restored.tier == MemoryLevel.L3


def test_08_status_structure() -> None:
    """TEST 8 — STATUS STRUCTURE:
    
    Verify ACTIVE / SUPERSEDED / ARCHIVED can be represented.
    Verify no automatic transition logic is executed.
    """
    active_mem = MemoryRecord(
        memory_id="mem-act",
        content="Active guideline",
        status=MemoryStatus.ACTIVE,
    )
    superseded_mem = MemoryRecord(
        memory_id="mem-sup",
        content="Superseded guideline",
        status=MemoryStatus.SUPERSEDED,
    )
    archived_mem = MemoryRecord(
        memory_id="mem-arc",
        content="Archived historical note",
        status=MemoryStatus.ARCHIVED,
    )

    assert active_mem.status == MemoryStatus.ACTIVE
    assert superseded_mem.status == MemoryStatus.SUPERSEDED
    assert archived_mem.status == MemoryStatus.ARCHIVED

    # Ensure no automatic mutation occurs
    assert active_mem.status.value == "active"
    assert superseded_mem.status.value == "superseded"
    assert archived_mem.status.value == "archived"


def test_09_supersedes_structure() -> None:
    """TEST 9 — SUPERSEDES STRUCTURE:
    
    Verify a memory can structurally reference an older memory using supersedes.
    Verify no automatic conflict resolution logic is triggered in Phase 03.
    """
    new_mem = MemoryRecord(
        memory_id="mem-v2",
        content="Updated database port configuration",
        memory_type=MemoryType.PROJECT,
        tier=MemoryLevel.L2,
        supersedes="mem-v1",
        status=MemoryStatus.ACTIVE,
    )

    assert new_mem.supersedes == "mem-v1"
    # Canonical record does not mutate or erase mem-v1 automatically:
    assert isinstance(new_mem.supersedes, str)


def test_10_consolidator_output_contract() -> None:
    """TEST 10 — CONSOLIDATOR OUTPUT CONTRACT:
    
    MemoryConsolidator provides consolidate_session_records returning canonical MemoryRecord objects.
    Uses conservative SEMANTIC fallback with explicit provenance metadata.
    """
    consolidator = MemoryConsolidator()
    context = ExecutionContext(
        project_id="proj-99",
        workspace_id="ws-99",
        task_id="task-99",
        session_id="sess-99",
    )
    messages = [
        {"role": "user", "content": "Implement unified memory domain model"},
        {"role": "assistant", "content": "I am working on Phase 03"},
    ]

    canonical_records = consolidator.consolidate_session_records(context, messages)
    assert len(canonical_records) == 1
    rec = canonical_records[0]

    assert isinstance(rec, MemoryRecord)
    assert rec.project_id == "proj-99"
    assert rec.tier == MemoryLevel.L1
    assert rec.memory_type == MemoryType.SEMANTIC
    assert rec.metadata.get("legacy_level") == "l1_working"
    assert rec.metadata.get("legacy_compatibility_fallback") is True
    assert "Implement unified memory domain model" in rec.content
    assert rec.source == "session:sess-99"


@pytest.mark.asyncio
async def test_11_no_pipeline_side_effect(tmp_path: Path) -> None:
    """TEST 11 — NO PIPELINE SIDE EFFECT:
    
    Calling consolidate_session or consolidate_session_records must NOT write to persistence.
    Phase 04 owns persistence pipeline wiring.
    """
    db_path = tmp_path / "unaffected.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()

    consolidator = MemoryConsolidator()
    context = ExecutionContext(project_id="proj-test", workspace_id=str(tmp_path))
    messages = [{"role": "user", "content": "Important session info"}]

    # Call consolidation
    entries = consolidator.consolidate_session(context, messages)
    records = consolidator.consolidate_session_records(context, messages)

    assert len(entries) == 1
    assert len(records) == 1

    # Verify database remains completely empty
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        assert count == 0


def test_12_validation_invariants() -> None:
    """TEST 12 — VALIDATION INVARIANTS:
    
    Verify boundary checks for memory_id, content, importance, confidence, and project_id.
    """
    with pytest.raises(ValueError, match="memory_id"):
        MemoryRecord(memory_id="", content="valid content")

    with pytest.raises(ValueError, match="content"):
        MemoryRecord(memory_id="m1", content="")

    with pytest.raises(ValueError, match="importance"):
        MemoryRecord(memory_id="m1", content="ok", importance=1.5)

    with pytest.raises(ValueError, match="importance"):
        MemoryRecord(memory_id="m1", content="ok", importance=-0.1)

    with pytest.raises(ValueError, match="confidence"):
        MemoryRecord(memory_id="m1", content="ok", confidence=1.2)

    with pytest.raises(ValueError, match="confidence"):
        MemoryRecord(memory_id="m1", content="ok", confidence=-0.5)

    with pytest.raises(ValueError, match="project_id"):
        MemoryRecord(memory_id="m1", content="ok", project_id="")


def test_13_audit1_l1_working_conservative_fallback() -> None:
    """AUDIT 1 TEST:
    
    Verify L1_WORKING does not claim unsupported PROJECT semantics solely because it is L1.
    If explicit evidence exists in key, respect it; otherwise use SEMANTIC fallback with metadata.
    """
    # Case A: Generic working entry without project evidence -> fallback to SEMANTIC + tier L1
    generic_entry = MemoryEntry(
        memory_id="entry-gen",
        level=LegacyMemoryLevel.L1_WORKING,
        key="scratch_note",
        content="Transient task context",
        source="runtime",
    )
    rec_gen = legacy_entry_to_memory_record(generic_entry, project_id="proj-x")
    assert rec_gen.tier == MemoryLevel.L1
    assert rec_gen.memory_type == MemoryType.SEMANTIC
    assert rec_gen.metadata.get("legacy_level") == "l1_working"
    assert rec_gen.metadata.get("legacy_compatibility_fallback") is True

    # Case B: Working entry with explicit semantic evidence in key -> maps to PROJECT
    project_entry = MemoryEntry(
        memory_id="entry-dec",
        level=LegacyMemoryLevel.L1_WORKING,
        key="architecture_decision",
        content="Chosen Redis for caching",
        source="runtime",
    )
    rec_dec = legacy_entry_to_memory_record(project_entry, project_id="proj-x")
    assert rec_dec.tier == MemoryLevel.L1
    assert rec_dec.memory_type == MemoryType.PROJECT
    assert rec_dec.metadata.get("legacy_level") == "l1_working"
    assert rec_dec.metadata.get("legacy_compatibility_fallback") is None


def test_14_audit2_all_four_legacy_levels_preservation() -> None:
    """AUDIT 2 TEST:
    
    Verify all four legacy levels preserve original source information (legacy level,
    key, source, recall count, timestamps, content, project).
    """
    levels = [
        (LegacyMemoryLevel.L0_EPISODIC, MemoryLevel.L0, MemoryType.EPISODIC),
        (LegacyMemoryLevel.L1_WORKING, MemoryLevel.L1, MemoryType.SEMANTIC),
        (LegacyMemoryLevel.L2_SEMANTIC, MemoryLevel.L2, MemoryType.SEMANTIC),
        (LegacyMemoryLevel.L3_LONG_TERM, MemoryLevel.L3, MemoryType.SEMANTIC),
    ]

    for legacy_lvl, expected_tier, expected_type in levels:
        entry = MemoryEntry(
            memory_id=f"entry-{legacy_lvl.value}",
            level=legacy_lvl,
            key=f"key_{legacy_lvl.value}",
            content=f"Content for {legacy_lvl.value}",
            source=f"source_{legacy_lvl.value}",
            recall_count=7,
        )

        canonical = legacy_entry_to_memory_record(entry, project_id="proj-audit")

        # Verify preservation in canonical record
        assert canonical.memory_id == entry.memory_id
        assert canonical.content == entry.content
        assert canonical.tier == expected_tier
        assert canonical.memory_type == expected_type
        assert canonical.source == f"source_{legacy_lvl.value}"
        assert canonical.project_id == "proj-audit"
        assert canonical.metadata["legacy_level"] == legacy_lvl.value.lower()
        assert canonical.metadata["key"] == f"key_{legacy_lvl.value}"
        assert canonical.metadata["recall_count"] == 7

        # Verify roundtrip back to legacy
        restored = memory_record_to_legacy_entry(canonical)
        assert restored.memory_id == entry.memory_id
        assert restored.level == legacy_lvl
        assert restored.key == f"key_{legacy_lvl.value}"
        assert restored.content == entry.content
        assert restored.source == f"source_{legacy_lvl.value}"
        assert restored.recall_count == 7


def test_15_audit3_reserved_canonical_metadata_rejected() -> None:
    """AUDIT 3 TEST:
    
    Caller metadata containing '__canonical__' must be rejected with ValueError,
    preventing silent data overwrite in SQLite persistence.
    """
    with pytest.raises(ValueError, match="reserved for internal store persistence"):
        MemoryRecord(
            memory_id="coll-1",
            content="Testing collision",
            metadata={"__canonical__": {"hijack": True}},
        )

    # Also test store serialization defense directly
    rec = MemoryRecord(memory_id="coll-2", content="Safe record")
    # If someone somehow bypasses init and sets __canonical__ in metadata
    tampered_rec = MemoryRecord(
        memory_id="coll-3",
        content="Safe",
        metadata={"user_key": "val"},
    )
    object.__setattr__(tampered_rec, "metadata", {"__canonical__": "evil"})
    with pytest.raises(ValueError, match="reserved for internal store persistence"):
        SQLiteMemoryStore._serialize_metadata(tampered_rec)


def test_16_audit4_project_id_scope_consistency() -> None:
    """AUDIT 4 TEST:
    
    Ensure project_id and scope maintain strict consistency without contradictory defaults:
    - project_id=None defaults to scope='global'
    - project_id=None with explicit scope='project' normalizes to 'global'
    - project_id present defaults to scope='project'
    """
    # Case 1: Global memory without scope specified
    global_mem = MemoryRecord(memory_id="g1", content="Global fact", project_id=None)
    assert global_mem.project_id is None
    assert global_mem.scope == "global"

    # Case 2: Global memory with contradictory scope='project'
    norm_mem = MemoryRecord(memory_id="g2", content="Global fact", project_id=None, scope="project")
    assert norm_mem.project_id is None
    assert norm_mem.scope == "global"

    # Case 3: Project memory without scope specified
    proj_mem = MemoryRecord(memory_id="p1", content="Project fact", project_id="proj-omega")
    assert proj_mem.project_id == "proj-omega"
    assert proj_mem.scope == "project"

    # Case 4: Project memory with explicit scope
    custom_scope_mem = MemoryRecord(
        memory_id="p2",
        content="Workspace fact",
        project_id="proj-omega",
        scope="workspace:ws-1",
    )
    assert custom_scope_mem.project_id == "proj-omega"
    assert custom_scope_mem.scope == "workspace:ws-1"


def test_17_canonical_semantic_equivalence_policy() -> None:
    """Audit canonical semantic equivalence helper:

    Verifies that are_canonically_equivalent audits all persistence-relevant canonical fields
    and explicitly ignores operational lifecycle timestamps.
    """
    from core.memory import are_canonically_equivalent

    base = MemoryRecord(
        memory_id="eq-1",
        content="Stable architecture invariant",
        project_id="proj-1",
        memory_type=MemoryType.PROJECT,
        tier=MemoryLevel.L2,
        importance=0.8,
        confidence=0.95,
        subject="system",
        scope="project",
        source="runtime",
        status=MemoryStatus.ACTIVE,
        supersedes=None,
        metadata={"logical_key": "arch_key", "custom_meta": 42},
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2027, 1, 1, tzinfo=UTC),
    )

    # Identical record is equivalent
    dup = MemoryRecord(
        memory_id="eq-1",
        content="Stable architecture invariant",
        project_id="proj-1",
        memory_type=MemoryType.PROJECT,
        tier=MemoryLevel.L2,
        importance=0.8,
        confidence=0.95,
        subject="system",
        scope="project",
        source="runtime",
        status=MemoryStatus.ACTIVE,
        supersedes=None,
        metadata={"logical_key": "arch_key", "custom_meta": 42},
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert are_canonically_equivalent(base, dup) is True

    # Differing operational timestamps are EXCLUDED and still equivalent
    op_diff = MemoryRecord(
        memory_id="eq-1",
        content="Stable architecture invariant",
        project_id="proj-1",
        memory_type=MemoryType.PROJECT,
        tier=MemoryLevel.L2,
        importance=0.8,
        confidence=0.95,
        subject="system",
        scope="project",
        source="runtime",
        status=MemoryStatus.ACTIVE,
        supersedes=None,
        metadata={"logical_key": "arch_key", "custom_meta": 42, "__status_updated_at": "2026-10-06T12:00:00Z"},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 10, 6, tzinfo=UTC),
        last_accessed_at=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert are_canonically_equivalent(base, op_diff) is True

    # Any canonical field mismatch evaluates False:
    # 1. memory_id
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "memory_id": "eq-2"})) is False
    # 2. project_id
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "project_id": "proj-2"})) is False
    # 3. memory_type
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "memory_type": "lesson"})) is False
    # 4. tier
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "tier": "l3"})) is False
    # 5. content
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "content": "Changed content"})) is False
    # 6. importance
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "importance": 0.5})) is False
    # 7. confidence
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "confidence": 0.6})) is False
    # 8. subject
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "subject": "user"})) is False
    # 9. scope
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "scope": "global"})) is False
    # 10. source
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "source": "user"})) is False
    # 11. status
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "status": "archived"})) is False
    # 12. supersedes
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "supersedes": "old-mem"})) is False
    # 13. valid_from
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "valid_from": "2026-05-01T00:00:00+00:00"})) is False
    # 14. valid_until
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "valid_until": "2028-01-01T00:00:00+00:00"})) is False
    # 15. metadata
    assert are_canonically_equivalent(base, MemoryRecord.from_dict({**base.to_dict(), "metadata": {"logical_key": "other_key"}})) is False

    # 15a. Operational allowlist: only __status_updated_at is excluded, so differing values are equivalent
    assert are_canonically_equivalent(
        MemoryRecord.from_dict({**base.to_dict(), "metadata": {"logical_key": "arch_key", "custom_meta": 42, "__status_updated_at": "2026-10-06T10:00:00Z"}}),
        MemoryRecord.from_dict({**base.to_dict(), "metadata": {"logical_key": "arch_key", "custom_meta": 42, "__status_updated_at": "2026-10-06T12:00:00Z"}}),
    ) is True

    # 15b. Arbitrary "__*" metadata (e.g. __business_flag) is SEMANTIC and must NOT be ignored
    assert are_canonically_equivalent(
        MemoryRecord.from_dict({**base.to_dict(), "metadata": {"logical_key": "arch_key", "custom_meta": 42, "__business_flag": "A"}}),
        MemoryRecord.from_dict({**base.to_dict(), "metadata": {"logical_key": "arch_key", "custom_meta": 42, "__business_flag": "B"}}),
    ) is False
