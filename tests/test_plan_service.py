from __future__ import annotations

import pytest

from core.plan import PlanState, PlanStepState
from core.plan_service import PlanService
from core.project import ProjectRecord
from core.task import TaskState
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_service(tmp_path):
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

    from core.task_service import TaskService

    task_service = TaskService(
        project_store=projects,
        task_store=tasks,
    )

    await task_service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Implement planner",
    )
    await task_service.start_task("task-1")

    return (
        PlanService(
            task_store=tasks,
            plan_store=plans,
        ),
        tasks,
        plans,
    )


@pytest.mark.asyncio
async def test_plan_service_creates_first_plan_revision(tmp_path) -> None:
    service, tasks, plans = await build_service(tmp_path)

    plan = await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )

    assert plan.task_id == "task-1"
    assert plan.revision == 1
    assert plan.state is PlanState.DRAFT

    task = await tasks.get("task-1")
    assert task is not None
    assert task.active_plan_id is None

    restored = await plans.get_plan("plan-1")
    assert restored is not None
    assert restored.revision == 1


@pytest.mark.asyncio
async def test_activate_plan_sets_task_active_plan_and_execution_state(
    tmp_path,
) -> None:
    service, tasks, _ = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )

    active = await service.activate_plan("plan-1")

    assert active.state is PlanState.ACTIVE

    task = await tasks.get("task-1")
    assert task is not None
    assert task.active_plan_id == "plan-1"
    assert task.state is TaskState.EXECUTING


@pytest.mark.asyncio
async def test_create_new_revision_increments_revision(tmp_path) -> None:
    service, _, _ = await build_service(tmp_path)

    first = await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    second = await service.create_plan(
        task_id="task-1",
        plan_id="plan-2",
    )

    assert first.revision == 1
    assert second.revision == 2


@pytest.mark.asyncio
async def test_activating_new_plan_supersedes_previous_active_plan(
    tmp_path,
) -> None:
    service, tasks, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await service.activate_plan("plan-1")

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-2",
    )
    second = await service.activate_plan("plan-2")

    first = await plans.get_plan("plan-1")

    assert first is not None
    assert first.state is PlanState.SUPERSEDED
    assert second.state is PlanState.ACTIVE

    task = await tasks.get("task-1")
    assert task is not None
    assert task.active_plan_id == "plan-2"
    assert task.state is TaskState.EXECUTING


@pytest.mark.asyncio
async def test_plan_service_adds_ordered_steps(tmp_path) -> None:
    service, _, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )

    first = await service.add_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect the failing tests.",
    )
    second = await service.add_step(
        plan_id="plan-1",
        step_id="step-2",
        title="Implement",
        instruction="Implement the fix.",
    )

    assert first.step_index == 0
    assert second.step_index == 1

    restored = await plans.list_steps("plan-1")

    assert [step.step_id for step in restored] == [
        "step-1",
        "step-2",
    ]


@pytest.mark.asyncio
async def test_plan_service_transitions_step_state(tmp_path) -> None:
    service, _, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await service.add_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect code.",
    )

    await service.activate_plan("plan-1")

    running = await service.start_step("step-1")
    completed = await service.complete_step("step-1")

    assert running.state is PlanStepState.RUNNING
    assert completed.state is PlanStepState.COMPLETED

    restored = await plans.get_step("step-1")
    assert restored is not None
    assert restored.state is PlanStepState.COMPLETED


@pytest.mark.asyncio
async def test_plan_service_rejects_plan_for_terminal_task(
    tmp_path,
) -> None:
    service, tasks, _ = await build_service(tmp_path)

    task = await tasks.get("task-1")
    assert task is not None

    task.transition(TaskState.CANCELLED)
    await tasks.save(task)

    with pytest.raises(ValueError, match="terminal task"):
        await service.create_plan(
            task_id="task-1",
            plan_id="plan-1",
        )


@pytest.mark.asyncio
async def test_plan_service_rejects_unknown_task(tmp_path) -> None:
    service, _, _ = await build_service(tmp_path)

    with pytest.raises(KeyError, match="unknown task"):
        await service.create_plan(
            task_id="missing",
            plan_id="plan-1",
        )


@pytest.mark.asyncio
async def test_complete_plan_rejects_unfinished_steps(tmp_path) -> None:
    service, _, _ = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await service.add_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect the code.",
    )
    await service.activate_plan("plan-1")

    with pytest.raises(ValueError, match="unfinished plan steps"):
        await service.complete_plan("plan-1")


@pytest.mark.asyncio
async def test_complete_plan_finishes_plan_but_not_task(
    tmp_path,
) -> None:
    service, tasks, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )

    await service.add_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Inspect",
        instruction="Inspect code.",
    )
    await service.add_step(
        plan_id="plan-1",
        step_id="step-2",
        title="Optional cleanup",
        instruction="Skip if unnecessary.",
    )

    await service.activate_plan("plan-1")

    await service.start_step("step-1")
    await service.complete_step("step-1")

    second = await plans.get_step("step-2")
    assert second is not None
    second.transition(PlanStepState.SKIPPED)
    await plans.save_step(second)

    completed = await service.complete_plan("plan-1")

    assert completed.state is PlanState.COMPLETED

    task = await tasks.get("task-1")

    assert task is not None

    # Completing a plan does not automatically complete the durable Task.
    assert task.state is TaskState.EXECUTING

    # The completed plan is no longer the active execution plan.
    assert task.active_plan_id is None

    restored = await plans.get_plan("plan-1")

    assert restored is not None
    assert restored.state is PlanState.COMPLETED






@pytest.mark.asyncio
async def test_start_step_requires_active_plan(
    tmp_path,
) -> None:
    service, _, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await service.add_step(
        plan_id="plan-1",
        step_id="step-active-guard",
        title="Inspect",
        instruction="Inspect code.",
    )

    with pytest.raises(
        ValueError,
        match="active plan",
    ):
        await service.start_step(
            "step-active-guard"
        )

    restored = await plans.get_step(
        "step-active-guard"
    )
    assert restored is not None
    assert restored.state is PlanStepState.PENDING


@pytest.mark.asyncio
async def test_start_step_requires_task_active_plan_match(
    tmp_path,
) -> None:
    service, tasks, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await service.add_step(
        plan_id="plan-1",
        step_id="step-plan-1",
        title="Old",
        instruction="Old plan step.",
    )
    await service.activate_plan("plan-1")

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-2",
    )
    await service.add_step(
        plan_id="plan-2",
        step_id="step-plan-2",
        title="New",
        instruction="New plan step.",
    )
    await service.activate_plan("plan-2")

    task = await tasks.get("task-1")
    assert task is not None
    assert task.active_plan_id == "plan-2"

    with pytest.raises(
        ValueError,
        match="active plan",
    ):
        await service.start_step(
            "step-plan-1"
        )

    old_step = await plans.get_step(
        "step-plan-1"
    )
    assert old_step is not None
    assert old_step.state is PlanStepState.PENDING


@pytest.mark.asyncio
async def test_fail_running_step(
    tmp_path,
) -> None:
    service, _, plans = await build_service(tmp_path)

    await service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await service.add_step(
        plan_id="plan-1",
        step_id="step-fail",
        title="Execute",
        instruction="Execute work.",
    )
    await service.activate_plan("plan-1")

    await service.start_step(
        "step-fail"
    )

    failed = await service.fail_step(
        "step-fail"
    )

    assert failed.state is PlanStepState.FAILED

    restored = await plans.get_step(
        "step-fail"
    )
    assert restored is not None
    assert restored.state is PlanStepState.FAILED
