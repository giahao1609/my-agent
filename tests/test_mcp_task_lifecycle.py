from __future__ import annotations

import pytest

from core.project import ProjectRecord
from core.task import TaskState
from my_agent_mcp import server
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_stores(tmp_path):
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    tasks = SQLiteTaskStore(database_path)

    await projects.initialize()
    await tasks.initialize()

    await projects.create(
        ProjectRecord(
            project_id="project-1",
            name="Project",
            workspace_path=str(tmp_path),
        )
    )

    return projects, tasks


def patch_server(monkeypatch, projects, tasks) -> None:
    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "projects", projects)
    monkeypatch.setattr(server, "tasks", tasks, raising=False)


@pytest.mark.asyncio
async def test_mcp_create_task_persists_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    result = await server.create_task(
        objective="Implement planner",
        project_id="project-1",
        task_id="task-1",
    )

    assert result["status"] == "created"
    assert result["task"]["task_id"] == "task-1"
    assert result["task"]["project_id"] == "project-1"
    assert result["task"]["state"] == TaskState.CREATED.value

    restored = await tasks.get("task-1")
    assert restored is not None
    assert restored.objective == "Implement planner"


@pytest.mark.asyncio
async def test_mcp_start_task_sets_project_active_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    await server.create_task(
        objective="Implement planner",
        project_id="project-1",
        task_id="task-1",
    )

    result = await server.start_task(
        task_id="task-1",
    )

    assert result["status"] == "started"
    assert result["task"]["state"] == TaskState.PLANNING.value

    project = await projects.get("project-1")
    assert project is not None
    assert project.active_task_id == "task-1"


@pytest.mark.asyncio
async def test_mcp_get_active_task_returns_active_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    await server.create_task(
        objective="Task one",
        project_id="project-1",
        task_id="task-1",
    )
    await server.start_task(task_id="task-1")

    result = await server.get_active_task(
        project_id="project-1",
    )

    assert result["status"] == "ok"
    assert result["task"]["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_mcp_list_tasks_is_project_scoped(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    await server.create_task(
        objective="Task one",
        project_id="project-1",
        task_id="task-1",
    )
    await server.create_task(
        objective="Task two",
        project_id="project-1",
        task_id="task-2",
    )

    result = await server.list_tasks(
        project_id="project-1",
    )

    assert result["status"] == "ok"
    assert {
        task["task_id"]
        for task in result["tasks"]
    } == {
        "task-1",
        "task-2",
    }


@pytest.mark.asyncio
async def test_mcp_cancel_task_clears_active_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    await server.create_task(
        objective="Cancel me",
        project_id="project-1",
        task_id="task-1",
    )
    await server.start_task(task_id="task-1")

    result = await server.cancel_task(
        task_id="task-1",
    )

    assert result["status"] == "cancelled"
    assert result["task"]["state"] == TaskState.CANCELLED.value

    project = await projects.get("project-1")
    assert project is not None
    assert project.active_task_id is None


@pytest.mark.asyncio
async def test_mcp_complete_task_clears_active_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    await server.create_task(
        objective="Complete me",
        project_id="project-1",
        task_id="task-1",
    )
    task = await tasks.get("task-1")

    assert task is not None
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTING)
    await tasks.save(task)

    project = await projects.get("project-1")
    assert project is not None
    project.active_task_id = "task-1"
    await projects.update(project)

    result = await server.complete_task(
        task_id="task-1",
    )

    assert result["status"] == "completed"
    assert result["task"]["state"] == TaskState.COMPLETED.value

    project = await projects.get("project-1")
    assert project is not None
    assert project.active_task_id is None


@pytest.mark.asyncio
async def test_mcp_cancel_task_rejects_when_task_has_resumable_session(
    tmp_path,
    monkeypatch,
) -> None:
    from agents.coder_stack import build_coder_agent_stack
    from core.context import ExecutionContext
    from core.session import SessionRecord, SessionState

    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    stack = await build_coder_agent_stack(tmp_path / "agent.db")

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )

    await server.create_task(
        objective="Active task",
        project_id="project-1",
        task_id="task-1",
    )
    await server.start_task(task_id="task-1")

    await stack.session_store.save(
        SessionRecord(
            session_id="session-1",
            context=ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="session-1",
                task_id="task-1",
                project_id="project-1",
            ),
            state=SessionState.RUNNING,
        )
    )

    result = await server.cancel_task(
        task_id="task-1",
    )

    assert result["status"] == "active_session"
    assert result["session_id"] == "session-1"

    restored = await tasks.get("task-1")

    assert restored is not None
    assert restored.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_mcp_complete_task_rejects_when_task_has_resumable_session(
    tmp_path,
    monkeypatch,
) -> None:
    from agents.coder_stack import build_coder_agent_stack
    from core.context import ExecutionContext
    from core.session import SessionRecord, SessionState

    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    stack = await build_coder_agent_stack(tmp_path / "agent.db")

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )

    await server.create_task(
        objective="Executing task",
        project_id="project-1",
        task_id="task-1",
    )
    await server.start_task(task_id="task-1")

    task = await tasks.get("task-1")
    assert task is not None
    task.transition(TaskState.EXECUTING)
    await tasks.save(task)

    await stack.session_store.save(
        SessionRecord(
            session_id="session-1",
            context=ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="session-1",
                task_id="task-1",
                project_id="project-1",
            ),
            state=SessionState.WAITING_APPROVAL,
        )
    )

    result = await server.complete_task(
        task_id="task-1",
    )

    assert result["status"] == "active_session"
    assert result["session_id"] == "session-1"

    restored = await tasks.get("task-1")

    assert restored is not None
    assert restored.state is TaskState.EXECUTING


@pytest.mark.asyncio
async def test_mcp_cancel_task_after_coder_session_is_cancelled(
    tmp_path,
    monkeypatch,
) -> None:
    from agents.coder_stack import build_coder_agent_stack
    from core.session import SessionState

    projects, tasks = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks)

    stack = await build_coder_agent_stack(tmp_path / "agent.db")

    async def fake_get_coder_stack():
        return stack

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        return await projects.get(project_id or "project-1")

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )

    await server.create_task(
        objective="Cancelable task",
        project_id="project-1",
        task_id="task-1",
    )
    await server.start_task(
        task_id="task-1",
    )

    started = await server.start_coder_session(
        "run task",
        project_id="project-1",
    )
    session_id = str(started["session_id"])

    blocked = await server.cancel_task(
        task_id="task-1",
    )

    assert blocked["status"] == "active_session"
    assert blocked["session_id"] == session_id

    cancelled_session = await server.cancel_coder_session(
        session_id=session_id,
        project_id="project-1",
    )

    assert cancelled_session["status"] == "cancelled"

    durable_session = await stack.session_store.get(session_id)

    assert durable_session is not None
    assert durable_session.state is SessionState.STOPPED

    cancelled_task = await server.cancel_task(
        task_id="task-1",
    )

    assert cancelled_task["status"] == "cancelled"
    assert (
        cancelled_task["task"]["state"]
        == TaskState.CANCELLED.value
    )

    durable_task = await tasks.get("task-1")

    assert durable_task is not None
    assert durable_task.state is TaskState.CANCELLED

    project = await projects.get("project-1")

    assert project is not None
    assert project.active_task_id is None
