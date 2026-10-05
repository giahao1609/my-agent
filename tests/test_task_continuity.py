from __future__ import annotations

import pytest

from core.context import ExecutionContext
from core.project import ProjectRecord
from core.session import SessionRecord
from core.task import TaskRecord, TaskState
from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


@pytest.mark.asyncio
async def test_task_identity_is_shared_by_project_task_and_session(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    tasks = SQLiteTaskStore(database_path)
    sessions = SQLiteCoderSessionStore(database_path)

    await projects.initialize()
    await tasks.initialize()
    await sessions.initialize()

    project = ProjectRecord(
        project_id="project-1",
        name="Project",
        workspace_path=str(tmp_path),
    )
    await projects.create(project)

    task = TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Implement durable planning",
    )
    task.transition(TaskState.PLANNING)
    await tasks.save(task)

    project.active_task_id = task.task_id
    await projects.update(project)

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id="session-1",
        task_id=task.task_id,
        project_id=project.project_id,
    )
    session = SessionRecord(
        session_id="session-1",
        context=context,
    )
    await sessions.save(session)

    restored_project = await projects.get("project-1")
    restored_task = await tasks.get("task-1")
    restored_session = await sessions.get("session-1")

    assert restored_project is not None
    assert restored_task is not None
    assert restored_session is not None

    assert restored_project.active_task_id == "task-1"
    assert restored_task.task_id == "task-1"
    assert restored_session.context.task_id == "task-1"

    assert restored_task.project_id == restored_project.project_id
    assert restored_session.context.project_id == restored_project.project_id


@pytest.mark.asyncio
async def test_multiple_sessions_can_belong_to_same_durable_task(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    sessions = SQLiteCoderSessionStore(database_path)
    await sessions.initialize()

    for session_id in ("session-1", "session-2"):
        await sessions.save(
            SessionRecord(
                session_id=session_id,
                context=ExecutionContext(
                    workspace_id=str(tmp_path),
                    agent_id="coder",
                    session_id=session_id,
                    task_id="task-1",
                    project_id="project-1",
                ),
            )
        )

    restored = await sessions.list_for_project("project-1")

    assert {session.session_id for session in restored} == {
        "session-1",
        "session-2",
    }
    assert {
        session.context.task_id
        for session in restored
    } == {"task-1"}
