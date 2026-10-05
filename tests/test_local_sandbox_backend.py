from __future__ import annotations

import pytest

from core.context import ExecutionContext
from core.errors import CapabilityUnavailableError
from integrations.local_sandbox_backend import LocalSandboxBackend


@pytest.mark.asyncio
async def test_local_sandbox_is_disabled_by_default(tmp_path) -> None:
    backend = LocalSandboxBackend()
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        project_id="project-1",
    )

    with pytest.raises(CapabilityUnavailableError):
        await backend.create(context, {})


@pytest.mark.asyncio
async def test_local_sandbox_can_create_exec_status_and_terminate_when_enabled(
    tmp_path,
) -> None:
    backend = LocalSandboxBackend(enabled=True)
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        project_id="project-1",
    )

    sandbox_id = await backend.create(context, {})

    status = await backend.status(sandbox_id)
    assert status["status"] == "running"
    assert status["backend"] == "local"
    assert status["workspace_path"] == str(tmp_path.resolve())

    result = await backend.exec(
        sandbox_id,
        ["/usr/bin/printf", "hello"],
        timeout=2,
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"
    assert result["stderr"] == ""

    await backend.terminate(sandbox_id)

    status = await backend.status(sandbox_id)
    assert status["status"] == "terminated"
