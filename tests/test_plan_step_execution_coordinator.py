from __future__ import annotations

import pytest

from core.plan import PlanStepState
from core.plan_service import PlanService
from core.plan_step_execution_coordinator import (
    PlanStepExecutionCoordinator,
)
from core.project import ProjectRecord
from core.task_service import TaskService
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_execution_fixture(tmp_path):
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
        objective="Implement the requested change",
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
        instruction="Implement exactly one plan step.",
    )
    await plan_service.activate_plan("plan-1")

    return tasks, plans, plan_service


@pytest.mark.asyncio
async def test_coordinator_starts_one_plan_step_and_coder_session(
    tmp_path,
) -> None:
    tasks, plans, plan_service = await build_execution_fixture(
        tmp_path
    )

    class Launcher:
        def __init__(self) -> None:
            self.calls = []

        async def start(
            self,
            *,
            task,
            step,
        ) -> str:
            self.calls.append((task, step))
            return "coder-session-1"

    launcher = Launcher()

    coordinator = PlanStepExecutionCoordinator(
        task_store=tasks,
        plan_store=plans,
        plan_service=plan_service,
        launcher=launcher,
    )

    result = await coordinator.start_step_execution(
        "step-1"
    )

    assert result.step_id == "step-1"
    assert result.session_id == "coder-session-1"

    restored = await plans.get_step("step-1")
    assert restored is not None
    assert restored.state is PlanStepState.RUNNING
    assert restored.execution_session_id == "coder-session-1"

    assert len(launcher.calls) == 1

    task_arg, step_arg = launcher.calls[0]
    assert task_arg.task_id == "task-1"
    assert task_arg.project_id == "project-1"
    assert step_arg.step_id == "step-1"
    assert step_arg.instruction == (
        "Implement exactly one plan step."
    )


@pytest.mark.asyncio
async def test_coordinator_marks_step_failed_when_launcher_fails(
    tmp_path,
) -> None:
    tasks, plans, plan_service = await build_execution_fixture(
        tmp_path
    )

    class Launcher:
        async def start(
            self,
            *,
            task,
            step,
        ) -> str:
            raise RuntimeError("coder launch failed")

    coordinator = PlanStepExecutionCoordinator(
        task_store=tasks,
        plan_store=plans,
        plan_service=plan_service,
        launcher=Launcher(),
    )

    with pytest.raises(
        RuntimeError,
        match="coder launch failed",
    ):
        await coordinator.start_step_execution(
            "step-1"
        )

    restored = await plans.get_step("step-1")
    assert restored is not None
    assert restored.state is PlanStepState.FAILED


@pytest.mark.asyncio
async def test_coordinator_rejects_non_active_step_before_launcher(
    tmp_path,
) -> None:
    tasks, plans, plan_service = await build_execution_fixture(
        tmp_path
    )

    await plan_service.create_plan(
        task_id="task-1",
        plan_id="plan-2",
    )
    await plan_service.add_step(
        plan_id="plan-2",
        step_id="step-2",
        title="Future",
        instruction="Do not run yet.",
    )

    class Launcher:
        def __init__(self) -> None:
            self.calls = 0

        async def start(
            self,
            *,
            task,
            step,
        ) -> str:
            self.calls += 1
            return "must-not-start"

    launcher = Launcher()

    coordinator = PlanStepExecutionCoordinator(
        task_store=tasks,
        plan_store=plans,
        plan_service=plan_service,
        launcher=launcher,
    )

    with pytest.raises(
        ValueError,
        match="active plan",
    ):
        await coordinator.start_step_execution(
            "step-2"
        )

    assert launcher.calls == 0

    restored = await plans.get_step("step-2")
    assert restored is not None
    assert restored.state is PlanStepState.PENDING


@pytest.mark.asyncio
async def test_coordinator_rejects_empty_session_id_and_fails_step(
    tmp_path,
) -> None:
    tasks, plans, plan_service = await build_execution_fixture(
        tmp_path
    )

    class Launcher:
        async def start(
            self,
            *,
            task,
            step,
        ) -> str:
            return "   "

    coordinator = PlanStepExecutionCoordinator(
        task_store=tasks,
        plan_store=plans,
        plan_service=plan_service,
        launcher=Launcher(),
    )

    with pytest.raises(
        ValueError,
        match="session_id",
    ):
        await coordinator.start_step_execution(
            "step-1"
        )

    restored = await plans.get_step("step-1")
    assert restored is not None
    assert restored.state is PlanStepState.FAILED
