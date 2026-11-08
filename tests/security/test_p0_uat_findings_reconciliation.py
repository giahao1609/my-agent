"""Targeted Negative & Regression Test Suite for P0 UAT Findings Reconciliation.

Verifies:
1. Checkpoint metadata consistency: last_checkpoint_id updated on save and returned on resume.
2. Checkpoint task ownership validation and explicit project-level vs task-level scoping.
3. Resume project fallback to project-level checkpoint when active task has no checkpoint.
4. User preference semantic project mismatch rejection on set_user_preference.
5. ContextBuilder exclusion of project-mismatched preferences from prompt context.
6. Multi-tenant preference isolation and global inheritance boundary verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.context_builder import UnifiedContextBuilder
from core.durable_memory_service import DurableMemoryService
from core.project import ProjectRecord
from core.security_authorization import (
    SecurityAuthorizationError,
    SecurityAuthorizationManager,
)
from core.task import TaskRecord, TaskState
from core.user_model import (
    PreferenceCategory,
    PreferenceSpec,
    TrustedProvenanceAuthority,
)
from core.user_model_engine import UserModelEngine
from my_agent_mcp import server
from persistence.sqlite_checkpoint_store import SQLiteCheckpointStore
from persistence.sqlite_memory_store import SQLiteMemoryStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


@pytest.fixture
async def uat_fixture(tmp_path: Path):
    db_path = tmp_path / "uat_test.db"
    proj_store = SQLiteProjectStore(db_path)
    task_store = SQLiteTaskStore(db_path)
    ckpt_store = SQLiteCheckpointStore(db_path)
    mem_store = SQLiteMemoryStore(db_path)

    await proj_store.initialize()
    await task_store.initialize()
    await ckpt_store.initialize()
    await mem_store.initialize()

    # In conftest, MYAGENT_WORKSPACE is bound to tmp_path.
    # Therefore, proj_alpha's workspace is tmp_path.
    ws_alpha = tmp_path
    ws_beta = tmp_path / "external_beta"
    ws_beta.mkdir(parents=True, exist_ok=True)

    proj_alpha = ProjectRecord(
        project_id="proj-alpha",
        name="Project Alpha",
        workspace_path=str(ws_alpha),
        active_task_id="task-alpha-1",
    )
    proj_beta = ProjectRecord(
        project_id="proj-beta",
        name="Project Beta",
        workspace_path=str(ws_beta),
    )

    await proj_store.create(proj_alpha)
    await proj_store.create(proj_beta)

    task_alpha = TaskRecord(
        task_id="task-alpha-1",
        project_id="proj-alpha",
        objective="Alpha primary objective",
        state=TaskState.PLANNING,
    )
    task_beta = TaskRecord(
        task_id="task-beta-1",
        project_id="proj-beta",
        objective="Beta objective",
        state=TaskState.PLANNING,
    )

    await task_store.save(task_alpha)
    await task_store.save(task_beta)

    auth_mgr = SecurityAuthorizationManager(proj_store, server_workspace_root=ws_alpha)

    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "proj_store": proj_store,
        "task_store": task_store,
        "ckpt_store": ckpt_store,
        "mem_store": mem_store,
        "ws_alpha": ws_alpha,
        "ws_beta": ws_beta,
        "proj_alpha": proj_alpha,
        "proj_beta": proj_beta,
        "task_alpha": task_alpha,
        "task_beta": task_beta,
        "auth_mgr": auth_mgr,
    }


# ===========================================================================
# Finding 01: Checkpoint Metadata Consistency & Task Scoping
# ===========================================================================

@pytest.mark.asyncio
async def test_checkpoint_metadata_consistency_on_save_and_resume(uat_fixture, monkeypatch):
    """Saving a checkpoint updates project.last_checkpoint_id and resume_project returns matching IDs."""
    f = uat_fixture
    monkeypatch.setattr(server, "projects", f["proj_store"])
    monkeypatch.setattr(server, "tasks", f["task_store"])
    monkeypatch.setattr(server, "checkpoints", f["ckpt_store"])
    monkeypatch.setattr(server, "_auth_manager", f["auth_mgr"])

    # 1. Initially last_checkpoint_id is None
    proj_initial = await f["proj_store"].get("proj-alpha")
    assert proj_initial.last_checkpoint_id is None

    # 2. Save checkpoint
    res_save = await server.save_checkpoint(
        summary="Milestone 1 checkpoint",
        project_id="proj-alpha",
    )
    assert res_save["status"] == "saved"
    saved_cp_id = res_save["checkpoint"]["checkpoint_id"]
    assert saved_cp_id.startswith("cp-")
    assert res_save["checkpoint"]["task_id"] == "task-alpha-1"

    # 3. ProjectRecord in SQLite now stores the last_checkpoint_id
    proj_updated = await f["proj_store"].get("proj-alpha")
    assert proj_updated.last_checkpoint_id == saved_cp_id

    # 4. resume_project returns consistent metadata and checkpoint
    res_resume = await server.resume_project(project_id="proj-alpha")
    assert res_resume["status"] == "resumed"
    assert res_resume["project"]["last_checkpoint_id"] == saved_cp_id
    assert res_resume["checkpoint"]["checkpoint_id"] == saved_cp_id
    assert res_resume["checkpoint"]["task_id"] == "task-alpha-1"


@pytest.mark.asyncio
async def test_checkpoint_task_ownership_and_scoping_rules(uat_fixture, monkeypatch):
    """Validates explicit project-level vs task-level scoping, and blocks foreign task_id."""
    f = uat_fixture
    monkeypatch.setattr(server, "projects", f["proj_store"])
    monkeypatch.setattr(server, "tasks", f["task_store"])
    monkeypatch.setattr(server, "checkpoints", f["ckpt_store"])
    monkeypatch.setattr(server, "_auth_manager", f["auth_mgr"])

    # A. Explicit project-level checkpoint (task_id="")
    res_proj_level = await server.save_checkpoint(
        summary="Repository architecture snapshot",
        project_id="proj-alpha",
        task_id="",
    )
    assert res_proj_level["status"] == "saved"
    assert res_proj_level["checkpoint"]["task_id"] is None

    # B. Cross-project task IDOR attempt: passing task_beta-1 while in proj-alpha
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        await server.save_checkpoint(
            summary="Attacking task ownership",
            project_id="proj-alpha",
            task_id="task-beta-1",
        )
    assert exc_info.value.violation_type == "TASK_IDOR_VIOLATION"
    assert "does not belong" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resume_project_fallback_to_project_checkpoint(uat_fixture, monkeypatch):
    """When active task has no checkpoint, resume_project falls back to latest project-level checkpoint."""
    f = uat_fixture
    # Switch active task to a new task with no checkpoints
    new_task = TaskRecord(
        task_id="task-alpha-empty",
        project_id="proj-alpha",
        objective="Fresh empty task",
        state=TaskState.PLANNING,
    )
    await f["task_store"].save(new_task)
    proj = await f["proj_store"].get("proj-alpha")
    proj.active_task_id = "task-alpha-empty"
    await f["proj_store"].update(proj)

    monkeypatch.setattr(server, "projects", f["proj_store"])
    monkeypatch.setattr(server, "tasks", f["task_store"])
    monkeypatch.setattr(server, "checkpoints", f["ckpt_store"])
    monkeypatch.setattr(server, "_auth_manager", f["auth_mgr"])

    # Save a project-level checkpoint
    res_proj = await server.save_checkpoint(
        summary="Global project baseline",
        project_id="proj-alpha",
        task_id="",
    )
    proj_cp_id = res_proj["checkpoint"]["checkpoint_id"]

    # Resume project: should find the project-level checkpoint despite active_task_id having none
    res_resume = await server.resume_project(project_id="proj-alpha")
    assert res_resume["status"] == "resumed"
    assert res_resume["checkpoint"] is not None
    assert res_resume["checkpoint"]["checkpoint_id"] == proj_cp_id
    assert res_resume["checkpoint"]["task_id"] is None


# ===========================================================================
# Finding 02: User Preferences Semantic Project Alignment & Context Isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_set_user_preference_rejects_project_semantic_mismatch(uat_fixture, monkeypatch):
    """Rejects storing a preference that explicitly references a different project name."""
    f = uat_fixture
    monkeypatch.setattr(server, "projects", f["proj_store"])
    monkeypatch.setattr(server, "_auth_manager", f["auth_mgr"])

    # Attempt to store a preference for 'aitablelog' while authorized in 'proj-alpha'
    res = await server.set_user_preference(
        logical_key="git_account",
        content="Tài khoản git gắn với dự án aitablelog là aiot-haohg",
        project_id="proj-alpha",
    )
    assert res["status"] == "error"
    assert res["error_type"] == "ProjectSemanticMismatchError"
    assert "aitablelog" in res["message"]
    assert "Cross-project preference storage is rejected" in res["message"]

    # Storing a matching preference succeeds
    res_ok = await server.set_user_preference(
        logical_key="git_account",
        content="Tài khoản git thuộc dự án proj-alpha là alpha-dev",
        project_id="proj-alpha",
    )
    assert res_ok["status"] == "ok"
    assert res_ok["preference"]["logical_key"] == "git_account"


@pytest.mark.asyncio
async def test_context_builder_excludes_mismatched_preference(uat_fixture):
    """ContextBuilder filters out preferences whose content declares association with a different project."""
    f = uat_fixture
    mem_svc = DurableMemoryService(f["mem_store"])
    user_engine = UserModelEngine(durable_service=mem_svc)

    token = TrustedProvenanceAuthority.issue_token(issuer="houhou_front_agent")

    # 1. Historical mismatched record in proj-alpha
    spec_mismatched = PreferenceSpec(
        logical_key="git_email",
        content="Email git gắn với dự án aitablelog là dev@aitablelog.io",
        category=PreferenceCategory.TECHNICAL,
        project_id="proj-alpha",
        scope="project",
    )
    # 2. Legitimate record in proj-alpha
    spec_legit = PreferenceSpec(
        logical_key="code_style",
        content="Coding standard: always use Ruff and strict type hints",
        category=PreferenceCategory.TECHNICAL,
        project_id="proj-alpha",
        scope="project",
    )

    await user_engine.set_explicit_preference(spec_mismatched, boundary_token=token)
    await user_engine.set_explicit_preference(spec_legit, boundary_token=token)

    builder = UnifiedContextBuilder(
        user_model_engine=user_engine,
        memory_service=mem_svc,
        default_token_budget=4000,
    )

    # Retrieve preferences for proj-alpha
    prefs = await builder._retrieve_user_preferences(
        user_id="default_user",
        project_id="proj-alpha",
    )

    keys = [p.logical_key for p in prefs]
    assert "code_style" in keys, "Legitimate preference must be included"
    assert "git_email" not in keys, "Mismatched preference targeting aitablelog must be EXCLUDED from proj-alpha"


@pytest.mark.asyncio
async def test_multi_tenant_preference_isolation_and_global_inheritance(uat_fixture):
    """Ensures Project A preferences never leak to Project B, while clean Global preferences inherit correctly."""
    f = uat_fixture
    mem_svc = DurableMemoryService(f["mem_store"])
    user_engine = UserModelEngine(durable_service=mem_svc)

    token = TrustedProvenanceAuthority.issue_token(issuer="houhou_front_agent")

    # Project Alpha preference
    spec_alpha = PreferenceSpec(
        logical_key="db.pool",
        content="Alpha confidential database connection pooling",
        category=PreferenceCategory.TECHNICAL,
        project_id="proj-alpha",
        scope="project",
    )
    # Global preference (project_id=None, scope="global")
    spec_global = PreferenceSpec(
        logical_key="system.tz",
        content="Default timezone is UTC across all services",
        category=PreferenceCategory.TECHNICAL,
        project_id=None,
        scope="global",
    )

    await user_engine.set_explicit_preference(spec_alpha, boundary_token=token)
    await user_engine.set_explicit_preference(spec_global, boundary_token=token)

    # Profile for Alpha
    prof_alpha = await user_engine.get_effective_profile(project_id="proj-alpha")
    assert "db.pool" in prof_alpha.preferences_by_key
    assert "system.tz" in prof_alpha.preferences_by_key

    # Profile for Beta: must have system.tz but NEVER db.pool
    prof_beta = await user_engine.get_effective_profile(project_id="proj-beta")
    assert "system.tz" in prof_beta.preferences_by_key
    assert "db.pool" not in prof_beta.preferences_by_key, "Security Violation: Project Beta inherited Project Alpha preference!"
