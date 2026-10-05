from __future__ import annotations

import pytest

from core.project import ProjectRecord
from core.task import TaskState
from core.task_service import TaskService
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_service(tmp_path):
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    tasks = SQLiteTaskStore(database_path)

    await projects.initialize()
    await tasks.initialize()

    project = ProjectRecord(
        project_id="project-1",
        name="Project",
        workspace_path=str(tmp_path),
    )
    await projects.create(project)

    return TaskService(
        project_store=projects,
        task_store=tasks,
    ), projects, tasks


@pytest.mark.asyncio
async def test_task_service_creates_durable_task(tmp_path) -> None:
    service, _, tasks = await build_service(tmp_path)

    created = await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Implement planning lifecycle",
    )

    assert created.task_id == "task-1"
    assert created.project_id == "project-1"
    assert created.objective == "Implement planning lifecycle"
    assert created.state is TaskState.CREATED

    restored = await tasks.get("task-1")

    assert restored is not None
    assert restored.state is TaskState.CREATED


@pytest.mark.asyncio
async def test_start_task_activates_project_and_enters_planning(
    tmp_path,
) -> None:
    service, projects, _ = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Implement planner",
    )

    started = await service.start_task("task-1")

    assert started.state is TaskState.PLANNING

    project = await projects.get("project-1")

    assert project is not None
    assert project.active_task_id == "task-1"


@pytest.mark.asyncio
async def test_get_active_task_returns_project_active_task(
    tmp_path,
) -> None:
    service, _, _ = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Task one",
    )
    await service.start_task("task-1")

    active = await service.get_active_task("project-1")

    assert active is not None
    assert active.task_id == "task-1"


@pytest.mark.asyncio
async def test_cancel_active_task_clears_project_active_task(
    tmp_path,
) -> None:
    service, projects, _ = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Cancel me",
    )
    await service.start_task("task-1")

    cancelled = await service.cancel_task("task-1")

    assert cancelled.state is TaskState.CANCELLED

    project = await projects.get("project-1")

    assert project is not None
    assert project.active_task_id is None


@pytest.mark.asyncio
async def test_complete_active_executing_task_clears_project_active_task(
    tmp_path,
) -> None:
    service, projects, tasks = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Complete me",
    )
    task = await service.start_task("task-1")

    task.transition(TaskState.EXECUTING)
    await tasks.save(task)

    completed = await service.complete_task("task-1")

    assert completed.state is TaskState.COMPLETED

    project = await projects.get("project-1")

    assert project is not None
    assert project.active_task_id is None


@pytest.mark.asyncio
async def test_starting_second_task_replaces_project_active_task(
    tmp_path,
) -> None:
    service, projects, _ = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="First",
    )
    await service.create_task(
        project_id="project-1",
        task_id="task-2",
        objective="Second",
    )

    await service.start_task("task-1")
    await service.start_task("task-2")

    project = await projects.get("project-1")

    assert project is not None
    assert project.active_task_id == "task-2"

    first = await service.get_task("task-1")
    second = await service.get_task("task-2")

    assert first is not None
    assert second is not None
    assert first.state is TaskState.PLANNING
    assert second.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_task_service_rejects_unknown_project(tmp_path) -> None:
    service, _, _ = await build_service(tmp_path)

    with pytest.raises(KeyError, match="unknown project"):
        await service.create_task(
            project_id="missing-project",
            task_id="task-1",
            objective="Invalid",
        )


@pytest.mark.asyncio
async def test_task_service_rejects_terminal_task_restart(
    tmp_path,
) -> None:
    service, _, _ = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Terminal",
    )
    await service.start_task("task-1")
    await service.cancel_task("task-1")

    with pytest.raises(ValueError):
        await service.start_task("task-1")


@pytest.mark.asyncio
async def test_start_task_reactivates_executing_task_without_replanning(
    tmp_path,
) -> None:
    service, projects, tasks = await build_service(tmp_path)

    await service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Long running task",
    )
    task = await service.start_task("task-1")

    task.transition(TaskState.EXECUTING)
    await tasks.save(task)

    # Switch the project pointer away without changing task lifecycle state.
    project = await projects.get("project-1")
    assert project is not None
    project.active_task_id = None
    await projects.update(project)

    reactivated = await service.start_task("task-1")

    assert reactivated.state is TaskState.EXECUTING

    project = await projects.get("project-1")
    assert project is not None
    assert project.active_task_id == "task-1"
