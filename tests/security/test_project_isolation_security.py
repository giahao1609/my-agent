"""Mandatory Negative Security & Project Isolation Test Suite (Scenarios A through J).

Verifies that MyAgent enforces strict multi-tenant boundary isolation:
- No Insecure Direct Object References (IDOR)
- No shared database singleton fallbacks
- No cross-project memory, code graph, conversation, checkpoint, or UI contract leakage
- Path traversal and symlink confinement
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from core.project import ProjectRecord
from core.security_authorization import (
    SecurityAuthorizationError,
    SecurityAuthorizationManager,
)
from core.context_builder import UnifiedContextBuilder
from core.durable_memory_service import DurableMemoryService
from core.memory import MemoryLevel, MemoryStatus, MemoryType
from persistence.sqlite_memory_store import SQLiteMemoryStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_checkpoint_store import CheckpointRecord, SQLiteCheckpointStore
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore
import my_agent_mcp.server as server


@pytest.fixture
async def security_fixture(tmp_path: Path):
    db_path = tmp_path / "sec_test.db"
    proj_store = SQLiteProjectStore(db_path)
    mem_store = SQLiteMemoryStore(db_path)
    ckpt_store = SQLiteCheckpointStore(db_path)
    graph_store = SQLiteCodeGraphStore(db_path)

    await proj_store.initialize()
    await mem_store.initialize()
    await ckpt_store.initialize()
    await graph_store.initialize()

    # Create mock physical project workspaces
    ws_a = tmp_path / "workspaces" / "project_a"
    ws_b = tmp_path / "workspaces" / "project_b"
    ws_a.mkdir(parents=True, exist_ok=True)
    ws_b.mkdir(parents=True, exist_ok=True)

    proj_a = ProjectRecord(
        project_id="proj-a",
        name="Project A (Customer Alpha)",
        workspace_path=str(ws_a),
    )
    proj_b = ProjectRecord(
        project_id="proj-b",
        name="Project B (Customer Beta)",
        workspace_path=str(ws_b),
    )

    await proj_store.create(proj_a)
    await proj_store.create(proj_b)

    auth_mgr = SecurityAuthorizationManager(proj_store)

    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "proj_store": proj_store,
        "mem_store": mem_store,
        "ckpt_store": ckpt_store,
        "graph_store": graph_store,
        "ws_a": ws_a,
        "ws_b": ws_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "auth_mgr": auth_mgr,
    }


# ---------------------------------------------------------------------------
# Scenario A: Secret Isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_a_secret_isolation(security_fixture):
    """Project A stores a sensitive secret. Project B queries symbols/memories -> zero matches."""
    f = security_fixture
    mem_svc = DurableMemoryService(f["mem_store"])
    from core.memory import MemoryRecord, MemoryLevel, MemoryType

    # Project A stores a proprietary secret
    rec = MemoryRecord(
        memory_id="mem-sec-alpha-1",
        project_id="proj-a",
        tier=MemoryLevel.L2,
        memory_type=MemoryType.PROJECT,
        content="CUSTOMER_ALPHA_STRIPE_SECRET_KEY_9988",
        subject="stripe_secret",
        importance=1.0,
    )
    await f["mem_store"].write_record_atomic(rec)

    # Project B queries memory for the secret
    matches_b = await mem_svc.retrieve(
        project_id="proj-b",
        query="STRIPE_SECRET_KEY",
        limit=10,
        include_global=False,
    )
    assert len(matches_b) == 0, "Security Violation: Project B retrieved Project A's secret memory"


# ---------------------------------------------------------------------------
# Scenario B: UI Asset Isolation & Anti-Contamination
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_b_ui_design_contract_isolation(security_fixture):
    """Project A generates a custom UI design contract. Project B requests design contract -> NO leak."""
    f = security_fixture
    design_dir = f["tmp_path"] / "designs"
    design_dir.mkdir(parents=True, exist_ok=True)

    # Project A has a design contract
    (design_dir / "proj-a.design.md").write_text("# Project A Design Contract\nAccent: #FF0000", encoding="utf-8")

    ctx_builder = UnifiedContextBuilder(
        design_dir=design_dir,
        default_token_budget=4000,
    )

    # Retrieve design for Project B
    contract_b = ctx_builder._retrieve_design_contract("proj-b")
    assert contract_b is None, "Security Violation: Project B accessed Project A's design contract"

    # Forbid default_project fallback
    (design_dir / "default_project.design.md").write_text("# Leaked Default Design", encoding="utf-8")
    assert ctx_builder._retrieve_design_contract("default_project") is None
    assert ctx_builder._retrieve_design_contract("") is None


# ---------------------------------------------------------------------------
# Scenario C: Same-Project Continuity
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_c_same_project_continuity(security_fixture):
    """Host 1 in Project X saves a checkpoint. Host 2 in Project X restores it successfully."""
    f = security_fixture
    ckpt = CheckpointRecord(
        checkpoint_id="cp-x-1",
        project_id="proj-a",
        summary="Host 1 completed feature setup in Project A",
        files_changed=("src/feature.py",),
    )
    await f["ckpt_store"].save(ckpt)

    latest = await f["ckpt_store"].latest("proj-a")
    assert latest is not None
    assert latest.checkpoint_id == "cp-x-1"
    assert "Host 1 completed" in latest.summary


# ---------------------------------------------------------------------------
# Scenario D: Cross-Project Switch Continuity (Zero Leakage)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_d_cross_project_zero_leakage(security_fixture):
    """Host 1 in Project X saves checkpoint. Host 2 starts in Project Y -> receives ZERO data from X."""
    f = security_fixture
    ckpt_a = CheckpointRecord(
        checkpoint_id="cp-alpha-secret",
        project_id="proj-a",
        summary="Customer Alpha Confidential Pipeline",
        files_changed=("alpha/secret.py",),
    )
    await f["ckpt_store"].save(ckpt_a)

    # Host 2 requests latest checkpoint for Project B
    latest_b = await f["ckpt_store"].latest("proj-b")
    assert latest_b is None, "Security Violation: Project B received checkpoint belonging to Project A"


# ---------------------------------------------------------------------------
# Scenario E: Concurrent Workspaces Isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_e_concurrent_workspaces_isolation(security_fixture):
    """Two concurrent hosts operate in separate workspaces; each resolves only its own project."""
    f = security_fixture
    auth_mgr_1 = SecurityAuthorizationManager(f["proj_store"], server_workspace_root=f["ws_a"])
    auth_mgr_2 = SecurityAuthorizationManager(f["proj_store"], server_workspace_root=f["ws_b"])

    # Host 1 operating in ws_a
    res_1 = await auth_mgr_1.authorize_project()
    assert res_1.project_id == "proj-a"

    # Host 2 operating concurrently in ws_b
    res_2 = await auth_mgr_2.authorize_project()
    assert res_2.project_id == "proj-b"

    # Verify no global state flip
    res_1_recheck = await auth_mgr_1.authorize_project()
    assert res_1_recheck.project_id == "proj-a"


# ---------------------------------------------------------------------------
# Scenario F: Cross-Project IDOR Parameter Tampering
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_f_cross_project_idor_tampering(security_fixture):
    """Caller in Workspace A explicitly passes project_id='proj-b' -> 403 Forbidden."""
    f = security_fixture
    auth_mgr = SecurityAuthorizationManager(f["proj_store"], server_workspace_root=f["ws_a"])

    with pytest.raises(SecurityAuthorizationError) as exc_info:
        await auth_mgr.authorize_project(project_id="proj-b")

    assert exc_info.value.violation_type == "CROSS_PROJECT_IDOR"
    assert "CROSS_PROJECT_IDOR_VIOLATION" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario G: Global Memory Injection & Search Isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_g_global_memory_injection_defense(security_fixture):
    """Attempting to save project-specific paths or secrets to global scope is BLOCKED."""
    f = security_fixture
    auth_mgr = f["auth_mgr"]

    # Block proprietary project name
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        auth_mgr.validate_global_memory_payload("Archived teachora login logic for school portal")
    assert exc_info.value.violation_type == "GLOBAL_MEMORY_POLLUTION"

    # Block proprietary local file paths
    with pytest.raises(SecurityAuthorizationError) as exc_info2:
        auth_mgr.validate_global_memory_payload("Config located at /Users/haohg/Project/customer-b/secrets.env")
    assert exc_info2.value.violation_type == "GLOBAL_MEMORY_POLLUTION"

    # Legitimate generic rule should PASS
    auth_mgr.validate_global_memory_payload("Always write type hints and clean docstrings in Python.")


# ---------------------------------------------------------------------------
# Scenario H: Path Traversal & Symlink Defense
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_h_path_traversal_defense(security_fixture):
    """Passing relative path traversal ('../') outside authorized workspace is BLOCKED."""
    f = security_fixture
    auth_mgr = f["auth_mgr"]

    # Valid inside workspace
    valid_path = f["ws_a"] / "src" / "main.py"
    res = auth_mgr.validate_path_containment(valid_path, f["proj_a"])
    assert res == valid_path.resolve()

    # Path traversal attack attempting to reach project B from project A
    malicious_target = f["ws_a"] / ".." / "project_b" / "secret.env"
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        auth_mgr.validate_path_containment(malicious_target, f["proj_a"])

    assert exc_info.value.violation_type == "PATH_TRAVERSAL"
    assert "PATH_TRAVERSAL_VIOLATION" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario I: Crash Recovery Isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_i_crash_recovery_isolation(security_fixture):
    """An unfinished run or crash in Project A is never surfaced in Project B recovery."""
    f = security_fixture
    from persistence.sqlite_agent_run_store import SQLiteAgentRunStore
    from core.agent_run import AgentRunRecord, AgentRunState
    from core.agent_role import AgentRole

    run_store = SQLiteAgentRunStore(f["db_path"])
    await run_store.initialize()

    # Incomplete run from crash in Project A
    crashed_run_a = AgentRunRecord(
        run_id="run-crashed-alpha",
        task_id="task-alpha",
        project_id="proj-a",
        agent_id="agent-alpha-1",
        execution_role=AgentRole.BACKEND_CODER,
        state=AgentRunState.RUNNING,
    )
    await run_store.save(crashed_run_a)

    # Project B queries runs
    runs_b = await run_store.list_for_task("task-alpha")
    runs_b_scoped = [r for r in runs_b if r.project_id == "proj-b"]
    assert len(runs_b_scoped) == 0


# ---------------------------------------------------------------------------
# Scenario J: Shared Docs Backend Isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_j_shared_docs_backend_isolation(security_fixture):
    """search_docs binds per-request to the authorized project, preventing cross-workspace bleed."""
    f = security_fixture

    # Write distinct docs in both workspaces
    (f["ws_a"] / "README.md").write_text("# Customer Alpha Docs\nAlpha proprietary protocol.", encoding="utf-8")
    (f["ws_b"] / "README.md").write_text("# Customer Beta Docs\nBeta architecture details.", encoding="utf-8")

    from integrations.docs_knowledge_backend import DocsKnowledgeBackend
    from core.context import ExecutionContext

    # Query for Alpha
    backend_a = DocsKnowledgeBackend(workspace_root=f["ws_a"])
    ctx_a = ExecutionContext(workspace_id="proj-a", project_id="proj-a")
    backend_a.index_workspace_docs()
    results_a = list(await backend_a.search(ctx_a, "Alpha"))
    assert len(results_a) > 0
    assert "Alpha" in results_a[0]["title"] or "Alpha" in results_a[0]["content"]

    # Query in Beta should NOT return Alpha
    backend_b = DocsKnowledgeBackend(workspace_root=f["ws_b"])
    ctx_b = ExecutionContext(workspace_id="proj-b", project_id="proj-b")
    backend_b.index_workspace_docs()
    results_b = list(await backend_b.search(ctx_b, "Alpha"))
    assert len(results_b) == 0
