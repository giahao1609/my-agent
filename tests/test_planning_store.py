from __future__ import annotations

import pytest

from core.plan import (
    PlanRecord,
    PlanState,
    PlanStepRecord,
    PlanStepState,
)
from core.task import TaskRecord, TaskState
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_task_store import SQLiteTaskStore


@pytest.mark.asyncio
async def test_task_store_round_trips_and_lists_by_project(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "agent.db")
    await store.initialize()

    first = TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Fix authentication",
    )
    first.transition(TaskState.PLANNING)
    first.set_active_plan("plan-1")

    second = TaskRecord(
        task_id="task-2",
        project_id="project-1",
        objective="Improve tests",
    )

    await store.save(first)
    await store.save(second)

    restored = await store.get("task-1")

    assert restored is not None
    assert restored.task_id == "task-1"
    assert restored.project_id == "project-1"
    assert restored.objective == "Fix authentication"
    assert restored.state is TaskState.PLANNING
    assert restored.active_plan_id == "plan-1"

    tasks = await store.list_for_project("project-1")

    assert {task.task_id for task in tasks} == {
        "task-1",
        "task-2",
    }


@pytest.mark.asyncio
async def test_task_store_updates_existing_task(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "agent.db")
    await store.initialize()

    task = TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Fix bug",
    )
    await store.save(task)

    task.transition(TaskState.PLANNING)
    task.set_active_plan("plan-1")
    await store.save(task)

    restored = await store.get("task-1")

    assert restored is not None
    assert restored.state is TaskState.PLANNING
    assert restored.active_plan_id == "plan-1"


@pytest.mark.asyncio
async def test_plan_store_round_trips_plan_and_steps(tmp_path) -> None:
    store = SQLitePlanStore(tmp_path / "agent.db")
    await store.initialize()

    plan = PlanRecord(
        plan_id="plan-1",
        task_id="task-1",
        revision=1,
    )
    plan.transition(PlanState.ACTIVE)

    first = PlanStepRecord(
        step_id="step-1",
        plan_id="plan-1",
        step_index=0,
        title="Inspect",
        instruction="Inspect failing tests.",
    )
    first.transition(PlanStepState.RUNNING)
    first.transition(PlanStepState.COMPLETED)

    second = PlanStepRecord(
        step_id="step-2",
        plan_id="plan-1",
        step_index=1,
        title="Fix",
        instruction="Implement the fix.",
    )

    await store.save_plan(plan)
    await store.save_step(first)
    await store.save_step(second)

    restored_plan = await store.get_plan("plan-1")

    assert restored_plan is not None
    assert restored_plan.task_id == "task-1"
    assert restored_plan.revision == 1
    assert restored_plan.state is PlanState.ACTIVE

    steps = await store.list_steps("plan-1")

    assert [step.step_id for step in steps] == [
        "step-1",
        "step-2",
    ]
    assert steps[0].state is PlanStepState.COMPLETED
    assert steps[1].state is PlanStepState.PENDING


@pytest.mark.asyncio
async def test_plan_store_lists_revisions_for_task(tmp_path) -> None:
    store = SQLitePlanStore(tmp_path / "agent.db")
    await store.initialize()

    await store.save_plan(
        PlanRecord(
            plan_id="plan-1",
            task_id="task-1",
            revision=1,
        )
    )
    await store.save_plan(
        PlanRecord(
            plan_id="plan-2",
            task_id="task-1",
            revision=2,
        )
    )

    plans = await store.list_for_task("task-1")

    assert [plan.revision for plan in plans] == [1, 2]


@pytest.mark.asyncio
async def test_plan_step_order_is_deterministic(tmp_path) -> None:
    store = SQLitePlanStore(tmp_path / "agent.db")
    await store.initialize()

    await store.save_plan(
        PlanRecord(
            plan_id="plan-1",
            task_id="task-1",
        )
    )

    await store.save_step(
        PlanStepRecord(
            step_id="step-2",
            plan_id="plan-1",
            step_index=1,
            title="Second",
            instruction="Second step",
        )
    )
    await store.save_step(
        PlanStepRecord(
            step_id="step-1",
            plan_id="plan-1",
            step_index=0,
            title="First",
            instruction="First step",
        )
    )

    steps = await store.list_steps("plan-1")

    assert [step.step_index for step in steps] == [0, 1]
