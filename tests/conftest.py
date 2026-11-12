"""Root Conftest for MyAgent Test Suite.

Provides:
1. Strict per-test isolation for SecurityAuthorizationManager.
2. Automatic workspace and project store binding when tests use tmp_path.
3. Teardown cleanup ensuring zero global state or monkeypatch leakage between tests.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from core.security_authorization import SecurityAuthorizationManager
from my_agent_mcp import server


@pytest.fixture(autouse=True)
def isolate_mcp_security_per_test(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[SecurityAuthorizationManager]:
    """Isolates SecurityAuthorizationManager per test.

    - If the test requests 'tmp_path', binds server._auth_manager and MYAGENT_WORKSPACE to tmp_path.
    - Otherwise, binds server._auth_manager to repository ROOT (/Users/haohg/Project/my-agent).
    - Always syncs with server.projects.
    - Explicitly sets MYAGENT_RUNTIME_ENV to 'development' to shield production database.
    - Restores clean state on teardown to eliminate cross-test pollution.
    """
    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "development")
    repo_root = server.ROOT

    # Determine test workspace
    if "tmp_path" in request.fixturenames:
        test_workspace = request.getfixturevalue("tmp_path")
        monkeypatch.setenv("MYAGENT_WORKSPACE", str(test_workspace))
        auth_manager = SecurityAuthorizationManager(
            project_store=server.projects,
            server_workspace_root=test_workspace,
        )
    else:
        auth_manager = SecurityAuthorizationManager(
            project_store=server.projects,
            server_workspace_root=repo_root,
        )

    # Monkeypatch server._auth_manager for the duration of this test
    monkeypatch.setattr(server, "_auth_manager", auth_manager)

    yield auth_manager

    # Teardown: ensure clean global state
    auth_manager.set_project_store(server.projects)
    auth_manager.set_server_workspace(repo_root)
