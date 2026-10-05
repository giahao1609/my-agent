from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agents.coder_stack import build_coder_agent_stack
from core.checkpoint import CheckpointRecord
from core.context import ExecutionContext
from core.project import ProjectRecord
from core.session import SessionRecord, SessionState
from my_agent_mcp import server
from persistence.sqlite_checkpoint_store import SQLiteCheckpointStore
from persistence.sqlite_conversation_store import SQLiteConversationStore
from persistence.sqlite_project_store import SQLiteProjectStore


@pytest.mark.asyncio
async def test_start_coder_session_inherits_active_task_id(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "agent.db"

    stack = await build_coder_agent_stack(database_path)
    conversations = SQLiteConversationStore(database_path)
    await conversations.initialize()

    project = ProjectRecord(
        project_id="project-1",
        name="Project",
        workspace_path=str(tmp_path),
        active_task_id="task-1",
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversations)

    started = await server.start_coder_session(
        "continue task 1",
        project_id="project-1",
    )
    session_id = str(started["session_id"])

    durable = await stack.session_store.get(session_id)

    assert durable is not None
    assert durable.context.task_id == "task-1"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_save_checkpoint_inherits_active_task_id(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    checkpoints = SQLiteCheckpointStore(database_path)

    await projects.initialize()
    await checkpoints.initialize()

    project = ProjectRecord(
        project_id="project-1",
        name="Project",
        workspace_path=str(tmp_path),
        active_task_id="task-1",
    )
    await projects.create(project)

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "projects", projects)
    monkeypatch.setattr(server, "checkpoints", checkpoints)

    result = await server.save_checkpoint(
        summary="Task 1 checkpoint",
        project_id="project-1",
    )

    checkpoint_id = str(
        result["checkpoint"]["checkpoint_id"]
    )
    restored = await checkpoints.latest(
        "project-1",
        task_id="task-1",
    )

    assert restored is not None
    assert restored.checkpoint_id == checkpoint_id
    assert restored.task_id == "task-1"


@pytest.mark.asyncio
async def test_latest_resumable_coder_session_prefers_active_task(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "agent.db"

    stack = await build_coder_agent_stack(database_path)

    project = ProjectRecord(
        project_id="project-1",
        name="Project",
        workspace_path=str(tmp_path),
        active_task_id="task-1",
    )

    await stack.session_store.save(
        SessionRecord(
            session_id="task-1-session",
            context=ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="task-1-session",
                task_id="task-1",
                project_id="project-1",
            ),
            state=SessionState.RUNNING,
        )
    )

    await asyncio.sleep(0.01)

    # Newer session belongs to another task and must not win.
    await stack.session_store.save(
        SessionRecord(
            session_id="task-2-session",
            context=ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="task-2-session",
                task_id="task-2",
                project_id="project-1",
            ),
            state=SessionState.RUNNING,
        )
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    result = await server.get_latest_resumable_coder_session(
        project_id="project-1",
    )

    assert result["status"] == "ok"
    assert result["session_id"] == "task-1-session"


@pytest.mark.asyncio
async def test_resume_project_prefers_checkpoint_for_active_task(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    checkpoints = SQLiteCheckpointStore(database_path)
    conversations = SQLiteConversationStore(database_path)

    await projects.initialize()
    await checkpoints.initialize()
    await conversations.initialize()

    project = ProjectRecord(
        project_id="project-1",
        name="Project",
        workspace_path=str(tmp_path),
        active_task_id="task-1",
    )
    await projects.create(project)

    await checkpoints.save(
        CheckpointRecord(
            checkpoint_id="task-1-checkpoint",
            project_id="project-1",
            task_id="task-1",
            summary="Task 1 recovery point",
            created_at=datetime(
                2026,
                8,
                24,
                1,
                0,
                tzinfo=UTC,
            ),
        )
    )

    # Newer project checkpoint belongs to task 2.
    await checkpoints.save(
        CheckpointRecord(
            checkpoint_id="task-2-checkpoint",
            project_id="project-1",
            task_id="task-2",
            summary="Task 2 newer recovery point",
            created_at=datetime(
                2026,
                8,
                24,
                2,
                0,
                tzinfo=UTC,
            ),
        )
    )

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "projects", projects)
    monkeypatch.setattr(server, "checkpoints", checkpoints)
    monkeypatch.setattr(server, "conversations", conversations)

    result = await server.resume_project("project-1")

    assert result["status"] == "resumed"
    assert result["checkpoint"] is not None
    assert (
        result["checkpoint"]["checkpoint_id"]
        == "task-1-checkpoint"
    )
