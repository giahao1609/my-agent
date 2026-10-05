from __future__ import annotations

import pytest

from core.plan import PlanState, PlanStepState
from core.project import ProjectRecord
from core.task import TaskState
from my_agent_mcp import server
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_stores(tmp_path):
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    tasks = SQLiteTaskStore(database_path)
    plans = SQLitePlanStore(database_path)

    await projects.initialize()
    await tasks.initialize()
    await plans.initialize()

    await projects.create(
        ProjectRecord(
            project_id="project-1",
            name="Project",
            workspace_path=str(tmp_path),
        )
    )

    return projects, tasks, plans


def patch_server(monkeypatch, projects, tasks, plans) -> None:
    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "projects", projects)
    monkeypatch.setattr(server, "tasks", tasks, raising=False)
    monkeypatch.setattr(server, "plans", plans, raising=False)


async def create_started_task() -> None:
    await server.create_task(
        objective="Implement planner",
        project_id="project-1",
        task_id="task-1",
    )
    await server.start_task(task_id="task-1")


@pytest.mark.asyncio
async def test_mcp_create_plan_creates_revision(tmp_path, monkeypatch) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    first = await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    second = await server.create_plan(
        task_id="task-1",
        plan_id="plan-2",
    )

    assert first["status"] == "created"
    assert first["plan"]["revision"] == 1
    assert first["plan"]["state"] == PlanState.DRAFT.value

    assert second["plan"]["revision"] == 2


@pytest.mark.asyncio
async def test_mcp_add_plan_step_preserves_order(tmp_path, monkeypatch) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )

    first = await server.add_plan_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect failing tests.",
    )
    second = await server.add_plan_step(
        plan_id="plan-1",
        step_id="step-2",
        title="Implement",
        instruction="Implement the fix.",
    )

    assert first["step"]["step_index"] == 0
    assert second["step"]["step_index"] == 1


@pytest.mark.asyncio
async def test_mcp_activate_plan_sets_task_execution_state(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )

    result = await server.activate_plan(
        plan_id="plan-1",
    )

    assert result["status"] == "activated"
    assert result["plan"]["state"] == PlanState.ACTIVE.value

    task = await tasks.get("task-1")

    assert task is not None
    assert task.state is TaskState.EXECUTING
    assert task.active_plan_id == "plan-1"


@pytest.mark.asyncio
async def test_mcp_plan_step_execution_lifecycle(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await server.add_plan_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect code.",
    )

    await server.activate_plan(plan_id="plan-1")

    running = await server.start_plan_step(
        step_id="step-1",
    )
    completed = await server.complete_plan_step(
        step_id="step-1",
    )

    assert running["step"]["state"] == PlanStepState.RUNNING.value
    assert completed["step"]["state"] == PlanStepState.COMPLETED.value


@pytest.mark.asyncio
async def test_mcp_complete_plan_rejects_unfinished_steps(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await server.add_plan_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect code.",
    )
    await server.activate_plan(plan_id="plan-1")

    with pytest.raises(ValueError, match="unfinished plan steps"):
        await server.complete_plan(plan_id="plan-1")


@pytest.mark.asyncio
async def test_mcp_complete_plan_clears_active_plan_but_not_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await server.add_plan_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect code.",
    )

    await server.activate_plan(plan_id="plan-1")
    await server.start_plan_step(step_id="step-1")
    await server.complete_plan_step(step_id="step-1")

    result = await server.complete_plan(
        plan_id="plan-1",
    )

    assert result["status"] == "completed"
    assert result["plan"]["state"] == PlanState.COMPLETED.value

    task = await tasks.get("task-1")

    assert task is not None
    assert task.state is TaskState.EXECUTING
    assert task.active_plan_id is None


@pytest.mark.asyncio
async def test_mcp_list_plans_returns_task_revisions(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)
    patch_server(monkeypatch, projects, tasks, plans)

    await create_started_task()

    await server.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await server.create_plan(
        task_id="task-1",
        plan_id="plan-2",
    )

    result = await server.list_plans(
        task_id="task-1",
    )

    assert result["status"] == "ok"
    assert [
        plan["revision"]
        for plan in result["plans"]
    ] == [1, 2]
