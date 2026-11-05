from __future__ import annotations

from pathlib import Path

import pytest

from core.context import ExecutionContext
from integrations.container_sandbox_backend import (
    ContainerSandboxBackend,
    is_docker_available,
)


@pytest.mark.asyncio
async def test_container_sandbox_simulation_lifecycle(tmp_path: Path) -> None:
    backend = ContainerSandboxBackend(workspace_root=tmp_path, force_simulation=True)
    context = ExecutionContext(project_id="proj-sim", workspace_id="ws-sim", session_id="sess-sim")

    # Create
    sandbox_id = await backend.create(context, {"image": "python:3.11-slim", "memory_mb": 1024})
    assert sandbox_id.startswith("sbx-")

    # Status
    st = await backend.status(sandbox_id)
    assert st["state"] == "running"
    assert st["network_egress"] == "default-deny"
    assert st["memory_mb"] == 1024
    assert st["is_real_container"] is False

    # Exec
    res = await backend.exec(sandbox_id, ["python", "--version"])
    assert res["exit_code"] == 0
    assert res["network_egress"] == "default-deny"
    assert "Simulated exec output" in str(res["stdout"])

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
async def test_container_sandbox_real_docker_lifecycle(tmp_path: Path) -> None:
    if not is_docker_available():
        pytest.skip("Docker/Podman daemon not reachable on host")

    backend = ContainerSandboxBackend(workspace_root=tmp_path, default_image="python:3.10-slim")
    context = ExecutionContext(project_id="proj-docker-live", workspace_id=str(tmp_path), session_id="sess-live")

    # 1. Create real container with default-deny network and memory limits
    sandbox_id = await backend.create(
        context,
        {
            "image": "python:3.10-slim",
            "memory_mb": 512,
            "cpu_limit": 1.0,
            "network_egress": "default-deny",
        },
    )
    assert sandbox_id.startswith("sbx-")

    try:
        # 2. Check live status
        st = await backend.status(sandbox_id)
        assert st["state"] == "running"
        assert st["is_real_container"] is True
        assert st["network_egress"] == "default-deny"

        # 3. Real Exec inside container
        res = await backend.exec(
            sandbox_id,
            ["python", "-c", "import sys; print(f'Engine: Python {sys.version_info.major}.{sys.version_info.minor}')"],
        )
        assert res["exit_code"] == 0
        assert "Engine: Python 3.10" in str(res["stdout"])

        # 4. Real Pause & Unpause
        await backend.pause(sandbox_id)
        st_pause = await backend.status(sandbox_id)
        assert st_pause["state"] == "paused"

        with pytest.raises(RuntimeError, match="cannot execute command"):
            await backend.exec(sandbox_id, ["ls"])

        await backend.resume(sandbox_id)
        st_resume = await backend.status(sandbox_id)
        assert st_resume["state"] == "running"

        # 5. Snapshot & Rollback
        # Write state before snapshot
        await backend.exec(sandbox_id, ["touch", "/tmp/pre_snapshot.txt"])
        snap_id = await backend.snapshot(sandbox_id)
        assert snap_id.startswith("snap-")

        # Write state after snapshot
        await backend.exec(sandbox_id, ["touch", "/tmp/post_snapshot.txt"])

        # Rollback to pre-snapshot state
        await backend.rollback(sandbox_id, snap_id)
        st_roll = await backend.status(sandbox_id)
        assert st_roll["state"] == "running"

        # Verify pre_snapshot exists, post_snapshot does not
        chk_pre = await backend.exec(sandbox_id, ["ls", "/tmp/pre_snapshot.txt"])
        assert chk_pre["exit_code"] == 0

        chk_post = await backend.exec(sandbox_id, ["ls", "/tmp/post_snapshot.txt"])
        assert chk_post["exit_code"] != 0

    finally:
        # 6. Terminate real container and remove artifacts
        await backend.terminate(sandbox_id)

    with pytest.raises(KeyError):
        await backend.status(sandbox_id)


@pytest.mark.asyncio
async def test_container_sandbox_capabilities_dynamic_switching(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backend = ContainerSandboxBackend(workspace_root=tmp_path)

    # Case 1: Docker reported available
    monkeypatch.setattr("integrations.container_sandbox_backend.is_docker_available", lambda: True)
    caps_available = await backend.capabilities()
    assert len(caps_available) >= 3
    assert all(c.state.value == "available" for c in caps_available)
    assert any("Docker" in c.evidence or "namespace" in c.evidence for c in caps_available)

    # Case 2: Docker reported absent
    monkeypatch.setattr("integrations.container_sandbox_backend.is_docker_available", lambda: False)
    caps_mocked = await backend.capabilities()
    assert len(caps_mocked) >= 3
    assert all(c.state.value == "mocked" for c in caps_mocked)
    assert any("simulated" in c.evidence.lower() or "without container daemon" in c.evidence.lower() for c in caps_mocked)
