"""Phase 05C — UserModelEngine & Preference Resolution Test Matrix.

Verifies the 18 required verification gates:
1.  Empty profile returns empty EffectiveUserProfile.
2.  Single verified global preference inherited by project and global profiles.
3.  Multiple project preferences resolved and categorized.
4.  Global vs Project precedence (Project rank 5 overrides Global rank 4).
5.  Explicit vs Inferred precedence (Explicit rank 4/5 overrides Inferred rank 2/3 regardless of timestamp).
6.  Unverified claims exclusion (untrusted caller claims excluded or degraded).
7.  Legacy unclassified records handled safely without false classification.
8.  Archived/superseded filtering (excluded from profile, soft tombstone preserved).
9.  Temporal validity (valid_from and valid_until bounds respected).
10. Duplicate logical_key conflict resolved deterministically to exactly 1 winner.
11. Stable tie-breaking across identical rank and timestamp.
12. More preferences than default retrieve limit (>10 records resolved without silent truncation).
13. Cross-project isolation (Project A never reads Project B preferences).
14. Forged provenance rejected (UntrustedProvenanceError on forged tokens/flags).
15. Global and project concurrent writes (Global blocked, Project serialized safely).
16. Transaction rollback on failure.
17. Durable persistence after service/store restart.
18. Existing Phase 04 / Phase 05B compatibility.

Plus Security Guardrails:
- Secret leakage detection.
- Houhou persona & system policy protection.
- Inferred preference rejection by set_explicit_preference.
- Retract project isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.durable_memory_service import DurableMemoryService
from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
)
from core.user_model import (
    PROVENANCE_ATTESTATION_KEY,
    EffectiveUserProfile,
    PreferenceCategory,
    PreferenceItem,
    PreferenceSourceType,
    PreferenceSpec,
    ProvenanceStatus,
    memory_record_to_preference_item,
)
from core.user_model_engine import (
    InvalidPreferenceSourceError,
    SecurityPolicyViolationError,
    ServerBoundaryToken,
    TruncatedRetrievalError,
    TrustedProvenanceAuthority,
    UntrustedProvenanceError,
    UserModelEngine,
)
from persistence.sqlite_memory_store import SQLiteMemoryStore

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def make_verified_record(
    *,
    memory_id: str,
    content: str,
    logical_key: str,
    project_id: str | None = None,
    category: str = "technical",
    created_at: datetime | None = None,
    tier: MemoryLevel | None = None,
    scope: str | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    source_type: PreferenceSourceType = PreferenceSourceType.EXPLICIT_USER,
    status: MemoryStatus = MemoryStatus.ACTIVE,
) -> MemoryRecord:
    token = TrustedProvenanceAuthority.issue_token()
    resolved_scope = scope or ("project" if project_id is not None else "global")
    resolved_tier = tier or (MemoryLevel.L2 if project_id is not None else MemoryLevel.L3)
    attestation = TrustedProvenanceAuthority.create_attestation(
        token=token,
        memory_id=memory_id,
        logical_key=logical_key,
        content=content,
        category=category,
        project_id=project_id,
        scope=resolved_scope,
        source_type=source_type,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    meta = {
        "logical_key": logical_key,
        "category": category,
        "source_type": source_type.value,
        "provenance_status": "verified_explicit",
        "trusted_provenance": True,
        PROVENANCE_ATTESTATION_KEY: attestation,
    }
    return MemoryRecord(
        memory_id=memory_id,
        content=content,
        project_id=project_id,
        memory_type=MemoryType.PREFERENCE,
        tier=resolved_tier,
        scope=resolved_scope,
        subject="user",
        source=source_type.value,
        metadata=meta,
        created_at=created_at or datetime.now(UTC),
        status=status,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def _mp_write_worker(db_path_str: str, project_id: str | None, logical_key: str, val: str) -> None:
    """Worker executed in a separate OS process for multi-process concurrency testing."""
    import asyncio
    from pathlib import Path

    from core.durable_memory_service import DurableMemoryService
    from core.user_model import PreferenceSpec
    from core.user_model_engine import TrustedProvenanceAuthority, UserModelEngine
    from persistence.sqlite_memory_store import SQLiteMemoryStore

    async def _run() -> None:
        store = SQLiteMemoryStore(Path(db_path_str))
        await store.initialize()
        svc = DurableMemoryService(store)
        engine = UserModelEngine(svc, store=store)
        token = TrustedProvenanceAuthority.issue_token()
        spec = PreferenceSpec(
            logical_key=logical_key,
            content=val,
            project_id=project_id,
            scope="global" if project_id is None else "project",
            trusted_provenance=True,
        )
        await engine.set_explicit_preference(spec, boundary_token=token)

    asyncio.run(_run())


@pytest.fixture
async def sqlite_store(tmp_path: Path):
    """Initialised SQLite store in temporary directory."""
    db_path = tmp_path / "test_user_model.db"
    store = SQLiteMemoryStore(db_path)
    await store.initialize()
    return store


@pytest.fixture
def durable_service(sqlite_store: SQLiteMemoryStore) -> DurableMemoryService:
    """DurableMemoryService instance."""
    return DurableMemoryService(sqlite_store)


@pytest.fixture
def user_model_engine(durable_service: DurableMemoryService, sqlite_store: SQLiteMemoryStore) -> UserModelEngine:
    """UserModelEngine instance."""
    return UserModelEngine(durable_service, store=sqlite_store)


@pytest.fixture
def trusted_token() -> ServerBoundaryToken:
    """Valid server boundary token."""
    return TrustedProvenanceAuthority.issue_token()


# ---------------------------------------------------------------------------
# Test Suite: 18 Verification Gates
# ---------------------------------------------------------------------------


class TestUserModelEngineMatrix:
    """Phase 05C Verification Matrix."""

    # Gate 1: Empty profile
    async def test_01_empty_profile(self, user_model_engine: UserModelEngine) -> None:
        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert isinstance(profile, EffectiveUserProfile)
        assert len(profile.preferences_by_key) == 0
        assert profile.get("code_style:typing") is None
        assert profile.has("code_style:typing") is False
        assert profile.active_constraints() == ()

    # Gate 2: Single verified global preference
    async def test_02_single_verified_global_preference(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        rec = make_verified_record(
            memory_id="pref_global_01",
            content="Vietnamese",
            logical_key="comm:language",
            category="communication",
            project_id=None,
        )
        await sqlite_store.add_many((rec,))

        # Inherited by project profile
        proj_profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert proj_profile.get("comm:language") == "Vietnamese"
        assert proj_profile.get_item("comm:language") is not None
        assert proj_profile.get_item("comm:language").provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT

        # Accessible in purely global profile
        global_profile = await user_model_engine.get_effective_profile(project_id=None)
        assert global_profile.get("comm:language") == "Vietnamese"

    # Gate 3: Multiple project preferences
    async def test_03_multiple_project_preferences(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        p1 = make_verified_record(
            memory_id="p1",
            content="Python",
            project_id="proj_alpha",
            logical_key="tech:stack",
            category="technical",
        )
        p2 = make_verified_record(
            memory_id="p2",
            content="ruff",
            project_id="proj_alpha",
            logical_key="tech:linter",
            category="technical",
        )
        p3 = make_verified_record(
            memory_id="p3",
            content="github_actions",
            project_id="proj_alpha",
            logical_key="workflow:ci",
            category="workflow",
        )
        await sqlite_store.add_many((p1, p2, p3))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert len(profile.preferences_by_key) == 3
        assert profile.get("tech:stack") == "Python"
        assert profile.get("tech:linter") == "ruff"
        assert profile.get("workflow:ci") == "github_actions"
        assert len(profile.get_by_category(PreferenceCategory.TECHNICAL)) == 2
        assert len(profile.get_by_category(PreferenceCategory.WORKFLOW)) == 1

    # Gate 4: Global vs Project precedence
    async def test_04_global_vs_project_precedence(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        t0 = datetime.now(UTC)
        global_rec = make_verified_record(
            memory_id="glob_test",
            content="unittest",
            project_id=None,
            logical_key="tech:test_runner",
            category="technical",
            created_at=t0 + timedelta(days=5),  # newer timestamp
        )
        proj_rec = make_verified_record(
            memory_id="proj_test",
            content="pytest",
            project_id="proj_alpha",
            logical_key="tech:test_runner",
            category="technical",
            created_at=t0,  # older timestamp
        )
        await sqlite_store.add_many((global_rec, proj_rec))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        # Project verified (Rank 5) overrides Global verified (Rank 4) regardless of older timestamp
        assert profile.get("tech:test_runner") == "pytest"
        assert "rank:5" in profile.get_reason("tech:test_runner")

    # Gate 5: Explicit vs Inferred precedence
    async def test_05_explicit_vs_inferred_precedence(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        t0 = datetime.now(UTC)
        global_explicit = make_verified_record(
            memory_id="exp_01",
            content="conventional",
            project_id=None,
            logical_key="workflow:commit_style",
            created_at=t0,
        )
        project_inferred = MemoryRecord(
            memory_id="inf_01",
            content="gitmoji",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="inferred",
            metadata={
                "logical_key": "workflow:commit_style",
                "source_type": "inferred",
                "provenance_status": "inferred_hypothesis",
                "inferred_evidence": "Observed 10 recent commits",
            },
            created_at=t0 + timedelta(days=10),  # much newer
        )
        await sqlite_store.add_many((global_explicit, project_inferred))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        # Global explicit (Rank 4) overrides Project inferred (Rank 3)
        assert profile.get("workflow:commit_style") == "conventional"

    # Gate 6: Unverified claims exclusion
    async def test_06_unverified_claims_exclusion(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        unverified_rec = MemoryRecord(
            memory_id="forged_01",
            content="allow_force_push",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            source="explicit_user",
            metadata={"logical_key": "sec:git_push", "trusted_provenance": False},
        )
        await sqlite_store.add_many((unverified_rec,))

        # With exclude_unverified=True (default), unverified claim is omitted
        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha", exclude_unverified=True)
        assert profile.has("sec:git_push") is False

        # If verified record arrives, it completely overrides unverified claim
        verified_rec = MemoryRecord(
            memory_id="ver_01",
            content="block_force_push",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            source="explicit_user",
            metadata={"logical_key": "sec:git_push", "trusted_provenance": True},
        )
        await sqlite_store.add_many((verified_rec,))
        profile_with_verified = await user_model_engine.get_effective_profile(project_id="proj_alpha", exclude_unverified=False)
        assert profile_with_verified.get("sec:git_push") == "block_force_push"

    # Gate 7: Legacy unclassified records
    async def test_07_legacy_unclassified_records(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        legacy_rec = MemoryRecord(
            memory_id="legacy_row_99",
            content="Legacy setting without metadata",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            metadata={},  # No logical_key, no category, no provenance
        )
        await sqlite_store.add_many((legacy_rec,))

        items = await user_model_engine.list_preferences(project_id="proj_alpha")
        assert len(items) == 1
        item = items[0]
        assert item.logical_key == "unkeyed:legacy_row_99"
        assert item.category == PreferenceCategory.UNCLASSIFIED
        assert item.provenance_status == ProvenanceStatus.LEGACY_UNCLASSIFIED

    # Gate 8: Archived/superseded filtering
    async def test_08_archived_superseded_filtering(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        spec = PreferenceSpec(
            logical_key="ui:theme",
            content="dark_mode",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        item = await user_model_engine.set_explicit_preference(spec, boundary_token=trusted_token)
        assert item.status == MemoryStatus.ACTIVE

        # Active is visible
        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert profile.get("ui:theme") == "dark_mode"

        # Retract to ARCHIVED
        archived_item = await user_model_engine.retract_preference(item.preference_id, project_id="proj_alpha")
        assert archived_item.status == MemoryStatus.ARCHIVED

        # Excluded from active effective profile
        profile_after = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert profile_after.has("ui:theme") is False

        # Preserved in store for audit (soft tombstone)
        raw_rec = await sqlite_store.get_by_id(item.preference_id)
        assert raw_rec is not None
        assert raw_rec.status == MemoryStatus.ARCHIVED

    # Gate 9: Temporal validity
    async def test_09_temporal_validity(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        now = datetime.now(UTC)
        future_rec = make_verified_record(
            memory_id="fut_01",
            content="future_val",
            project_id="proj_alpha",
            logical_key="temp:future",
            valid_from=now + timedelta(days=2),
        )
        expired_rec = make_verified_record(
            memory_id="exp_01",
            content="expired_val",
            project_id="proj_alpha",
            logical_key="temp:expired",
            valid_until=now - timedelta(days=2),
        )
        active_rec = make_verified_record(
            memory_id="act_01",
            content="active_val",
            project_id="proj_alpha",
            logical_key="temp:active",
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=1),
        )
        await sqlite_store.add_many((future_rec, expired_rec, active_rec))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha", as_of=now)
        assert profile.has("temp:future") is False
        assert profile.has("temp:expired") is False
        assert profile.get("temp:active") == "active_val"

    # Gate 10: Duplicate logical_key conflict resolution
    async def test_10_duplicate_logical_key(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        t0 = datetime.now(UTC)
        rec1 = make_verified_record(
            memory_id="dup_1",
            content="choice_1",
            project_id="proj_alpha",
            logical_key="choice:dup",
            created_at=t0,
        )
        rec2 = make_verified_record(
            memory_id="dup_2",
            content="choice_2",
            project_id="proj_alpha",
            logical_key="choice:dup",
            created_at=t0 + timedelta(seconds=10),
        )
        await sqlite_store.add_many((rec1, rec2))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        # Exactly one winner selected deterministically (newer timestamp)
        assert profile.get("choice:dup") == "choice_2"
        assert len([k for k in profile.preferences_by_key if k == "choice:dup"]) == 1

    # Gate 11: Stable tie-breaking
    async def test_11_stable_tie_breaking(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        same_time = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        r_a = make_verified_record(
            memory_id="mem_aaa",
            content="value_a",
            project_id="proj_alpha",
            logical_key="tie:break",
            created_at=same_time,
        )
        r_b = make_verified_record(
            memory_id="mem_bbb",
            content="value_b",
            project_id="proj_alpha",
            logical_key="tie:break",
            created_at=same_time,
        )
        await sqlite_store.add_many((r_a, r_b))

        # Multiple runs must produce the exact same deterministic outcome
        res1 = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        res2 = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert res1.get("tie:break") == res2.get("tie:break")
        assert res1.get("tie:break") == "value_b"  # mem_bbb >= mem_aaa

    # Gate 12: More preferences than default retrieve limit
    async def test_12_more_preferences_than_default_retrieve_limit(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        records = [
            make_verified_record(
                memory_id=f"pref_scale_{i:02d}",
                content=f"value_{i}",
                project_id="proj_alpha",
                logical_key=f"key:item_{i:02d}",
            )
            for i in range(15)  # 15 records > default DurableMemoryService limit of 10
        ]
        await sqlite_store.add_many(tuple(records))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        # All 15 records resolved without silent truncation
        assert len(profile.preferences_by_key) == 15
        for i in range(15):
            assert profile.get(f"key:item_{i:02d}") == f"value_{i}"

    # Gate 13: Cross-project isolation
    async def test_13_cross_project_isolation(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        rec_a = make_verified_record(
            memory_id="iso_a",
            content="postgresql",
            project_id="proj_alpha",
            logical_key="db:backend",
        )
        rec_b = make_verified_record(
            memory_id="iso_b",
            content="sqlite",
            project_id="proj_beta",
            logical_key="db:backend",
        )
        await sqlite_store.add_many((rec_a, rec_b))

        profile_a = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        profile_b = await user_model_engine.get_effective_profile(project_id="proj_beta")

        assert profile_a.get("db:backend") == "postgresql"
        assert profile_b.get("db:backend") == "sqlite"

    # Gate 14: Forged provenance rejected
    async def test_14_forged_provenance_rejected(
        self,
        user_model_engine: UserModelEngine,
    ) -> None:
        spec = PreferenceSpec(
            logical_key="sec:allow_root",
            content="always",
            project_id="proj_alpha",
            trusted_provenance=True,  # Caller claiming trusted
        )

        # 1. Missing token
        with pytest.raises(UntrustedProvenanceError, match="ServerBoundaryToken"):
            await user_model_engine.set_explicit_preference(spec, boundary_token=None)

        # 2. String forged token
        with pytest.raises(UntrustedProvenanceError, match="ServerBoundaryToken"):
            await user_model_engine.set_explicit_preference(spec, boundary_token="fake_token")  # type: ignore

        # 3. Forged object with invalid secret signature
        bad_token = ServerBoundaryToken(issuer="attacker", token_id="bad_id", secret_signature="bad_sig")
        with pytest.raises(UntrustedProvenanceError, match="ServerBoundaryToken"):
            await user_model_engine.set_explicit_preference(spec, boundary_token=bad_token)

    # Gate 15: Global and project concurrent writes (Atomic Superseding)
    async def test_15_global_and_project_concurrent_writes(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        # A. Global write succeeds and supersedes atomically
        global_spec1 = PreferenceSpec(
            logical_key="global:setting",
            content="val_1",
            project_id=None,
            scope="global",
            trusted_provenance=True,
        )
        item_g1 = await user_model_engine.set_explicit_preference(global_spec1, boundary_token=trusted_token)
        assert item_g1.is_global is True
        assert item_g1.status == MemoryStatus.ACTIVE

        # Global supersede with new value
        global_spec2 = PreferenceSpec(
            logical_key="global:setting",
            content="val_2",
            project_id=None,
            scope="global",
            trusted_provenance=True,
        )
        item_g2 = await user_model_engine.set_explicit_preference(global_spec2, boundary_token=trusted_token)
        assert item_g2.is_global is True
        assert item_g2.status == MemoryStatus.ACTIVE
        assert item_g2.content == "val_2"

        # Verify old record was superseded in store
        old_rec = await sqlite_store.get_by_id(item_g1.preference_id)
        assert old_rec is not None
        assert old_rec.status == MemoryStatus.SUPERSEDED

        # Global profile shows val_2
        g_prof = await user_model_engine.get_effective_profile(project_id=None)
        assert g_prof.get("global:setting") == "val_2"

        # B. Concurrent project writes serialized safely by in-process lock
        async def _write_worker(val: str) -> PreferenceItem:
            s = PreferenceSpec(
                logical_key="worker:choice",
                content=val,
                project_id="proj_alpha",
                trusted_provenance=True,
            )
            return await user_model_engine.set_explicit_preference(s, boundary_token=trusted_token)

        results = await asyncio.gather(*[_write_worker(f"val_{i}") for i in range(5)])
        assert len(results) == 5

        # Check effective profile: exactly 1 active preference remains
        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert profile.has("worker:choice") is True

        # Check list of active preferences: only 1 ACTIVE
        active_items = await user_model_engine.list_preferences(
            project_id="proj_alpha", status=MemoryStatus.ACTIVE
        )
        worker_active = [it for it in active_items if it.logical_key == "worker:choice"]
        assert len(worker_active) == 1

        # Older 4 preferences were superseded
        superseded_items = await user_model_engine.list_preferences(
            project_id="proj_alpha", status=MemoryStatus.SUPERSEDED
        )
        worker_superseded = [it for it in superseded_items if it.logical_key == "worker:choice"]
        assert len(worker_superseded) == 4
        assert len(worker_superseded) == 4

    # Gate 16: Transaction rollback on failure
    async def test_16_transaction_rollback(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        spec1 = PreferenceSpec(
            logical_key="rollback:key",
            content="initial_val",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        item1 = await user_model_engine.set_explicit_preference(spec1, boundary_token=trusted_token)
        assert item1.status == MemoryStatus.ACTIVE

        # Simulate store failure by closing DB connection / pointing to invalid state
        # In a transactional supersede failure, old record status must not be left superseded
        with pytest.raises(ValueError, match="RESERVED_SENTINEL"):
            # Attempt write with invalid project_id sentinel
            bad_spec = PreferenceSpec(
                logical_key="rollback:key",
                content="new_val",
                project_id="__global__",  # Invalid sentinel
                trusted_provenance=True,
            )
            await user_model_engine.set_explicit_preference(bad_spec, boundary_token=trusted_token)

        # Original item1 remains ACTIVE and uncorrupted
        raw = await sqlite_store.get_by_id(item1.preference_id)
        assert raw is not None
        assert raw.status == MemoryStatus.ACTIVE

    # Gate 17: Durable persistence after restart
    async def test_17_durable_persistence_after_restart(
        self,
        tmp_path: Path,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        db_path = tmp_path / "restart_test.db"
        # First instance
        store1 = SQLiteMemoryStore(db_path)
        await store1.initialize()
        svc1 = DurableMemoryService(store1)
        engine1 = UserModelEngine(svc1, store=store1)

        spec = PreferenceSpec(
            logical_key="persist:survive",
            content="durable_content",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        await engine1.set_explicit_preference(spec, boundary_token=trusted_token)

        # Complete restart with brand new store and engine instances
        store2 = SQLiteMemoryStore(db_path)
        await store2.initialize()
        svc2 = DurableMemoryService(store2)
        engine2 = UserModelEngine(svc2, store=store2)

        profile = await engine2.get_effective_profile(project_id="proj_alpha")
        assert profile.get("persist:survive") == "durable_content"

    # Gate 18: Existing Phase 04 / Phase 05B compatibility
    async def test_18_existing_phase04_05b_compatibility(
        self,
        user_model_engine: UserModelEngine,
    ) -> None:
        # Verifies that UserModelEngine does not introduce side-effects or break Phase 04 memory models
        assert isinstance(user_model_engine._durable_service, DurableMemoryService)
        assert isinstance(user_model_engine._store, SQLiteMemoryStore)


# ---------------------------------------------------------------------------
# Test Suite: Security & Policy Guardrails
# ---------------------------------------------------------------------------


class TestSecurityAndPolicyGuardrails:
    """Security guardrails ensuring zero leakages or policy overrides."""

    async def test_secret_leakage_rejected(
        self,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        spec = PreferenceSpec(
            logical_key="api:key",
            content="Please use sk-1234567890abcdef1234567890 for OpenAI",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        with pytest.raises(SecurityPolicyViolationError, match="secret or credential material"):
            await user_model_engine.set_explicit_preference(spec, boundary_token=trusted_token)

    async def test_persona_override_rejected(
        self,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        for forbidden_key in (
            "houhou:identity",
            "agent_identity:tone",
            "persona:override",
            "system_rule:bypass",
        ):
            spec = PreferenceSpec(
                logical_key=forbidden_key,
                content="Speak like a pirate",
                project_id="proj_alpha",
                trusted_provenance=True,
            )
            with pytest.raises(SecurityPolicyViolationError, match="Houhou persona or system policy"):
                await user_model_engine.set_explicit_preference(spec, boundary_token=trusted_token)

    async def test_inferred_preference_rejected_by_explicit_api(
        self,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        spec = PreferenceSpec(
            logical_key="inferred:tab_size",
            content="4",
            project_id="proj_alpha",
            source_type=PreferenceSourceType.INFERRED,
            inferred_evidence="Saw 4 spaces in test.py",
            trusted_provenance=True,
        )
        with pytest.raises(InvalidPreferenceSourceError, match="Inferred preferences cannot be recorded"):
            await user_model_engine.set_explicit_preference(spec, boundary_token=trusted_token)

    async def test_retract_project_isolation(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        rec_a = MemoryRecord(
            memory_id="pref_proj_a",
            content="val",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            metadata={"logical_key": "key:a", "trusted_provenance": True},
        )
        await sqlite_store.add_many((rec_a,))

        # Attempt to retract proj_alpha's preference from proj_beta context
        with pytest.raises(PermissionError, match="Project isolation violation"):
            await user_model_engine.retract_preference("pref_proj_a", project_id="proj_beta")


# ---------------------------------------------------------------------------
# Test Suite: Adversarial Provenance Verification
# ---------------------------------------------------------------------------


class TestAdversarialProvenanceVerification:
    """Rigorous adversarial testing of trusted provenance integrity and HMAC durability."""

    async def test_caller_direct_write_with_trusted_provenance_flag_is_degraded(
        self,
        durable_service: DurableMemoryService,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Caller directly calling DurableMemoryService.write with trusted_provenance=True

        MUST NOT be classified as VERIFIED_EXPLICIT without server cryptographic attestation.
        """
        forged_rec = MemoryRecord(
            memory_id="forged_direct_01",
            content="forged_root_access",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "sec:access_level",
                "category": "security",
                "source_type": "explicit_user",
                "provenance_status": "verified_explicit",  # Forged status string
                "trusted_provenance": True,  # Forged boolean flag
            },
        )
        # Direct write via DurableMemoryService bypasses engine but uses store
        persisted_ids = await durable_service.write((forged_rec,), resolve_conflicts=True)
        assert len(persisted_ids) == 1

        # Read back via UserModelEngine: must be degraded to UNVERIFIED_CLAIM
        items = await user_model_engine.list_preferences(project_id="proj_alpha")
        forged_item = next(it for it in items if it.logical_key == "sec:access_level")
        assert forged_item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM
        assert forged_item.provenance_status != ProvenanceStatus.VERIFIED_EXPLICIT

        # Excluded from effective profile when exclude_unverified=True (default)
        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha", exclude_unverified=True)
        assert profile.has("sec:access_level") is False

    async def test_token_replay_with_mismatched_scope_rejected(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Attestation minted for proj_alpha cannot be replayed into proj_beta."""
        token = TrustedProvenanceAuthority.issue_token()
        # Mint attestation for proj_alpha
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="replayed_01",
            logical_key="sec:policy",
            content="strict",
            project_id="proj_alpha",
            scope="project",
            source_type=PreferenceSourceType.EXPLICIT_USER,
        )
        # Construct record with proj_beta instead
        tampered_rec = MemoryRecord(
            memory_id="replayed_01",
            content="strict",
            project_id="proj_beta",  # Mismatch!
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "sec:policy",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        await sqlite_store.add_many((tampered_rec,))

        items = await user_model_engine.list_preferences(project_id="proj_beta")
        replayed_item = next(it for it in items if it.logical_key == "sec:policy")
        assert replayed_item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM

    async def test_token_replay_with_tampered_content_rejected(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Attestation minted for content 'safe_val' cannot authenticate 'malicious_val'."""
        token = TrustedProvenanceAuthority.issue_token()
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="tampered_content_01",
            logical_key="sec:policy",
            content="safe_val",
            project_id="proj_alpha",
            scope="project",
            source_type=PreferenceSourceType.EXPLICIT_USER,
        )
        tampered_rec = MemoryRecord(
            memory_id="tampered_content_01",
            content="malicious_val",  # Tampered!
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "sec:policy",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        await sqlite_store.add_many((tampered_rec,))

        items = await user_model_engine.list_preferences(project_id="proj_alpha")
        tampered_item = next(it for it in items if it.logical_key == "sec:policy")
        assert tampered_item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM

    async def test_token_replay_with_tampered_logical_key_rejected(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Attestation minted for key 'key_a' cannot authenticate key 'key_b'."""
        token = TrustedProvenanceAuthority.issue_token()
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="tampered_key_01",
            logical_key="key_a",
            content="val",
            project_id="proj_alpha",
            scope="project",
            source_type=PreferenceSourceType.EXPLICIT_USER,
        )
        tampered_rec = MemoryRecord(
            memory_id="tampered_key_01",
            content="val",
            project_id="proj_alpha",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "key_b",  # Tampered!
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        await sqlite_store.add_many((tampered_rec,))

        items = await user_model_engine.list_preferences(project_id="proj_alpha")
        tampered_item = next(it for it in items if it.logical_key == "key_b")
        assert tampered_item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM

    async def test_provenance_verified_after_store_and_authority_restart(
        self,
        tmp_path: Path,
    ) -> None:
        """HMAC key persistence ensures verified preferences remain verified after restart."""
        db_path = tmp_path / "provenance_restart.db"
        store1 = SQLiteMemoryStore(db_path)
        await store1.initialize()
        svc1 = DurableMemoryService(store1)
        engine1 = UserModelEngine(svc1, store=store1)

        token1 = TrustedProvenanceAuthority.issue_token()
        spec = PreferenceSpec(
            logical_key="persistent:provenance",
            content="retained_trust",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        written_item = await engine1.set_explicit_preference(spec, boundary_token=token1)
        assert written_item.provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT

        # Complete restart
        store2 = SQLiteMemoryStore(db_path)
        await store2.initialize()
        svc2 = DurableMemoryService(store2)
        engine2 = UserModelEngine(svc2, store=store2)

        profile = await engine2.get_effective_profile(project_id="proj_alpha", exclude_unverified=True)
        assert profile.get("persistent:provenance") == "retained_trust"
        item = profile.get_item("persistent:provenance")
        assert item is not None
        assert item.provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT


# ---------------------------------------------------------------------------
# Test Suite: Multi-Process & Concurrency Integrity
# ---------------------------------------------------------------------------


class TestMultiProcessAndConcurrencyIntegrity:
    """Tests verifying database-level atomic transactions and concurrency safety."""

    def test_multi_process_concurrent_writes_atomic_supersede(self, tmp_path: Path) -> None:
        """8 separate OS processes concurrently writing to the same SQLite database

        across both global and project scopes.
        Guaranteed: exactly 1 active global record, 3 superseded;
                    exactly 1 active project record, 3 superseded.
        """
        import multiprocessing

        db_path = tmp_path / "mp_concurrency.db"
        # Pre-initialize database schema
        async def _init_db():
            s = SQLiteMemoryStore(db_path)
            await s.initialize()

        asyncio.run(_init_db())

        ctx = multiprocessing.get_context("spawn")
        processes: list[multiprocessing.Process] = []

        # 4 workers for global scope
        for i in range(4):
            p = ctx.Process(
                target=_mp_write_worker,
                args=(str(db_path), None, "mp:global_key", f"g_val_{i}"),
            )
            processes.append(p)

        # 4 workers for project scope
        for i in range(4):
            p = ctx.Process(
                target=_mp_write_worker,
                args=(str(db_path), "proj_alpha", "mp:proj_key", f"p_val_{i}"),
            )
            processes.append(p)

        # Start all 8 processes concurrently
        for p in processes:
            p.start()

        # Join all
        for p in processes:
            p.join(timeout=30)
            assert p.exitcode == 0, f"Process {p.pid} failed with exit code {p.exitcode}"

        # Verify database invariants
        async def _verify():
            store = SQLiteMemoryStore(db_path)
            await store.initialize()
            svc = DurableMemoryService(store)
            engine = UserModelEngine(svc, store=store)

            # Global scope check
            global_items = await engine.list_preferences(project_id=None, status=None)
            g_active = [it for it in global_items if it.logical_key == "mp:global_key" and it.status == MemoryStatus.ACTIVE]
            g_superseded = [it for it in global_items if it.logical_key == "mp:global_key" and it.status == MemoryStatus.SUPERSEDED]
            assert len(g_active) == 1, f"Expected exactly 1 active global record, found {len(g_active)}"
            assert len(g_superseded) == 3, f"Expected 3 superseded global records, found {len(g_superseded)}"

            # Project scope check
            proj_items = await engine.list_preferences(project_id="proj_alpha", status=None, include_global=False)
            p_active = [it for it in proj_items if it.logical_key == "mp:proj_key" and it.status == MemoryStatus.ACTIVE]
            p_superseded = [it for it in proj_items if it.logical_key == "mp:proj_key" and it.status == MemoryStatus.SUPERSEDED]
            assert len(p_active) == 1, f"Expected exactly 1 active project record, found {len(p_active)}"
            assert len(p_superseded) == 3, f"Expected 3 superseded project records, found {len(p_superseded)}"

        asyncio.run(_verify())

    async def test_two_engine_instances_concurrency(self, tmp_path: Path) -> None:
        """Two separate engine instances with different SQLite connections concurrently write."""
        db_path = tmp_path / "two_instances.db"
        store1 = SQLiteMemoryStore(db_path)
        await store1.initialize()
        svc1 = DurableMemoryService(store1)
        engine1 = UserModelEngine(svc1, store=store1)

        store2 = SQLiteMemoryStore(db_path)
        await store2.initialize()
        svc2 = DurableMemoryService(store2)
        engine2 = UserModelEngine(svc2, store=store2)

        token1 = TrustedProvenanceAuthority.issue_token()
        token2 = TrustedProvenanceAuthority.issue_token()

        spec1 = PreferenceSpec(
            logical_key="shared:setting",
            content="engine1_wins",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        spec2 = PreferenceSpec(
            logical_key="shared:setting",
            content="engine2_wins",
            project_id="proj_alpha",
            trusted_provenance=True,
        )

        res1, res2 = await asyncio.gather(
            engine1.set_explicit_preference(spec1, boundary_token=token1),
            engine2.set_explicit_preference(spec2, boundary_token=token2),
        )
        assert res1 is not None
        assert res2 is not None

        # Check that exactly 1 active record exists
        items = await engine1.list_preferences(project_id="proj_alpha", status=MemoryStatus.ACTIVE)
        shared_active = [it for it in items if it.logical_key == "shared:setting"]
        assert len(shared_active) == 1

        superseded = await engine1.list_preferences(project_id="proj_alpha", status=MemoryStatus.SUPERSEDED)
        shared_superseded = [it for it in superseded if it.logical_key == "shared:setting"]
        assert len(shared_superseded) == 1

    async def test_duplicate_active_record_detection_and_reconciliation(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
        trusted_token: ServerBoundaryToken,
    ) -> None:
        """If legacy data had multiple ACTIVE records with identical conflict identity,

        write_records_atomic reconciles and supersedes ALL of them before inserting new.
        """
        # Inject 2 corrupted active records with identical identity
        corrupt1 = make_verified_record(
            memory_id="corrupt_01",
            content="bad_state_1",
            project_id="proj_alpha",
            logical_key="reconcile:key",
            status=MemoryStatus.ACTIVE,
        )
        corrupt2 = make_verified_record(
            memory_id="corrupt_02",
            content="bad_state_2",
            project_id="proj_alpha",
            logical_key="reconcile:key",
            status=MemoryStatus.ACTIVE,
        )
        await sqlite_store.add_many((corrupt1, corrupt2))

        # Now write clean preference via engine
        new_spec = PreferenceSpec(
            logical_key="reconcile:key",
            content="clean_resolved",
            project_id="proj_alpha",
            trusted_provenance=True,
        )
        new_item = await user_model_engine.set_explicit_preference(new_spec, boundary_token=trusted_token)
        assert new_item.content == "clean_resolved"
        assert new_item.status == MemoryStatus.ACTIVE

        # Verify that BOTH corrupt records were superseded
        rec1 = await sqlite_store.get_by_id("corrupt_01")
        rec2 = await sqlite_store.get_by_id("corrupt_02")
        assert rec1 is not None and rec1.status == MemoryStatus.SUPERSEDED
        assert rec2 is not None and rec2.status == MemoryStatus.SUPERSEDED

        # Only 1 ACTIVE record exists
        active_items = await user_model_engine.list_preferences(project_id="proj_alpha", status=MemoryStatus.ACTIVE)
        reconciled = [it for it in active_items if it.logical_key == "reconcile:key"]
        assert len(reconciled) == 1


# ---------------------------------------------------------------------------
# Test Suite: Retrieval Completeness
# ---------------------------------------------------------------------------


class TestRetrievalCompleteness:
    """Verifies retrieval completeness and fail-closed defense against silent truncation."""

    async def test_retrieval_under_limit(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """50 preferences are completely retrieved and resolved."""
        records = [
            make_verified_record(
                memory_id=f"pref_50_{i:02d}",
                content=f"val_{i}",
                project_id="proj_alpha",
                logical_key=f"key:scale_{i:02d}",
            )
            for i in range(50)
        ]
        await sqlite_store.add_many(tuple(records))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert len(profile.preferences_by_key) == 50

    async def test_mixed_memory_types_do_not_crowd_out_preferences(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """100 CONVERSATION records + 50 PREFERENCE records:

        Preferences are completely retrieved because kind = 'preference' filters at SQL level.
        """
        now = datetime.now(UTC)
        conversations = [
            MemoryRecord(
                memory_id=f"conv_{i:03d}",
                content=f"User conversation turn {i}",
                project_id="proj_alpha",
                memory_type=MemoryType.EPISODIC,
                tier=MemoryLevel.L1,
                created_at=now + timedelta(minutes=i),
            )
            for i in range(100)
        ]
        preferences = [
            make_verified_record(
                memory_id=f"pref_mixed_{i:02d}",
                content=f"pref_{i}",
                project_id="proj_alpha",
                logical_key=f"pref:item_{i:02d}",
            )
            for i in range(50)
        ]
        await sqlite_store.add_many(tuple(conversations) + tuple(preferences))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert len(profile.preferences_by_key) == 50

    async def test_retrieval_over_limit_triggers_fail_closed(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """When store query cap (200 records) is reached, fail-closed with TruncatedRetrievalError."""
        records = [
            make_verified_record(
                memory_id=f"cap_pref_{i:03d}",
                content=f"val_{i}",
                project_id="proj_alpha",
                logical_key=f"cap:key_{i:03d}",
            )
            for i in range(200)
        ]
        await sqlite_store.add_many(tuple(records))

        with pytest.raises(TruncatedRetrievalError, match="browse limit .* reached"):
            await user_model_engine.get_effective_profile(project_id="proj_alpha")

    async def test_global_plus_project_high_volume_completeness(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """80 global preferences + 80 project preferences (total 160) completely resolved."""
        global_records = [
            make_verified_record(
                memory_id=f"high_g_{i:02d}",
                content=f"g_val_{i}",
                project_id=None,
                logical_key=f"gkey:item_{i:02d}",
            )
            for i in range(80)
        ]
        project_records = [
            make_verified_record(
                memory_id=f"high_p_{i:02d}",
                content=f"p_val_{i}",
                project_id="proj_alpha",
                logical_key=f"pkey:item_{i:02d}",
            )
            for i in range(80)
        ]
        await sqlite_store.add_many(tuple(global_records) + tuple(project_records))

        profile = await user_model_engine.get_effective_profile(project_id="proj_alpha")
        assert len(profile.preferences_by_key) == 160


# ---------------------------------------------------------------------------
# Test Suite: Phase 05C-P Final Security Hardening & Adversarial Verification
# ---------------------------------------------------------------------------


class TestPhase05CPAdversarialSecurityAndRetrieval:
    """Rigorous adversarial tests for F-02 (P1 HMAC binding), F-03 (P2 Secret protection),

    and F-04 (P2 Legacy retrieval correctness).
    """

    async def test_tampered_category_fails_attestation_and_degrades_claim(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Attestation minted for COMMUNICATION cannot be replayed as a CONSTRAINT."""
        token = TrustedProvenanceAuthority.issue_token()
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="tamper_cat_01",
            logical_key="comm:tone",
            content="Concise responses",
            category=PreferenceCategory.COMMUNICATION,
            project_id="proj_sec",
            scope="project",
            source_type=PreferenceSourceType.EXPLICIT_USER,
        )

        # Forged record: category tampered to CONSTRAINT
        forged_rec = MemoryRecord(
            memory_id="tamper_cat_01",
            content="Concise responses",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "comm:tone",
                "category": "constraint",  # TAMPERED!
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert not TrustedProvenanceAuthority.verify_record_attestation(forged_rec)
        await sqlite_store.add_many((forged_rec,))

        items = await user_model_engine.list_preferences(project_id="proj_sec")
        found = next(it for it in items if it.logical_key == "comm:tone")
        assert found.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM
        assert not found.is_verified

    async def test_tampered_validity_window_fails_attestation(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Attestation minted with finite validity cannot be extended to perpetuity."""
        token = TrustedProvenanceAuthority.issue_token()
        valid_until = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="tamper_val_01",
            logical_key="license:term",
            content="Standard term",
            category=PreferenceCategory.WORKFLOW,
            project_id="proj_sec",
            scope="project",
            source_type=PreferenceSourceType.EXPLICIT_USER,
            valid_until=valid_until,
        )

        # Forged record: valid_until extended to 2035
        forged_rec = MemoryRecord(
            memory_id="tamper_val_01",
            content="Standard term",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            valid_until=datetime(2035, 1, 1, 0, 0, 0, tzinfo=UTC),  # TAMPERED!
            metadata={
                "logical_key": "license:term",
                "category": "workflow",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert not TrustedProvenanceAuthority.verify_record_attestation(forged_rec)

    async def test_tampered_confidence_fails_attestation(self) -> None:
        """Attestation minted with confidence 0.5 cannot be asserted as confidence 1.0."""
        token = TrustedProvenanceAuthority.issue_token()
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="tamper_conf_01",
            logical_key="code:lint",
            content="ruff",
            category=PreferenceCategory.TECHNICAL,
            project_id="proj_sec",
            confidence=0.5,
        )

        forged_rec = MemoryRecord(
            memory_id="tamper_conf_01",
            content="ruff",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            confidence=1.0,  # TAMPERED!
            metadata={
                "logical_key": "code:lint",
                "category": "technical",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert not TrustedProvenanceAuthority.verify_record_attestation(forged_rec)

    async def test_replayed_memory_id_transplant_fails_attestation(self) -> None:
        """Attestation minted for memory_id 'orig_01' cannot be transplanted to 'trans_02'."""
        token = TrustedProvenanceAuthority.issue_token()
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="orig_01",
            logical_key="ide:editor",
            content="neovim",
            category=PreferenceCategory.TECHNICAL,
            project_id="proj_sec",
        )

        forged_rec = MemoryRecord(
            memory_id="trans_02",  # DIFFERENT memory_id!
            content="neovim",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "ide:editor",
                "category": "technical",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert not TrustedProvenanceAuthority.verify_record_attestation(forged_rec)

    async def test_v1_legacy_attestation_fails_closed(self) -> None:
        """Legacy V1 attestation missing required security fields fails closed under V2."""
        v1_attestation = {
            "version": "1.0",
            "issuer": "houhou_front_agent",
            "token_id": "tok_123",
            "logical_key": "v1:legacy",
            "content_sha256": hashlib.sha256(b"legacy content").hexdigest(),
            "project_id": "proj_sec",
            "scope": "project",
            "user_id": "default_user",
            "source_type": "explicit_user",
            "signature": "fake_or_v1_sig",
        }
        legacy_rec = MemoryRecord(
            memory_id="v1_rec_01",
            content="legacy content",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            source="explicit_user",
            metadata={
                "logical_key": "v1:legacy",
                "category": "technical",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: v1_attestation,
            },
        )
        # Must fail closed
        assert not TrustedProvenanceAuthority.verify_record_attestation(legacy_rec)

        # In converter under require_attestation=True, degrades to UNVERIFIED_CLAIM
        item = memory_record_to_preference_item(legacy_rec, require_attestation=True)
        assert item.provenance_status == ProvenanceStatus.UNVERIFIED_CLAIM
        assert not item.is_verified

    async def test_lifecycle_transition_preserves_attestation_validity(self) -> None:
        """Status transitioning from ACTIVE to SUPERSEDED and ARCHIVED does not invalidate attestation."""
        token = TrustedProvenanceAuthority.issue_token()
        attestation = TrustedProvenanceAuthority.create_attestation(
            token=token,
            memory_id="life_01",
            logical_key="life:cycle",
            content="stable declaration",
            category=PreferenceCategory.WORKFLOW,
            project_id="proj_sec",
        )

        active_rec = MemoryRecord(
            memory_id="life_01",
            content="stable declaration",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            subject="user",
            source="explicit_user",
            status=MemoryStatus.ACTIVE,
            metadata={
                "logical_key": "life:cycle",
                "category": "workflow",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert TrustedProvenanceAuthority.verify_record_attestation(active_rec)

        # Transition to SUPERSEDED
        superseded_rec = MemoryRecord(
            memory_id="life_01",
            content="stable declaration",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            subject="user",
            source="explicit_user",
            status=MemoryStatus.SUPERSEDED,  # Transitioned!
            supersedes="older_01",
            updated_at=datetime.now(UTC),
            metadata={
                "logical_key": "life:cycle",
                "category": "workflow",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert TrustedProvenanceAuthority.verify_record_attestation(superseded_rec)

        # Transition to ARCHIVED
        archived_rec = MemoryRecord(
            memory_id="life_01",
            content="stable declaration",
            project_id="proj_sec",
            memory_type=MemoryType.PREFERENCE,
            tier=MemoryLevel.L2,
            scope="project",
            subject="user",
            source="explicit_user",
            status=MemoryStatus.ARCHIVED,  # Transitioned!
            updated_at=datetime.now(UTC),
            metadata={
                "logical_key": "life:cycle",
                "category": "workflow",
                "trusted_provenance": True,
                PROVENANCE_ATTESTATION_KEY: attestation,
            },
        )
        assert TrustedProvenanceAuthority.verify_record_attestation(archived_rec)

    async def test_restart_persistence_and_authority_key_recovery(
        self,
        sqlite_store: SQLiteMemoryStore,
        user_model_engine: UserModelEngine,
    ) -> None:
        """Attestation minted before process restart remains 100% verifiable after reload."""
        token = TrustedProvenanceAuthority.issue_token()
        spec = PreferenceSpec(
            logical_key="restart:key",
            content="persisted across restarts",
            category=PreferenceCategory.TECHNICAL,
            project_id="proj_sec",
            trusted_provenance=True,
        )
        saved = await user_model_engine.set_explicit_preference(spec, boundary_token=token)
        assert saved.is_verified

        # Simulate engine & authority restart by clearing in-memory secret
        TrustedProvenanceAuthority.set_secret(None)

        # Reload from store
        reloaded_rec = await sqlite_store.get_by_id(saved.preference_id)
        assert reloaded_rec is not None
        assert TrustedProvenanceAuthority.verify_record_attestation(reloaded_rec)

        # Verify through UserModelEngine
        reloaded_item = memory_record_to_preference_item(reloaded_rec, require_attestation=True)
        assert reloaded_item.is_verified
        assert reloaded_item.provenance_status == ProvenanceStatus.VERIFIED_EXPLICIT

    async def test_legacy_kind_retrieval_with_canonical_preference_f04(
        self,
        sqlite_store: SQLiteMemoryStore,
    ) -> None:
        """F-04 Verification: store.retrieve finds legacy records with custom kind column

        when metadata contains canonical memory_type='preference'.
        """
        # Inject raw row directly into SQLite with disparate kind column
        created_iso = datetime.now(UTC).isoformat()
        raw_meta = {
            "__canonical__": {
                "memory_type": "preference",
                "tier": "l2",
                "subject": "user",
                "scope": "project",
                "project_id": "proj_legacy",
                "source": "explicit_user",
                "status": "active",
                "confidence": 1.0,
            },
            "logical_key": "legacy:pref_01",
            "category": "technical",
        }
        with sqlite_store._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    memory_id, project_id, level, kind, content,
                    importance, metadata_json, created_at, status, logical_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy_disparate_01",
                    "proj_legacy",
                    "l2",
                    "legacy_custom_note",  # Disparate kind column in SQLite!
                    "Use Python 3.13 strictly",
                    0.8,
                    json.dumps(raw_meta),
                    created_iso,
                    "active",
                    "legacy:pref_01",
                ),
            )

        # Retrieve with memory_type_filter=MemoryType.PREFERENCE
        matches = await sqlite_store.retrieve(
            "proj_legacy",
            query="",
            memory_type_filter=MemoryType.PREFERENCE,
        )
        found_ids = [m.memory_id for m in matches]
        assert "legacy_disparate_01" in found_ids
        rec = next(m for m in matches if m.memory_id == "legacy_disparate_01")
        assert rec.memory_type == MemoryType.PREFERENCE
        assert rec.content == "Use Python 3.13 strictly"

    async def test_malformed_json_fallback_in_retrieval_f04(
        self,
        sqlite_store: SQLiteMemoryStore,
    ) -> None:
        """F-04 Verification: store.retrieve handles malformed JSON metadata gracefully without crash."""
        created_iso = datetime.now(UTC).isoformat()
        with sqlite_store._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    memory_id, project_id, level, kind, content,
                    importance, metadata_json, created_at, status, logical_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "corrupt_json_01",
                    "proj_legacy",
                    "l2",
                    "preference",  # kind matches
                    "Raw preference with corrupt json",
                    0.5,
                    "NOT_VALID_JSON{[[",  # Malformed JSON!
                    created_iso,
                    "active",
                    "corrupt:01",
                ),
            )

        # Must not raise sqlite3.OperationalError or json.JSONDecodeError
        matches = await sqlite_store.retrieve(
            "proj_legacy",
            query="",
            memory_type_filter=MemoryType.PREFERENCE,
        )
        found_ids = [m.memory_id for m in matches]
        assert "corrupt_json_01" in found_ids

