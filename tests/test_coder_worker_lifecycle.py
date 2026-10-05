from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.config import CoderRuntimeMode
from my_agent_mcp import server


def patch_in_process_server(
    monkeypatch,
    worker,
) -> None:
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
        {},
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_failures",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_lock",
        asyncio.Lock(),
        raising=False,
    )

    async def fake_get_model_composition():
        return SimpleNamespace(
            coder_worker=worker,
        )

    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )


@pytest.mark.asyncio
async def test_completed_worker_is_removed_from_registry(
    monkeypatch,
) -> None:
    class Worker:
        async def run_session(
            self,
            session_id: str,
        ) -> None:
            return None

    patch_in_process_server(
        monkeypatch,
        Worker(),
    )

    task = await server._ensure_in_process_coder_worker(
        "session-1"
    )

    assert task is not None

    await task
    await asyncio.sleep(0)

    assert "session-1" not in server._coder_worker_tasks
    assert "session-1" not in server._coder_worker_failures


@pytest.mark.asyncio
async def test_failed_worker_is_removed_and_failure_is_recorded(
    monkeypatch,
) -> None:
    class Worker:
        async def run_session(
            self,
            session_id: str,
        ) -> None:
            raise RuntimeError("model transport failed")

    patch_in_process_server(
        monkeypatch,
        Worker(),
    )

    task = await server._ensure_in_process_coder_worker(
        "session-1"
    )

    assert task is not None

    # Do not await the failed task here. The production done callback
    # must retrieve the exception itself.
    for _ in range(100):
        if task.done():
            await asyncio.sleep(0)
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("worker task never finished")

    assert "session-1" not in server._coder_worker_tasks

    assert server._coder_worker_failures["session-1"] == {
        "error_type": "RuntimeError",
        "message": "model transport failed",
    }


@pytest.mark.asyncio
async def test_cancelled_worker_is_removed_without_failure(
    monkeypatch,
) -> None:
    started = asyncio.Event()

    class Worker:
        async def run_session(
            self,
            session_id: str,
        ) -> None:
            started.set()
            await asyncio.Event().wait()

    patch_in_process_server(
        monkeypatch,
        Worker(),
    )

    task = await server._ensure_in_process_coder_worker(
        "session-1"
    )

    assert task is not None

    await asyncio.wait_for(
        started.wait(),
        timeout=1,
    )

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0)

    assert "session-1" not in server._coder_worker_tasks
    assert "session-1" not in server._coder_worker_failures
