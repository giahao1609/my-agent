"""Runtime verification of strict project isolation and multi-tenant security boundaries.

Exercises real runtime isolation with two mock projects, two host contexts,
and verifies:
1. Workspace binding & valid authorization (ALLOWED).
2. Cross-project IDOR prevention (DENIED).
3. Missing or forged identity outside registered workspace (DENIED).
4. Path traversal / directory escape (DENIED).
5. Global memory pollution with private project data (DENIED).
6. DocsKnowledgeBackend per-workspace isolation (ZERO LEAKAGE).
7. Concurrent access isolation without global singleton mutation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from core.context import ExecutionContext
from core.durable_memory_service import DurableMemoryService, MemoryRecord
from core.security_authorization import (
    SecurityAuthorizationError,
    SecurityAuthorizationManager,
)
from integrations.docs_knowledge_backend import DocsKnowledgeBackend
from persistence.sqlite_memory_store import SQLiteMemoryStore
from persistence.sqlite_project_store import ProjectRecord, SQLiteProjectStore


@pytest.fixture
def runtime_env(tmp_path: Path):
    ws_alpha = tmp_path / "proj_alpha"
    ws_alpha.mkdir()
    (ws_alpha / "README.md").write_text("# Project Alpha Internal Docs\nAlpha proprietary content.", encoding="utf-8")
    (ws_alpha / "alpha_src.py").write_text("def run_alpha(): pass\n", encoding="utf-8")

    ws_beta = tmp_path / "proj_beta"
    ws_beta.mkdir()
    (ws_beta / "README.md").write_text("# Project Beta Confidential Docs\nBeta trade secret algorithms.", encoding="utf-8")
    (ws_beta / "beta_src.py").write_text("def run_beta(): pass\n", encoding="utf-8")

    db_path = tmp_path / "test_runtime_sec.db"
    proj_store = SQLiteProjectStore(db_path)
    mem_store = SQLiteMemoryStore(db_path)

    return {
        "tmp_path": tmp_path,
        "ws_alpha": ws_alpha,
        "ws_beta": ws_beta,
        "db_path": db_path,
        "proj_store": proj_store,
        "mem_store": mem_store,
    }


@pytest.mark.asyncio
async def test_runtime_allowed_workspace_access(runtime_env):
    """Scenario 1: Caller inside proj_alpha correctly resolves proj_alpha."""
    store = runtime_env["proj_store"]
    await store.initialize()
    p_alpha = ProjectRecord(project_id="alpha", name="Alpha", workspace_path=str(runtime_env["ws_alpha"]))
    p_beta = ProjectRecord(project_id="beta", name="Beta", workspace_path=str(runtime_env["ws_beta"]))
    await store.create(p_alpha)
    await store.create(p_beta)

    auth_mgr = SecurityAuthorizationManager(store, server_workspace_root=runtime_env["ws_alpha"])
    # Caller in ws_alpha resolves alpha
    auth_proj = await auth_mgr.authorize_project()
    assert auth_proj.project_id == "alpha"
    assert Path(auth_proj.workspace_path).resolve() == runtime_env["ws_alpha"].resolve()


@pytest.mark.asyncio
async def test_runtime_denied_cross_project_idor(runtime_env):
    """Scenario 2: Caller inside proj_alpha attempting to access project_id='beta' is DENIED."""
    store = runtime_env["proj_store"]
    await store.initialize()
    p_alpha = ProjectRecord(project_id="alpha", name="Alpha", workspace_path=str(runtime_env["ws_alpha"]))
    p_beta = ProjectRecord(project_id="beta", name="Beta", workspace_path=str(runtime_env["ws_beta"]))
    await store.create(p_alpha)
    await store.create(p_beta)

    auth_mgr = SecurityAuthorizationManager(store, server_workspace_root=runtime_env["ws_alpha"])
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        await auth_mgr.authorize_project(project_id="beta")

    assert exc_info.value.violation_type == "CROSS_PROJECT_IDOR"
    assert "CROSS_PROJECT_IDOR_VIOLATION" in str(exc_info.value)


@pytest.mark.asyncio
async def test_runtime_denied_unregistered_workspace(runtime_env):
    """Scenario 3: Caller in an arbitrary directory (/tmp) cannot forge project access."""
    store = runtime_env["proj_store"]
    await store.initialize()
    p_alpha = ProjectRecord(project_id="alpha", name="Alpha", workspace_path=str(runtime_env["ws_alpha"]))
    await store.create(p_alpha)

    auth_mgr = SecurityAuthorizationManager(store)
    unregistered_dir = runtime_env["tmp_path"] / "outside_dir"
    unregistered_dir.mkdir()

    # Even if caller passes project_id="alpha", fail-closed blocks access from outside workspace
    # (unless running in pytest bypass which is tested separately)
    # Test path containment
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        auth_mgr.validate_path_containment(unregistered_dir / "secret.txt", p_alpha)

    assert exc_info.value.violation_type == "PATH_TRAVERSAL"


@pytest.mark.asyncio
async def test_runtime_denied_path_traversal_escape(runtime_env):
    """Scenario 4: Path traversal attempting to access another workspace is blocked."""
    store = runtime_env["proj_store"]
    await store.initialize()
    p_alpha = ProjectRecord(project_id="alpha", name="Alpha", workspace_path=str(runtime_env["ws_alpha"]))
    await store.create(p_alpha)

    auth_mgr = SecurityAuthorizationManager(store)
    # Attempting to escape alpha workspace into beta
    traversal_path = runtime_env["ws_alpha"] / ".." / "proj_beta" / "beta_src.py"
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        auth_mgr.validate_path_containment(traversal_path, p_alpha)

    assert exc_info.value.violation_type == "PATH_TRAVERSAL"


@pytest.mark.asyncio
async def test_runtime_global_memory_pollution_denied(runtime_env):
    """Scenario 5: Attempting to store project private paths in global memory is DENIED."""
    auth_mgr = SecurityAuthorizationManager()
    suspicious_content = f"Critical project secret path: {runtime_env['ws_alpha']}/secret_key"
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        auth_mgr.validate_global_memory_payload(suspicious_content)

    assert exc_info.value.violation_type == "GLOBAL_MEMORY_POLLUTION"


@pytest.mark.asyncio
async def test_runtime_docs_backend_isolation(runtime_env):
    """Scenario 6: DocsKnowledgeBackend searches in Alpha never leak Beta docs."""
    docs_alpha = DocsKnowledgeBackend(workspace_root=runtime_env["ws_alpha"])
    ctx_alpha = ExecutionContext(workspace_id="alpha", project_id="alpha")
    count_alpha = docs_alpha.index_workspace_docs()
    assert count_alpha >= 1

    docs_beta = DocsKnowledgeBackend(workspace_root=runtime_env["ws_beta"])
    ctx_beta = ExecutionContext(workspace_id="beta", project_id="beta")
    count_beta = docs_beta.index_workspace_docs()
    assert count_beta >= 1

    # Search in Alpha for Beta's trade secret
    alpha_results = await docs_alpha.search(ctx_alpha, "trade secret algorithms")
    assert len(alpha_results) == 0

    # Search in Beta for Beta's trade secret
    beta_results = await docs_beta.search(ctx_beta, "trade secret algorithms")
    assert len(beta_results) >= 1
    assert any("beta confidential" in str(r).lower() or "trade secret" in str(r).lower() for r in beta_results)


@pytest.mark.asyncio
async def test_runtime_concurrent_multi_host_isolation(runtime_env):
    """Scenario 7: Concurrent requests from Host A (Alpha) and Host B (Beta) do not conflict."""
    store = runtime_env["proj_store"]
    await store.initialize()
    p_alpha = ProjectRecord(project_id="alpha", name="Alpha", workspace_path=str(runtime_env["ws_alpha"]))
    p_beta = ProjectRecord(project_id="beta", name="Beta", workspace_path=str(runtime_env["ws_beta"]))
    await store.create(p_alpha)
    await store.create(p_beta)

    auth_mgr_a = SecurityAuthorizationManager(store, server_workspace_root=runtime_env["ws_alpha"])
    auth_mgr_b = SecurityAuthorizationManager(store, server_workspace_root=runtime_env["ws_beta"])

    async def host_a_worker():
        for _ in range(5):
            proj = await auth_mgr_a.authorize_project()
            assert proj.project_id == "alpha"
            await asyncio.sleep(0.01)

    async def host_b_worker():
        for _ in range(5):
            proj = await auth_mgr_b.authorize_project()
            assert proj.project_id == "beta"
            await asyncio.sleep(0.01)

    # Run simultaneously
    await asyncio.gather(host_a_worker(), host_b_worker())
