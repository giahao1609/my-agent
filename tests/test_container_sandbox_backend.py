from __future__ import annotations

import pytest

from core.context import ExecutionContext
from integrations.container_sandbox_backend import ContainerSandboxBackend


@pytest.mark.asyncio
async def test_container_sandbox_lifecycle(tmp_path) -> None:
    backend = ContainerSandboxBackend(workspace_root=tmp_path)
    context = ExecutionContext(project_id="proj-1", workspace_id="ws-1", session_id="sess-1")

    # Create
    sandbox_id = await backend.create(context, {"image": "python:3.11-slim", "memory_mb": 1024})
    assert sandbox_id.startswith("sbx-")

    # Status
    st = await backend.status(sandbox_id)
    assert st["state"] == "running"
    assert st["network_egress"] == "default-deny"
    assert st["memory_mb"] == 1024

    # Exec
    res = await backend.exec(sandbox_id, ["python", "--version"])
    assert res["exit_code"] == 0
    assert res["network_egress"] == "default-deny"

    # Pause & Resume
    await backend.pause(sandbox_id)
    st_pause = await backend.status(sandbox_id)
    assert st_pause["state"] == "paused"

    with pytest.raises(RuntimeError, match="cannot execute command"):
        await backend.exec(sandbox_id, ["ls"])

    await backend.resume(sandbox_id)
    st_resume = await backend.status(sandbox_id)
    assert st_resume["state"] == "running"

    # Snapshot & Rollback
    snap_id = await backend.snapshot(sandbox_id)
    assert snap_id.startswith("snap-")

    await backend.pause(sandbox_id)
    await backend.rollback(sandbox_id, snap_id)
    st_roll = await backend.status(sandbox_id)
    assert st_roll["state"] == "running"

    # Terminate
    await backend.terminate(sandbox_id)
    with pytest.raises(KeyError):
        await backend.status(sandbox_id)


@pytest.mark.asyncio
async def test_container_sandbox_capabilities(tmp_path) -> None:
    backend = ContainerSandboxBackend(workspace_root=tmp_path)
    caps = await backend.capabilities()
    assert len(caps) >= 3
    assert any(c.name == "default_deny_egress" for c in caps)
