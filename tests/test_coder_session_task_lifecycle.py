from __future__ import annotations

import asyncio

import pytest

from my_agent_mcp import server


def reset_task_state(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_coder_task_failures",
        {},
        raising=False,
    )


@pytest.mark.asyncio
async def test_completed_coder_task_is_removed(
    monkeypatch,
) -> None:
    reset_task_state(monkeypatch)

    async def run() -> None:
        return None

    task = asyncio.create_task(run())

    server._track_coder_session_task(
        "session-1",
        task,
    )

    await task
    await asyncio.sleep(0)

    assert "session-1" not in server._coder_tasks
    assert "session-1" not in server._coder_task_failures


@pytest.mark.asyncio
async def test_failed_coder_task_is_removed_and_failure_recorded(
    monkeypatch,
) -> None:
    reset_task_state(monkeypatch)

    async def run() -> None:
        raise RuntimeError(
            "event consumer failed"
        )

    task = asyncio.create_task(run())

    server._track_coder_session_task(
        "session-1",
        task,
    )

    # Production callback must retrieve the exception.
    for _ in range(100):
        if task.done():
            await asyncio.sleep(0)
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail(
            "coder session task never finished"
        )

    assert "session-1" not in server._coder_tasks

    assert server._coder_task_failures["session-1"] == {
        "error_type": "RuntimeError",
        "message": "event consumer failed",
    }


@pytest.mark.asyncio
async def test_cancelled_coder_task_is_removed_without_failure(
    monkeypatch,
) -> None:
    reset_task_state(monkeypatch)

    started = asyncio.Event()

    async def run() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run())

    server._track_coder_session_task(
        "session-1",
        task,
    )

    await asyncio.wait_for(
        started.wait(),
        timeout=1,
    )

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0)

    assert "session-1" not in server._coder_tasks
    assert "session-1" not in server._coder_task_failures


@pytest.mark.asyncio
async def test_old_coder_task_callback_cannot_remove_new_task(
    monkeypatch,
) -> None:
    reset_task_state(monkeypatch)

    first_release = asyncio.Event()
    second_release = asyncio.Event()

    async def first_run() -> None:
        await first_release.wait()

    async def second_run() -> None:
        await second_release.wait()

    first = asyncio.create_task(
        first_run()
    )

    server._track_coder_session_task(
        "session-1",
        first,
    )

    second = asyncio.create_task(
        second_run()
    )

    server._track_coder_session_task(
        "session-1",
        second,
    )

    assert server._coder_tasks["session-1"] is second

    first_release.set()
    await first
    await asyncio.sleep(0)

    # Old callback must not remove the new owner.
    assert server._coder_tasks["session-1"] is second

    second_release.set()
    await second
    await asyncio.sleep(0)

    assert "session-1" not in server._coder_tasks
