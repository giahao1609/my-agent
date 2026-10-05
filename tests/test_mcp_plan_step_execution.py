from __future__ import annotations

import pytest

from core.plan import PlanStepState
from core.plan_service import PlanService
from core.project import ProjectRecord
from core.task_service import TaskService
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore

from my_agent_mcp import server


async def build_fixture(tmp_path):
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

    task_service = TaskService(
        project_store=projects,
        task_store=tasks,
    )
    plan_service = PlanService(
        task_store=tasks,
        plan_store=plans,
    )

    await task_service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Implement requested change",
    )
    await task_service.start_task("task-1")

    await plan_service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await plan_service.add_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Implement",
        instruction="Implement exactly this plan step.",
    )
    await plan_service.activate_plan("plan-1")

    return projects, tasks, plans


@pytest.mark.asyncio
async def test_mcp_starts_plan_step_execution_with_explicit_task(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_fixture(
        tmp_path
    )

    calls = []

    async def fake_initialize() -> None:
        return None

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
        return "coder-session-step-1"

    monkeypatch.setattr(
        server,
        "projects",
        projects,
    )
    monkeypatch.setattr(
        server,
        "tasks",
        tasks,
    )
    monkeypatch.setattr(
        server,
        "plans",
        plans,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_start_coder_session_for_project",
        fake_shared_start,
    )

    result = await server.start_plan_step_execution(
        "step-1"
    )

    assert calls == [
        {
            "project_id": "project-1",
            "message": (
                "Implement exactly this plan step."
            ),
            "task_id": "task-1",
        }
    ]

    assert result["status"] == "started"
    assert result["step_id"] == "step-1"
    assert result["session_id"] == (
        "coder-session-step-1"
    )

    restored = await plans.get_step("step-1")

    assert restored is not None
    assert restored.state is PlanStepState.RUNNING
    assert restored.execution_session_id == (
        "coder-session-step-1"
    )


@pytest.mark.asyncio
async def test_mcp_marks_plan_step_failed_when_coder_launch_fails(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_fixture(
        tmp_path
    )

    async def fake_initialize() -> None:
        return None

    async def failing_shared_start(
        *,
        project,
        message: str,
        task_id: str | None,
    ) -> str:
        raise RuntimeError(
            "coder session launch failed"
        )

    monkeypatch.setattr(
        server,
        "projects",
        projects,
    )
    monkeypatch.setattr(
        server,
        "tasks",
        tasks,
    )
    monkeypatch.setattr(
        server,
        "plans",
        plans,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_start_coder_session_for_project",
        failing_shared_start,
    )

    with pytest.raises(
        RuntimeError,
        match="coder session launch failed",
    ):
        await server.start_plan_step_execution(
            "step-1"
        )

    restored = await plans.get_step("step-1")

    assert restored is not None
    assert restored.state is PlanStepState.FAILED
    assert restored.execution_session_id is None
