"""Phase 04 — Durable Memory Pipeline: test suite.

Definition of Done requirements addressed:
    [D1] write() persists records to SQLite.
    [D2] retrieve() returns ACTIVE records with relevance scoring.
    [D3] Superseding transition is atomic: old record becomes SUPERSEDED,
         new record is ACTIVE with supersedes reference.
    [D4] logical_key-based conflict detection automatically supersedes
         stale ACTIVE records.
    [D5] Persistence survives store re-instantiation (new SQLiteMemoryStore
         object pointing to the same file returns the same records).
    [D6] Tier filter, memory_type filter, status filter, and min_importance
         filter narrow retrieval results correctly.
    [D7] archive() transitions a record to ARCHIVED and hides it from
         default (ACTIVE-only) retrieval.
    [D8] write() with empty sequence is a no-op.
    [D9] retrieve() with no project_id raises ValueError.
    [D10] Supersede rejects mismatched supersedes reference.
    [D11] retrieve() with limit=0 returns empty tuple.
    [D12] Pre-Phase-04 (legacy) rows lacking the 'status' column default
          correctly after schema migration.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.durable_memory_service import DurableMemoryService
from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
)
from persistence.sqlite_memory_store import SQLiteMemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record(
    content: str,
    *,
    memory_id: str | None = None,
    project_id: str = "proj-04",
    memory_type: MemoryType = MemoryType.SEMANTIC,
    tier: MemoryLevel = MemoryLevel.L2,
    importance: float = 0.7,
    logical_key: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    supersedes: str | None = None,
) -> MemoryRecord:
    mid = memory_id or f"mem-{abs(hash(content)):016x}"
    meta: dict[str, object] = {}
    if logical_key:
        meta["logical_key"] = logical_key
    return MemoryRecord(
        memory_id=mid,
        content=content,
        project_id=project_id,
        memory_type=memory_type,
        tier=tier,
        importance=importance,
        status=status,
        supersedes=supersedes,
        metadata=meta,
    )


async def _service(tmp_path: Path) -> DurableMemoryService:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.initialize()
    return DurableMemoryService(store)


# ---------------------------------------------------------------------------
# D1 — write() persists records to SQLite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d1_write_persists_records(tmp_path: Path) -> None:
    svc = await _service(tmp_path)
    rec = _record("Use PostgreSQL for transactional data.", importance=0.9)

    ids = await svc.write([rec])

    assert len(ids) == 1
    assert ids[0] == rec.memory_id

    # Verify via retrieve().
    results = await svc.retrieve("proj-04", "PostgreSQL")
    assert len(results) == 1
    assert results[0].memory_id == rec.memory_id
    assert results[0].content == "Use PostgreSQL for transactional data."


# ---------------------------------------------------------------------------
# D2 — retrieve() returns ACTIVE records with relevance scoring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d2_retrieve_scores_by_relevance(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    low = _record("Frontend uses React.", importance=0.3, memory_id="low-001")
    high = _record(
        "Backend uses FastAPI and PostgreSQL.", importance=0.9, memory_id="high-001"
    )
    await svc.write([low, high])

    results = await svc.retrieve("proj-04", "PostgreSQL FastAPI", limit=5)

    # The high-importance, phrase-matching record must rank first.
    assert results[0].memory_id == high.memory_id


# ---------------------------------------------------------------------------
# D3 — supersede() is atomic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d3_atomic_supersede(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.initialize()

    old = _record("Decision: use REST API.", memory_id="old-001")
    await store.add_many((old,))

    new = _record(
        "Decision: use GraphQL API.",
        memory_id="new-001",
        supersedes="old-001",
    )
    await store.supersede("old-001", new)

    # Old record must be SUPERSEDED.
    superseded = await store.retrieve(
        "proj-04", "", status_filter=MemoryStatus.SUPERSEDED
    )
    sids = {r.memory_id for r in superseded}
    assert "old-001" in sids

    # New record must be ACTIVE.
    active = await store.retrieve("proj-04", "", status_filter=MemoryStatus.ACTIVE)
    aids = {r.memory_id for r in active}
    assert "new-001" in aids
    assert "old-001" not in aids


# ---------------------------------------------------------------------------
# D4 — logical_key conflict detection (auto-supersede via write())
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d4_logical_key_conflict_supersedes_stale(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    original = _record(
        "DB decision: PostgreSQL.",
        memory_id="original-001",
        logical_key="db_decision",
    )
    await svc.write([original])

    updated = _record(
        "DB decision: CockroachDB for global scale.",
        memory_id="updated-001",
        logical_key="db_decision",
    )
    ids = await svc.write([updated])

    # The service must have issued a superseding record.
    assert len(ids) == 1
    new_id = ids[0]

    # Original must now be SUPERSEDED.
    store = svc._store  # noqa: SLF001
    superseded_matches = await store.retrieve(
        "proj-04", "", status_filter=MemoryStatus.SUPERSEDED
    )
    s_ids = {r.memory_id for r in superseded_matches}
    assert "original-001" in s_ids

    # The new record must be ACTIVE and reference the old one.
    active_records = await svc.retrieve("proj-04", "CockroachDB")
    assert len(active_records) == 1
    assert active_records[0].memory_id == new_id
    assert active_records[0].supersedes == "original-001"


# ---------------------------------------------------------------------------
# D5 — Persistence across re-instantiation (MANDATORY)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d5_persistence_across_reinstantiation(tmp_path: Path) -> None:
    """Records written via one store instance must survive re-instantiation."""
    db_path = tmp_path / "memory.db"

    # ---- Session 1: write ----
    store_a = SQLiteMemoryStore(db_path)
    await store_a.initialize()
    svc_a = DurableMemoryService(store_a)

    r1 = _record("Architecture: event-driven microservices.", memory_id="persist-001")
    r2 = _record("Frontend: Next.js SPA.", memory_id="persist-002")
    await svc_a.write([r1, r2])

    # ---- Session 2: new store instance, same db file ----
    store_b = SQLiteMemoryStore(db_path)
    await store_b.initialize()
    svc_b = DurableMemoryService(store_b)

    results = await svc_b.retrieve("proj-04", "microservices", limit=5)

    # persist-001 must be present and ranked first (it matches "microservices").
    assert len(results) >= 1
    assert results[0].memory_id == "persist-001"
    assert results[0].content == "Architecture: event-driven microservices."
    assert results[0].project_id == "proj-04"
    assert results[0].status == MemoryStatus.ACTIVE

    # Also verify the second record persists and ranks first for its specific query.
    all_results = await svc_b.retrieve("proj-04", "Next.js SPA", limit=5)
    assert len(all_results) >= 1
    assert all_results[0].memory_id == "persist-002"


# ---------------------------------------------------------------------------
# D6 — Filters: tier, memory_type, status, min_importance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d6_tier_filter(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    l1_rec = _record("L1 working note.", tier=MemoryLevel.L1, memory_id="tier-l1")
    l3_rec = _record("L3 durable knowledge.", tier=MemoryLevel.L3, memory_id="tier-l3")
    await svc.write([l1_rec, l3_rec])

    results = await svc.retrieve("proj-04", "", tier_filter=MemoryLevel.L3)
    ids = {r.memory_id for r in results}
    assert "tier-l3" in ids
    assert "tier-l1" not in ids


@pytest.mark.asyncio
async def test_d6_memory_type_filter(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    pref = _record(
        "Preference: dark mode.",
        memory_type=MemoryType.PREFERENCE,
        memory_id="type-pref",
    )
    sem = _record(
        "Concept: idempotency.",
        memory_type=MemoryType.SEMANTIC,
        memory_id="type-sem",
    )
    await svc.write([pref, sem])

    results = await svc.retrieve(
        "proj-04", "", memory_type_filter=MemoryType.PREFERENCE
    )
    ids = {r.memory_id for r in results}
    assert "type-pref" in ids
    assert "type-sem" not in ids


@pytest.mark.asyncio
async def test_d6_min_importance_filter(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    low = _record("Low importance note.", importance=0.2, memory_id="imp-low")
    high = _record("High importance rule.", importance=0.9, memory_id="imp-high")
    await svc.write([low, high])

    results = await svc.retrieve("proj-04", "", min_importance=0.5)
    ids = {r.memory_id for r in results}
    assert "imp-high" in ids
    assert "imp-low" not in ids


# ---------------------------------------------------------------------------
# D7 — archive() hides records from default ACTIVE retrieval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d7_archive_hides_from_active_retrieval(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    rec = _record("Stale design note.", memory_id="archive-001")
    await svc.write([rec])

    # Confirm it is retrievable before archiving.
    before = await svc.retrieve("proj-04", "Stale design")
    assert any(r.memory_id == "archive-001" for r in before)

    await svc.archive("proj-04", "archive-001")

    # Must no longer appear in default (ACTIVE) retrieval.
    after = await svc.retrieve("proj-04", "Stale design")
    assert not any(r.memory_id == "archive-001" for r in after)

    # Must appear when explicitly querying ARCHIVED status.
    archived = await svc.retrieve(
        "proj-04", "Stale design", status_filter=MemoryStatus.ARCHIVED
    )
    assert any(r.memory_id == "archive-001" for r in archived)


# ---------------------------------------------------------------------------
# D8 — write() with empty sequence is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d8_write_empty_is_noop(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    ids = await svc.write([])

    assert ids == ()
    results = await svc.retrieve("proj-04", "")
    assert results == ()


# ---------------------------------------------------------------------------
# D9 — retrieve() without project_id raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d9_retrieve_requires_project_id(tmp_path: Path) -> None:
    svc = await _service(tmp_path)

    with pytest.raises(ValueError, match="project_id"):
        await svc.retrieve("", "anything")

    with pytest.raises(ValueError, match="project_id"):
        await svc.retrieve("   ", "anything")


# ---------------------------------------------------------------------------
# D10 — supersede() rejects mismatched supersedes reference
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d10_supersede_rejects_mismatch(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.initialize()

    old = _record("Old record.", memory_id="mismatch-old")
    await store.add_many((old,))

    bad_new = _record(
        "New record with wrong supersedes ref.",
        memory_id="mismatch-new",
        supersedes="WRONG-ID",  # Does not match "mismatch-old".
    )

    with pytest.raises(ValueError, match="supersedes"):
        await store.supersede("mismatch-old", bad_new)


# ---------------------------------------------------------------------------
# D11 — retrieve() with limit=0 returns empty tuple
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d11_retrieve_limit_zero(tmp_path: Path) -> None:
    svc = await _service(tmp_path)
    await svc.write([_record("Some content.", memory_id="limit-001")])

    results = await svc.retrieve("proj-04", "", limit=0)
    assert results == ()


# ---------------------------------------------------------------------------
# D12 — Pre-Phase-04 legacy rows survive schema migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d12_legacy_rows_survive_schema_migration(tmp_path: Path) -> None:
    """Rows inserted BEFORE the 'status' / 'logical_key' columns existed
    must be readable after migration via DEFAULT 'active'."""

    db_path = tmp_path / "legacy.db"

    # Directly insert a row WITHOUT the status / logical_key columns
    # (simulating a pre-Phase-04 database).
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE memories (
            memory_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            level TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            importance REAL NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        INSERT INTO memories VALUES (
            'legacy-001', 'proj-04', 'l2', 'note',
            'Legacy row content.', 0.6, '{}',
            '2026-01-01T00:00:00+00:00'
        )
        """
    )
    con.commit()
    con.close()

    # Now let SQLiteMemoryStore migrate (add status + logical_key columns).
    store = SQLiteMemoryStore(db_path)
    await store.initialize()

    # Legacy row must be retrievable via Phase 04 retrieve().
    svc = DurableMemoryService(store)
    results = await svc.retrieve("proj-04", "Legacy row")
    assert len(results) == 1
    assert results[0].memory_id == "legacy-001"
    # Default status from migration DEFAULT clause.
    assert results[0].status == MemoryStatus.ACTIVE
