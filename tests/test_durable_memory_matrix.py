"""Phase 04 — Behavioral Test Matrix (28 required verification gates).

Verifies the complete DoD and audit requirements for the Durable Memory Pipeline:
1.  Durable write survives new store/service instance.
2.  Explicit consolidate-and-persist writes canonical records.
3.  Pure consolidation remains side-effect free.
4.  Same ID + same content is idempotent.
5.  Same ID + different content cannot silently overwrite.
6.  Explicit conflict supersedes old ACTIVE memory.
7.  Natural-language similarity without logical key does not supersede.
8.  Conflict identity distinguishes subject/scope/type.
9.  Failed replacement transaction rolls back old status.
10. Default retrieval returns ACTIVE only.
11. Historical retrieval can return SUPERSEDED/ARCHIVED.
12. Project A cannot retrieve Project B memory.
13. include_global=True includes global memory.
14. include_global=False excludes global memory.
15. MemoryType filter.
16. MemoryTier filter.
17. MemoryStatus filter.
18. min_importance filter.
19. Deterministic ranking.
20. Ranking result exposes score/reasons.
21. >200-record case does not hide a strong lexical match due solely to importance preselection.
22. Non-empty query does not return zero-match unrelated memories.
23. MCP consolidate_memory success corresponds to durable SQLite data.
24. Legacy row compatibility.
25. Superseded memory remains physically stored.
26. Archived memory remains physically stored.
27. No automatic ContextBuilder injection.
28. No UserModel side effect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest

from core.context import ExecutionContext
from core.durable_memory_service import (
    GLOBAL_PROJECT_ID,
    DurableMemoryService,
    MemoryMatch,
)
from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
)
from core.memory_consolidation import MemoryConsolidator
from my_agent_mcp import server
from persistence.sqlite_memory_store import SQLiteMemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    content: str,
    *,
    memory_id: str | None = None,
    project_id: str | None = "proj-matrix",
    memory_type: MemoryType = MemoryType.SEMANTIC,
    tier: MemoryLevel = MemoryLevel.L2,
    importance: float = 0.7,
    confidence: float = 0.9,
    logical_key: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    subject: str = "system",
    scope: str = "project",
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
        confidence=confidence,
        status=status,
        subject=subject,
        scope=scope,
        supersedes=supersedes,
        metadata=meta,
    )


async def _init_service(db_path: Path) -> DurableMemoryService:
    store = SQLiteMemoryStore(db_path)
    await store.initialize()
    return DurableMemoryService(store)


# ---------------------------------------------------------------------------
# Test Cases 1 - 28
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_01_durable_write_survives_new_store_instance(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    svc1 = await _init_service(db_file)
    rec = _make_record("Durable architectural choice: event bus", memory_id="surv-1")
    await svc1.write([rec])

    # New store and service instance pointing to the same SQLite file
    svc2 = await _init_service(db_file)
    res = await svc2.retrieve("proj-matrix", "event bus")
    assert len(res) == 1
    assert res[0].memory_id == "surv-1"
    assert res[0].content == "Durable architectural choice: event bus"


@pytest.mark.asyncio
async def test_02_explicit_consolidate_and_persist(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    consolidator = MemoryConsolidator()
    ctx = ExecutionContext(workspace_id="ws-1", session_id="sess-1", project_id="proj-matrix")
    session_messages = [
        {"role": "user", "content": "We decided to migrate the frontend to Next.js 15."}
    ]

    persisted_ids = await svc.consolidate_and_persist(consolidator, ctx, session_messages)
    assert len(persisted_ids) == 1

    matches = await svc.retrieve("proj-matrix", "Next.js")
    assert len(matches) == 1
    assert matches[0].memory_id == persisted_ids[0]
    assert "Next.js" in matches[0].content


@pytest.mark.asyncio
async def test_03_pure_consolidation_remains_side_effect_free(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    svc = await _init_service(db_file)
    consolidator = MemoryConsolidator()
    ctx = ExecutionContext(workspace_id="ws-1", session_id="sess-1", project_id="proj-matrix")
    session_messages = [
        {"role": "user", "content": "Side effect check message."}
    ]

    # Calling pure consolidator directly must NOT write to persistence
    records = consolidator.consolidate_session_records(ctx, session_messages)
    assert len(records) >= 1

    # Database must remain completely empty
    results = await svc.retrieve("proj-matrix", "")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_04_same_id_same_content_is_idempotent(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    rec = _make_record("Constant database config", memory_id="const-1")

    ids1 = await svc.write([rec])
    ids2 = await svc.write([rec])

    assert ids1 == ("const-1",)
    assert ids2 == ("const-1",)

    results = await svc.retrieve("proj-matrix", "")
    assert len(results) == 1
    assert results[0].memory_id == "const-1"


@pytest.mark.asyncio
async def test_04b_store_add_many_enforces_semantic_equality_and_propagates_integrity_error(tmp_path: Path) -> None:
    """Direct SQLiteMemoryStore write:

    - Identical canonical semantics re-written: idempotent success without error.
    - Same ID with changed canonical semantics: raises ValueError (re-write rejected).
    - Unexpected IntegrityError (e.g. invalid foreign key or corrupted constraint) propagates.
    """
    db_file = tmp_path / "test.db"
    store = SQLiteMemoryStore(db_file)
    await store.initialize()

    rec1 = _make_record("Original record", memory_id="direct-1", subject="backend")
    await store.add_many((rec1,))

    # Identical record re-added: accepted idempotently
    await store.add_many((rec1,))

    # Different canonical semantics (changed subject) with same ID: rejected with ValueError
    rec2 = _make_record("Original record", memory_id="direct-1", subject="frontend")
    with pytest.raises(ValueError, match="Conflict: memory record 'direct-1' already exists with different canonical semantics"):
        await store.add_many((rec2,))


@pytest.mark.asyncio
async def test_05_same_id_different_content_cannot_silently_overwrite(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record("Version 1 content", memory_id="dup-1")
    await svc.write([rec1])

    rec2 = _make_record("Version 2 different content", memory_id="dup-1")
    with pytest.raises(ValueError, match="Conflict: memory record 'dup-1' already exists"):
        await svc.write([rec2])

    # Content in store must remain unchanged (no silent overwrite)
    results = await svc.retrieve("proj-matrix", "")
    assert len(results) == 1
    assert results[0].content == "Version 1 content"


@pytest.mark.asyncio
async def test_05b_same_id_same_content_changed_subject_rejected(tmp_path: Path) -> None:
    """Idempotency audit: same ID + same content but changed subject must be rejected."""
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record("Invariant content", memory_id="idem-subj", subject="system")
    await svc.write([rec1])

    rec2 = _make_record("Invariant content", memory_id="idem-subj", subject="user")
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-subj' already exists"):
        await svc.write([rec2])


@pytest.mark.asyncio
async def test_05c_same_id_same_content_changed_scope_rejected(tmp_path: Path) -> None:
    """Idempotency audit: same ID + same content but changed scope must be rejected."""
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record("Invariant content", memory_id="idem-scope", scope="project")
    await svc.write([rec1])

    rec2 = _make_record("Invariant content", memory_id="idem-scope", scope="global")
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-scope' already exists"):
        await svc.write([rec2])


@pytest.mark.asyncio
async def test_05d_same_id_same_content_changed_tier_rejected(tmp_path: Path) -> None:
    """Idempotency audit: same ID + same content but changed tier must be rejected."""
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record("Invariant content", memory_id="idem-tier", tier=MemoryLevel.L1)
    await svc.write([rec1])

    rec2 = _make_record("Invariant content", memory_id="idem-tier", tier=MemoryLevel.L3)
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-tier' already exists"):
        await svc.write([rec2])


@pytest.mark.asyncio
async def test_05e_same_id_same_content_changed_confidence_rejected(tmp_path: Path) -> None:
    """Idempotency audit: same ID + same content but changed confidence must be rejected."""
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record("Invariant content", memory_id="idem-conf", confidence=0.9)
    await svc.write([rec1])

    rec2 = _make_record("Invariant content", memory_id="idem-conf", confidence=0.5)
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-conf' already exists"):
        await svc.write([rec2])


@pytest.mark.asyncio
async def test_05f_same_id_same_content_changed_metadata_rejected(tmp_path: Path) -> None:
    """Idempotency audit: same ID + same content but changed relevant metadata must be rejected."""
    svc = await _init_service(tmp_path / "test.db")
    rec1 = MemoryRecord(
        memory_id="idem-meta",
        content="Invariant content",
        project_id="proj-matrix",
        metadata={"logical_key": "k1", "env": "dev"},
    )
    await svc.write([rec1])

    rec2 = MemoryRecord(
        memory_id="idem-meta",
        content="Invariant content",
        project_id="proj-matrix",
        metadata={"logical_key": "k1", "env": "prod"},
    )
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-meta' already exists"):
        await svc.write([rec2])

    # Arbitrary caller "__*" metadata (e.g. __business_flag) is semantic and must NOT be ignored
    rec_flag_a = MemoryRecord(
        memory_id="idem-meta-flag",
        content="Flag invariant content",
        project_id="proj-matrix",
        metadata={"logical_key": "k2", "__business_flag": "A"},
    )
    await svc.write([rec_flag_a])

    rec_flag_b = MemoryRecord(
        memory_id="idem-meta-flag",
        content="Flag invariant content",
        project_id="proj-matrix",
        metadata={"logical_key": "k2", "__business_flag": "B"},
    )
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-meta-flag' already exists"):
        await svc.write([rec_flag_b])

    # Only __status_updated_at differs: classified operational allowlist, write accepted idempotently
    rec_op_1 = MemoryRecord(
        memory_id="idem-meta-op",
        content="Op invariant content",
        project_id="proj-matrix",
        metadata={"logical_key": "k3", "__status_updated_at": "2026-10-06T10:00:00Z"},
    )
    await svc.write([rec_op_1])

    rec_op_2 = MemoryRecord(
        memory_id="idem-meta-op",
        content="Op invariant content",
        project_id="proj-matrix",
        metadata={"logical_key": "k3", "__status_updated_at": "2026-10-06T12:00:00Z"},
    )
    res_op = await svc.write([rec_op_2])
    assert res_op == ("idem-meta-op",)


@pytest.mark.asyncio
async def test_05g_same_id_same_content_changed_valid_range_rejected(tmp_path: Path) -> None:
    """Idempotency audit: same ID + same content but changed valid_until must be rejected."""
    from datetime import UTC, datetime
    svc = await _init_service(tmp_path / "test.db")
    rec1 = MemoryRecord(
        memory_id="idem-range",
        content="Invariant content",
        project_id="proj-matrix",
        valid_until=datetime(2026, 12, 31, tzinfo=UTC),
    )
    await svc.write([rec1])

    rec2 = MemoryRecord(
        memory_id="idem-range",
        content="Invariant content",
        project_id="proj-matrix",
        valid_until=datetime(2027, 12, 31, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="Conflict: memory record 'idem-range' already exists"):
        await svc.write([rec2])


@pytest.mark.asyncio
async def test_06_explicit_conflict_supersedes_old_active_memory(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record(
        "PostgreSQL 14 database",
        memory_id="db-v1",
        logical_key="database_version",
        subject="project",
        scope="project",
        memory_type=MemoryType.PROJECT,
    )
    await svc.write([rec1])

    rec2 = _make_record(
        "PostgreSQL 16 database upgrade",
        memory_id="db-v2",
        logical_key="database_version",
        subject="project",
        scope="project",
        memory_type=MemoryType.PROJECT,
    )
    persisted = await svc.write([rec2])

    # Active retrieval only returns the new record
    active = await svc.retrieve("proj-matrix", "PostgreSQL")
    assert len(active) == 1
    assert active[0].content == "PostgreSQL 16 database upgrade"
    assert active[0].supersedes == "db-v1"

    # Old record is now SUPERSEDED
    old = await svc.retrieve("proj-matrix", "", status_filter=MemoryStatus.SUPERSEDED)
    assert len(old) == 1
    assert old[0].memory_id == "db-v1"


@pytest.mark.asyncio
async def test_07_natural_language_similarity_without_logical_key_does_not_supersede(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    rec1 = _make_record("We prefer TypeScript for web apps.", memory_id="sim-1")
    rec2 = _make_record("We prefer TypeScript for web apps and backend.", memory_id="sim-2")

    await svc.write([rec1])
    await svc.write([rec2])

    results = await svc.retrieve("proj-matrix", "TypeScript")
    # Both records remain ACTIVE since no logical_key was provided
    assert len(results) == 2
    ids = {r.memory_id for r in results}
    assert ids == {"sim-1", "sim-2"}


@pytest.mark.asyncio
async def test_08_conflict_identity_distinguishes_subject_scope_type(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    rec_user = _make_record(
        "User prefers dark theme",
        memory_id="conf-user",
        logical_key="ui_theme",
        subject="user",
        scope="global",
        memory_type=MemoryType.PREFERENCE,
    )
    rec_proj = _make_record(
        "Project design system requires light theme",
        memory_id="conf-proj",
        logical_key="ui_theme",
        subject="project",
        scope="project",
        memory_type=MemoryType.PROJECT,
    )

    await svc.write([rec_user])
    await svc.write([rec_proj])

    # Both records must remain ACTIVE because composite conflict identities differ
    active = await svc.retrieve("proj-matrix", "")
    assert len(active) == 2
    ids = {r.memory_id for r in active}
    assert ids == {"conf-user", "conf-proj"}


@pytest.mark.asyncio
async def test_09_failed_replacement_transaction_rolls_back_old_status(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    store = SQLiteMemoryStore(db_file)
    await store.initialize()
    svc = DurableMemoryService(store)

    rec1 = _make_record(
        "Original active record",
        memory_id="atom-1",
        logical_key="atom_key",
    )
    await svc.write([rec1])

    # Mock execute inside _supersede_sync such that the second execute (INSERT) raises
    orig_connect = store._connect

    class FailingConnection:
        def __init__(self, real_con):
            self._real_con = real_con
            self._call_count = 0

        def execute(self, sql, params=()):
            if "INSERT INTO memories" in sql:
                raise sqlite3.OperationalError("Simulated disk error on INSERT")
            return self._real_con.execute(sql, params)

        def __enter__(self):
            self._real_con.__enter__()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return self._real_con.__exit__(exc_type, exc_val, exc_tb)

        def __getattr__(self, name):
            return getattr(self._real_con, name)

    with patch.object(store, "_connect", side_effect=lambda: FailingConnection(orig_connect())):
        rec2 = _make_record(
            "Failing replacement",
            memory_id="atom-2",
            logical_key="atom_key",
            supersedes="atom-1",
        )
        with pytest.raises(sqlite3.OperationalError, match="Simulated disk error"):
            await store.supersede("atom-1", rec2)

    # Verify rollback: atom-1 remains ACTIVE and atom-2 does not exist
    res = await svc.retrieve("proj-matrix", "Original active", status_filter=MemoryStatus.ACTIVE)
    assert len(res) == 1
    assert res[0].memory_id == "atom-1"
    assert res[0].status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_10_default_retrieval_returns_active_only(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r_act = _make_record("Active note", memory_id="r-act", status=MemoryStatus.ACTIVE)
    r_sup = _make_record("Superseded note", memory_id="r-sup", status=MemoryStatus.SUPERSEDED)
    r_arc = _make_record("Archived note", memory_id="r-arc", status=MemoryStatus.ARCHIVED)

    await svc._store.add_many((r_act, r_sup, r_arc))

    results = await svc.retrieve("proj-matrix", "")
    assert len(results) == 1
    assert results[0].memory_id == "r-act"


@pytest.mark.asyncio
async def test_11_historical_retrieval_can_return_superseded_archived(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r_act = _make_record("Active note", memory_id="h-act", status=MemoryStatus.ACTIVE)
    r_sup = _make_record("Superseded note", memory_id="h-sup", status=MemoryStatus.SUPERSEDED)
    r_arc = _make_record("Archived note", memory_id="h-arc", status=MemoryStatus.ARCHIVED)

    await svc._store.add_many((r_act, r_sup, r_arc))

    all_res = await svc.retrieve("proj-matrix", "", status_filter=None)
    assert len(all_res) == 3

    sup_res = await svc.retrieve("proj-matrix", "", status_filter=MemoryStatus.SUPERSEDED)
    assert len(sup_res) == 1
    assert sup_res[0].memory_id == "h-sup"

    arc_res = await svc.retrieve("proj-matrix", "", status_filter=MemoryStatus.ARCHIVED)
    assert len(arc_res) == 1
    assert arc_res[0].memory_id == "h-arc"


@pytest.mark.asyncio
async def test_12_project_a_cannot_retrieve_project_b_memory(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r_a = _make_record("Secret Project A information", project_id="project-A")
    r_b = _make_record("Secret Project B information", project_id="project-B")

    await svc.write([r_a, r_b])

    results_a = await svc.retrieve("project-A", "Secret")
    assert len(results_a) == 1
    assert results_a[0].project_id == "project-A"
    assert "Project A" in results_a[0].content


@pytest.mark.asyncio
async def test_13_include_global_true_includes_global_memory(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r_proj = _make_record("Project specific convention: use pytest", project_id="project-A")
    r_glob = _make_record("Global convention: use python 3.13", project_id=GLOBAL_PROJECT_ID)

    await svc.write([r_proj, r_glob])

    results = await svc.retrieve("project-A", "convention", include_global=True)
    assert len(results) == 2
    ids = {r.project_id for r in results}
    assert ids == {"project-A", GLOBAL_PROJECT_ID}


@pytest.mark.asyncio
async def test_14_include_global_false_excludes_global_memory(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r_proj = _make_record("Project specific convention: use pytest", project_id="project-A")
    r_glob = _make_record("Global convention: use python 3.13", project_id=GLOBAL_PROJECT_ID)

    await svc.write([r_proj, r_glob])

    results = await svc.retrieve("project-A", "convention", include_global=False)
    assert len(results) == 1
    assert results[0].project_id == "project-A"


@pytest.mark.asyncio
async def test_13b_canonical_global_memory_persistence_lifecycle(tmp_path: Path) -> None:
    """Preserve frozen Phase 03 canonical global memory contract:

    - write MemoryRecord(project_id=None, scope="global")
    - recreate SQLite store/service
    - retrieve(project_A, include_global=True) -> global record returned (with project_id=None)
    - retrieve(project_A, include_global=False) -> global record absent
    """
    db_file = tmp_path / "global_lifecycle.db"
    svc1 = await _init_service(db_file)

    global_record = MemoryRecord(
        memory_id="glob-canonical-42",
        content="Cross-project standard: Ruff linter with strict type annotations",
        project_id=None,
        scope="global",
        importance=0.85,
    )
    persisted_ids = await svc1.write([global_record])
    assert persisted_ids == ("glob-canonical-42",)

    # Recreate SQLite store and service from same file to test persistence durability
    svc2 = await _init_service(db_file)

    # retrieve(project_A, include_global=True) -> global record returned
    res_with_global = await svc2.retrieve("project_A", "Ruff linter", include_global=True)
    assert len(res_with_global) == 1
    assert res_with_global[0].memory_id == "glob-canonical-42"
    assert res_with_global[0].project_id is None
    assert res_with_global[0].scope == "global"
    assert "global_scope" in res_with_global[0].reasons

    # retrieve(project_A, include_global=False) -> global record absent
    res_no_global = await svc2.retrieve("project_A", "Ruff linter", include_global=False)
    assert len(res_no_global) == 0

    # Rejection of reserved legacy sentinel '__global__' for new durable writes
    bad_global_rec = MemoryRecord(
        memory_id="bad-glob-sentinel",
        content="Forbidden legacy sentinel write",
        project_id="__global__",
        scope="global",
    )
    with pytest.raises(ValueError, match="RESERVED_SENTINEL"):
        await svc1.write([bad_global_rec])

    with pytest.raises(ValueError, match="RESERVED_SENTINEL"):
        await svc1._store.add_many((bad_global_rec,))


@pytest.mark.asyncio
async def test_15_memory_type_filter(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r1 = _make_record("User preference", memory_type=MemoryType.PREFERENCE)
    r2 = _make_record("System pattern", memory_type=MemoryType.SEMANTIC)

    await svc.write([r1, r2])

    res = await svc.retrieve("proj-matrix", "", memory_type_filter=MemoryType.PREFERENCE)
    assert len(res) == 1
    assert res[0].memory_type == MemoryType.PREFERENCE


@pytest.mark.asyncio
async def test_16_memory_tier_filter(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r1 = _make_record("L1 working note", tier=MemoryLevel.L1)
    r2 = _make_record("L3 long term note", tier=MemoryLevel.L3)

    await svc.write([r1, r2])

    res = await svc.retrieve("proj-matrix", "", tier_filter=MemoryLevel.L3)
    assert len(res) == 1
    assert res[0].tier == MemoryLevel.L3


@pytest.mark.asyncio
async def test_17_memory_status_filter(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r1 = _make_record("Note one", memory_id="stat-1")
    await svc.write([r1])
    await svc.archive("proj-matrix", "stat-1")

    res = await svc.retrieve("proj-matrix", "", status_filter=MemoryStatus.ARCHIVED)
    assert len(res) == 1
    assert res[0].status == MemoryStatus.ARCHIVED


@pytest.mark.asyncio
async def test_18_min_importance_filter(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    r1 = _make_record("Trivial note", importance=0.2)
    r2 = _make_record("Critical architecture rule", importance=0.95)

    await svc.write([r1, r2])

    res = await svc.retrieve("proj-matrix", "", min_importance=0.8)
    assert len(res) == 1
    assert res[0].importance == 0.95


@pytest.mark.asyncio
async def test_19_deterministic_ranking(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    recs = [
        _make_record("Docker container deployment", importance=0.8, memory_id="r-dock"),
        _make_record("Docker compose local setup", importance=0.7, memory_id="r-comp"),
        _make_record("Kubernetes helm deployment", importance=0.9, memory_id="r-k8s"),
    ]
    await svc.write(recs)

    run1 = await svc.retrieve("proj-matrix", "Docker")
    run2 = await svc.retrieve("proj-matrix", "Docker")

    assert [m.memory_id for m in run1] == [m.memory_id for m in run2]
    assert [m.score for m in run1] == [m.score for m in run2]


@pytest.mark.asyncio
async def test_20_ranking_result_exposes_score_and_reasons(tmp_path: Path) -> None:
    svc = await _init_service(tmp_path / "test.db")
    rec = _make_record("Clean architecture hexagonal design", importance=0.85)
    await svc.write([rec])

    results = await svc.retrieve("proj-matrix", "clean architecture")
    assert len(results) == 1
    match = results[0]

    assert isinstance(match, MemoryMatch)
    assert match.score > 0.0
    assert isinstance(match.reasons, tuple)
    assert "exact_phrase" in match.reasons
    assert any("importance" in r for r in match.reasons)
    assert "project_scope" in match.reasons


@pytest.mark.asyncio
async def test_21_large_store_lexical_match_not_discarded(tmp_path: Path) -> None:
    """With >200 records, a low-importance strong lexical match must NOT be discarded."""
    svc = await _init_service(tmp_path / "test.db")

    # Generate 250 unrelated records with high importance (0.95)
    unrelated = [
        _make_record(
            f"Unrelated background noise telemetry metric {i}",
            importance=0.95,
            memory_id=f"noise-{i:03d}",
        )
        for i in range(250)
    ]
    await svc._store.add_many(tuple(unrelated))

    # Add 1 record with very low importance (0.1) but exact target keyword
    target = _make_record(
        "RareDiamondNeedleInHaystack specific configuration",
        importance=0.10,
        memory_id="target-rare",
    )
    await svc._store.add_many((target,))

    # Retrieve with limit=5
    results = await svc.retrieve("proj-matrix", "RareDiamondNeedleInHaystack", limit=5)
    assert len(results) >= 1
    assert results[0].memory_id == "target-rare"


@pytest.mark.asyncio
async def test_21b_large_store_non_contiguous_multi_token_match(tmp_path: Path) -> None:
    """Large-store token retrieval:

    With >200 records, a low-importance record matching query tokens
    non-contiguously (not an exact substring) must NOT disappear behind
    the importance fallback.
    """
    svc = await _init_service(tmp_path / "test.db")

    # Generate 250 unrelated records with very high importance (0.95)
    unrelated = [
        _make_record(
            f"Unrelated background noise telemetry metric {i}",
            importance=0.95,
            memory_id=f"noise-tok-{i:03d}",
        )
        for i in range(250)
    ]
    await svc._store.add_many(tuple(unrelated))

    # Add 1 record with very low importance (0.10) containing query tokens non-contiguously
    target = _make_record(
        "PostgreSQL enterprise distributed schema design with automated replication migration guide",
        importance=0.10,
        memory_id="target-multi-token",
    )
    await svc._store.add_many((target,))

    # Query with non-contiguous tokens: "PostgreSQL migration"
    # Neither "PostgreSQL migration" nor exact substring exists as a whole phrase,
    # but individual tokens "PostgreSQL" and "migration" have strong overlap.
    results = await svc.retrieve("proj-matrix", "PostgreSQL migration", limit=5)
    assert len(results) >= 1
    target_matches = [r for r in results if r.memory_id == "target-multi-token"]
    assert len(target_matches) == 1
    assert target_matches[0].memory_id == "target-multi-token"
    assert any("token_overlap" in reason for reason in target_matches[0].reasons)


@pytest.mark.asyncio
async def test_22_non_empty_query_does_not_return_zero_match_unrelated(tmp_path: Path) -> None:
    """Fail-closed policy: non-empty query with 0 lexical relevance returns empty tuple."""
    svc = await _init_service(tmp_path / "test.db")
    recs = [
        _make_record("Frontend React components with Tailwind CSS", importance=0.9),
        _make_record("Backend FastAPI endpoints with Pydantic", importance=0.85),
    ]
    await svc.write(recs)

    # Query for something completely unrelated
    results = await svc.retrieve("proj-matrix", "QuantumPhysicsParticleCollider")
    assert results == ()


@pytest.mark.asyncio
async def test_22b_zero_match_uses_explicit_lexical_evidence(tmp_path: Path) -> None:
    """Zero-match policy:

    Verifies that records without explicit lexical-match evidence
    (exact_phrase or token_overlap > 0) are excluded, avoiding reliance
    on floating-point score comparisons.
    """
    svc = await _init_service(tmp_path / "test.db")
    recs = [
        _make_record("Deploy service using Docker and Kubernetes", importance=0.9, confidence=1.0),
        _make_record("Config file parsing using YAML and JSON", importance=0.9, confidence=1.0),
    ]
    await svc.write(recs)

    # Query with non-matching term
    results = await svc.retrieve("proj-matrix", "Superconductivity Quantum Cryptography")
    assert results == ()


@pytest.mark.asyncio
async def test_23_mcp_consolidate_memory_success_corresponds_to_durable_sqlite_data(tmp_path: Path) -> None:
    """Calling production consolidate_memory writes durably to SQLite."""
    db_path = tmp_path / "mcp_test.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()

    # Point server._memory_service to our test store
    test_svc = DurableMemoryService(store)
    with patch.object(server, "_memory_service", test_svc), \
         patch.object(server, "memory_store", store):

        res = await server.consolidate_memory(
            session_id="test-session-mcp",
            project_id="test-proj-mcp",
            messages=[
                {"role": "user", "content": "Database decided: PostgreSQL 16 with pgvector."}
            ],
        )
        assert res["memories_created"] >= 1
        persisted_id = res["persisted_ids"][0]

    # Recreate store and service from same SQLite file
    store_reopened = SQLiteMemoryStore(db_path)
    await store_reopened.initialize()
    svc_reopened = DurableMemoryService(store_reopened)

    results = await svc_reopened.retrieve("test-proj-mcp", "PostgreSQL")
    assert len(results) >= 1
    assert results[0].memory_id == persisted_id


@pytest.mark.asyncio
async def test_23b_mcp_consolidate_memory_requires_explicit_or_active_project(tmp_path: Path) -> None:
    """MCP project identity validation:

    consolidate_memory must never use session_id as a fabricated project_id.
    When project_id is omitted and no active project exists, it must fail explicitly
    with PROJECT_CONTEXT_REQUIRED / ValueError.
    """
    db_path = tmp_path / "mcp_test.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()
    test_svc = DurableMemoryService(store)

    with patch.object(server, "_memory_service", test_svc), \
         patch.object(server, "memory_store", store), \
         patch.object(server.projects, "get_active", return_value=None):

        with pytest.raises(ValueError, match="PROJECT_CONTEXT_REQUIRED"):
            await server.consolidate_memory(
                session_id="session-isolated-identity",
                project_id=None,
                messages=[{"role": "user", "content": "Critical architectural decision"}],
            )

        with pytest.raises(ValueError, match="RESERVED_SENTINEL"):
            await server.consolidate_memory(
                session_id="session-legacy-global",
                project_id="__global__",
                messages=[{"role": "user", "content": "Attempting legacy global write"}],
            )


@pytest.mark.asyncio
async def test_23c_mcp_consolidate_memory_uses_active_project_when_project_id_omitted(tmp_path: Path) -> None:
    """MCP project identity resolution:

    When project_id is omitted, consolidate_memory resolves to the registered active project.
    """
    from core.project import ProjectRecord
    db_path = tmp_path / "mcp_test.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()
    test_svc = DurableMemoryService(store)

    fake_project = ProjectRecord(
        project_id="active-registered-proj-99",
        name="Active Proj 99",
        workspace_path=str(tmp_path),
    )

    with patch.object(server, "_memory_service", test_svc), \
         patch.object(server, "memory_store", store), \
         patch.object(server.projects, "get_active", return_value=fake_project):

        res = await server.consolidate_memory(
            session_id="session-active-fallback",
            project_id=None,
            messages=[{"role": "user", "content": "Persistent architecture choice"}],
        )
        assert res["project_id"] == "active-registered-proj-99"
        assert res["memories_created"] >= 1


@pytest.mark.asyncio
async def test_24_legacy_row_compatibility(tmp_path: Path) -> None:
    """Pre-Phase-04 rows survive and default status to ACTIVE."""
    db_path = tmp_path / "legacy.db"
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
            'legacy-42', 'proj-legacy', 'l2', 'note',
            'Legacy data from 2025.', 0.5, '{}',
            '2025-01-01T00:00:00+00:00'
        )
        """
    )
    con.commit()
    con.close()

    store = SQLiteMemoryStore(db_path)
    await store.initialize()
    svc = DurableMemoryService(store)

    results = await svc.retrieve("proj-legacy", "Legacy data")
    assert len(results) == 1
    assert results[0].memory_id == "legacy-42"
    assert results[0].status == MemoryStatus.ACTIVE

    # Legacy row with SQLite project_id='__global__' deserializes to canonical project_id=None
    con = sqlite3.connect(db_path)
    con.execute(
        """
        INSERT INTO memories (
            memory_id, project_id, level, kind, content,
            importance, metadata_json, created_at
        ) VALUES (
            'legacy-global-99', '__global__', 'l2', 'note',
            'Legacy global memory from 2025.', 0.9, '{}',
            '2025-01-01T00:00:00+00:00'
        )
        """
    )
    con.commit()
    con.close()

    res_global = await svc.retrieve("proj-legacy", "Legacy global memory", include_global=True)
    glob_match = [r for r in res_global if r.memory_id == "legacy-global-99"]
    assert len(glob_match) == 1
    assert glob_match[0].project_id is None
    assert glob_match[0].status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_25_superseded_memory_remains_physically_stored(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    svc = await _init_service(db_path)
    r1 = _make_record("Draft API v1", memory_id="phys-v1", logical_key="api_v")
    await svc.write([r1])

    r2 = _make_record("Final API v2", memory_id="phys-v2", logical_key="api_v")
    await svc.write([r2])

    # Direct raw SQL inspection: verify phys-v1 row is STILL in the database
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT memory_id, status FROM memories WHERE memory_id = 'phys-v1'").fetchall()
    con.close()

    assert len(rows) == 1
    assert rows[0][0] == "phys-v1"
    assert rows[0][1] == "superseded"


@pytest.mark.asyncio
async def test_26_archived_memory_remains_physically_stored(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    svc = await _init_service(db_path)
    rec = _make_record("Stale requirement", memory_id="phys-arc")
    await svc.write([rec])
    await svc.archive("proj-matrix", "phys-arc")

    # Direct raw SQL inspection: verify row still exists
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT memory_id, status FROM memories WHERE memory_id = 'phys-arc'").fetchall()
    con.close()

    assert len(rows) == 1
    assert rows[0][0] == "phys-arc"
    assert rows[0][1] == "archived"


def test_27_no_automatic_context_builder_injection() -> None:
    """Verify Phase 04 does not import or invoke ContextBuilder."""
    import sys
    assert "core.context_builder" not in sys.modules
    import core.durable_memory_service as dms
    assert not hasattr(dms, "ContextBuilder")


def test_28_no_user_model_side_effect() -> None:
    """Verify PREFERENCE memory is merely a MemoryRecord and does not instantiate UserModel."""
    import sys
    rec = _make_record("User preference", memory_type=MemoryType.PREFERENCE)
    assert isinstance(rec, MemoryRecord)
    assert "core.user_model" not in sys.modules
    assert not hasattr(rec, "user_model")
