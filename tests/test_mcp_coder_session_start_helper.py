from __future__ import annotations

import pytest

from agents.coder_stack import build_coder_agent_stack
from core.project import ProjectRecord
from persistence.sqlite_conversation_store import (
    SQLiteConversationStore,
)

from my_agent_mcp import server


@pytest.mark.asyncio
async def test_shared_coder_session_starter_uses_explicit_task_id(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "agent.db"

    stack = await build_coder_agent_stack(
        database_path
    )

    conversation_store = SQLiteConversationStore(
        database_path
    )
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
        active_task_id="task-project-active",
    )

    async def fake_get_coder_stack():
        return stack

    async def fake_ensure_worker(
        session_id: str,
    ):
        return None

    async def fake_run_coder_session(
        stack_arg,
        session_id: str,
        message,
        context,
    ) -> None:
        return None

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_ensure_in_process_coder_worker",
        fake_ensure_worker,
    )
    monkeypatch.setattr(
        server,
        "_run_coder_session",
        fake_run_coder_session,
    )
    monkeypatch.setattr(
        server,
        "conversations",
        conversation_store,
    )
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
    )
    monkeypatch.setattr(
        server,
        "_coder_task_failures",
        {},
    )

    session_id = await server._start_coder_session_for_project(
        project=project,
        message="Execute exactly this plan step.",
        task_id="task-step-owner",
    )

    durable = await stack.session_store.get(
        session_id
    )

    assert durable is not None
    assert durable.context.project_id == "demo"

    # Explicit task ownership must win over the project's
    # currently selected active task.
    assert durable.context.task_id == "task-step-owner"

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    assert len(history) == 1
    assert history[0].role.value == "user"
    assert history[0].content == (
        "Execute exactly this plan step."
    )


@pytest.mark.asyncio
async def test_mcp_start_coder_session_delegates_to_shared_starter(
    tmp_path,
    monkeypatch,
) -> None:
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
        active_task_id="task-active",
    )

    calls = []

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        assert project_id == "demo"
        return project

    async def fake_shared_start(
        *,
        project,
        message: str,
        task_id: str | None,
    ) -> str:
        calls.append(
            {
                "project_id": project.project_id,
                "message": message,
                "task_id": task_id,
            }
        )
        return "session-shared"

    async def reject_legacy_path():
        raise AssertionError(
            "start_coder_session bypassed shared starter"
        )

    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )
    monkeypatch.setattr(
        server,
        "_start_coder_session_for_project",
        fake_shared_start,
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        reject_legacy_path,
    )

    result = await server.start_coder_session(
        "Inspect the active task.",
        project_id="demo",
    )

    assert calls == [
        {
            "project_id": "demo",
            "message": "Inspect the active task.",
            "task_id": "task-active",
        }
    ]

    assert result == {
        "status": "started",
        "session_id": "session-shared",
        "project_id": "demo",
        "workspace_path": str(tmp_path),
    }


@pytest.mark.asyncio
async def test_shared_coder_session_starter_cancels_session_when_setup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    from core.session import SessionState

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
        active_task_id="task-active",
    )

    class FailingConversationStore:
        async def create(self, conversation) -> None:
            raise RuntimeError("conversation setup failed")

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "conversations",
        FailingConversationStore(),
    )

    with pytest.raises(
        RuntimeError,
        match="conversation setup failed",
    ):
        await server._start_coder_session_for_project(
            project=project,
            message="Execute this step.",
            task_id="task-1",
        )

    sessions = await stack.session_store.list_for_project(
        "demo"
    )

    assert len(sessions) == 1

    durable = sessions[0]
    assert durable.context.task_id == "task-1"

    # A partially-created coder session must never remain resumable.
    assert durable.state is SessionState.STOPPED
