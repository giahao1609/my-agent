from __future__ import annotations

import pytest

from core.plan import (
    PlanRecord,
    PlanState,
    PlanStepRecord,
    PlanStepState,
)
from core.task import TaskRecord, TaskState


def test_task_record_tracks_durable_planning_execution_lifecycle() -> None:
    task = TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Fix the failing authentication flow",
    )

    assert task.state is TaskState.CREATED
    assert task.active_plan_id is None

    task.transition(TaskState.PLANNING)
    task.set_active_plan("plan-1")
    task.transition(TaskState.EXECUTING)

    assert task.active_plan_id == "plan-1"
    assert task.state is TaskState.EXECUTING

    # Re-planning must preserve the durable task identity.
    task.transition(TaskState.PLANNING)

    assert task.task_id == "task-1"
    assert task.state is TaskState.PLANNING


def test_task_terminal_state_cannot_be_reopened() -> None:
    task = TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Complete the task",
    )

    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTING)
    task.transition(TaskState.COMPLETED)

    with pytest.raises(ValueError, match="invalid task transition"):
        task.transition(TaskState.EXECUTING)


def test_plan_supports_revision_and_superseding() -> None:
    plan = PlanRecord(
        plan_id="plan-2",
        task_id="task-1",
        revision=2,
    )

    assert plan.state is PlanState.DRAFT
    assert plan.revision == 2

    plan.transition(PlanState.ACTIVE)
    plan.transition(PlanState.SUPERSEDED)

    assert plan.state is PlanState.SUPERSEDED

    with pytest.raises(ValueError, match="invalid plan transition"):
        plan.transition(PlanState.ACTIVE)


def test_plan_step_has_independent_execution_state() -> None:
    step = PlanStepRecord(
        step_id="step-1",
        plan_id="plan-1",
        step_index=0,
        title="Inspect the failing tests",
        instruction="Use existing test failures and Code Graph context.",
    )

    assert step.state is PlanStepState.PENDING

    step.transition(PlanStepState.RUNNING)
    step.transition(PlanStepState.COMPLETED)

    assert step.state is PlanStepState.COMPLETED


def test_planning_records_validate_durable_identity() -> None:
    with pytest.raises(ValueError):
        TaskRecord(
            task_id="",
            project_id="project-1",
            objective="Invalid task",
        )

    with pytest.raises(ValueError):
        PlanRecord(
            plan_id="plan-1",
            task_id="task-1",
            revision=0,
        )

    with pytest.raises(ValueError):
        PlanStepRecord(
            step_id="step-1",
            plan_id="plan-1",
            step_index=-1,
            title="Invalid step",
            instruction="Invalid step",
        )
