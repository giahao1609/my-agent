from __future__ import annotations

import pytest

from core.agent_role import AgentRole
from core.plan import PlanRecord, PlanStepRecord
from core.plan_service import PlanService
from core.plan_step_execution_coordinator import PlanStepExecutionCoordinator
from core.task import TaskRecord, TaskState
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_task_store import SQLiteTaskStore


class FakeLauncher:
    async def start(self, *, task: TaskRecord, step: PlanStepRecord) -> str:
        return f"session-{step.step_id}"


@pytest.mark.asyncio
async def test_plan_step_record_assigned_role_default() -> None:
    step = PlanStepRecord(
        step_id="step-1",
        plan_id="plan-1",
        step_index=0,
        title="Implement API",
        instruction="Write backend logic",
    )
    assert step.assigned_role == AgentRole.BACKEND_CODER


@pytest.mark.asyncio
async def test_sqlite_plan_store_assigned_role_persistence(tmp_path) -> None:
    db_path = tmp_path / "test_plan_role.db"
    store = SQLitePlanStore(db_path)
    await store.initialize()

    plan = PlanRecord(plan_id="plan-1", task_id="task-1")
    step = PlanStepRecord(
        step_id="step-1",
        plan_id="plan-1",
        step_index=0,
        title="Build UI",
        instruction="Create payment button",
        assigned_role=AgentRole.UI_CODER,
    )

    await store.save_plan_with_steps(plan, (step,))

    loaded_step = await store.get_step("step-1")
    assert loaded_step is not None
    assert loaded_step.assigned_role == AgentRole.UI_CODER


@pytest.mark.asyncio
async def test_plan_service_and_coordinator_with_assigned_role(tmp_path) -> None:
    db_path = tmp_path / "test_plan_service_role.db"
    task_store = SQLiteTaskStore(db_path)
    plan_store = SQLitePlanStore(db_path)
    await task_store.initialize()
    await plan_store.initialize()

    task = TaskRecord(task_id="task-1", project_id="proj-1", objective="Test roles")
    task.state = TaskState.EXECUTING
    await task_store.save(task)

    plan_service = PlanService(task_store=task_store, plan_store=plan_store)
    await plan_service.materialize_plan(
        task_id="task-1",
        plan_id="plan-1",
        steps=(("step-1", "Inspect UI", "Check components"),),
    )

    ui_step = await plan_service.add_step(
        plan_id="plan-1",
        step_id="step-2",
        title="Build Component",
        instruction="Create UI component",
        assigned_role=AgentRole.UI_CODER,
    )
    assert ui_step.assigned_role == AgentRole.UI_CODER

    await plan_service.activate_plan("plan-1")

    launcher = FakeLauncher()
    coordinator = PlanStepExecutionCoordinator(
        task_store=task_store,
        plan_store=plan_store,
        plan_service=plan_service,
        launcher=launcher,
    )

    exec_result = await coordinator.start_step_execution("step-2")
    assert exec_result.session_id == "session-step-2"
    assert exec_result.assigned_role == AgentRole.UI_CODER
