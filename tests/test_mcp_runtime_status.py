from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import CoderRuntimeMode
from my_agent_mcp import server


class FakeTask:
    def __init__(self, *, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


@pytest.mark.asyncio
async def test_status_exposes_coder_runtime_and_worker_diagnostics(
    monkeypatch,
) -> None:
    async def fake_initialize() -> None:
        return None

    class FakeProjects:
        async def get_active(self):
            return None

    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "projects",
        FakeProjects(),
    )
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {
            "session-running": FakeTask(done=False),
            "session-finished": FakeTask(done=True),
        },
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_failures",
        {
            "session-failed": {
                "error_type": "RuntimeError",
                "message": "model transport failed",
            },
        },
    )
    monkeypatch.setattr(
        server,
        "_coder_task_failures",
        {
            "session-agent-failed": {
                "error_type": "ValueError",
                "message": "event consumer failed",
            },
        },
    )

    result = await server.my_agent_status()

    assert result["coder_runtime_mode"] == "in_process"

    assert result["active_coder_workers"] == [
        "session-running",
    ]

    assert result["coder_worker_failures"] == {
        "session-failed": {
            "error_type": "RuntimeError",
            "message": "model transport failed",
        },
    }

    assert result["coder_task_failures"] == {
        "session-agent-failed": {
            "error_type": "ValueError",
            "message": "event consumer failed",
        },
    }
