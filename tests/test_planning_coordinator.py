from __future__ import annotations

import pytest

from core.plan import PlanState, PlanStepState
from core.plan_service import PlanService
from core.planner import (
    PlannerContext,
    PlanProposal,
    PlanStepProposal,
)
from core.planning_coordinator import PlanningCoordinator
from core.project import ProjectRecord
from core.task import TaskState
from core.task_service import TaskService
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_coordinator(tmp_path, planner):
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

    await task_service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Implement planner",
    )
    await task_service.start_task("task-1")

    plan_service = PlanService(
        task_store=tasks,
        plan_store=plans,
    )

    coordinator = PlanningCoordinator(
        planner=planner,
        task_store=tasks,
        plan_service=plan_service,
    )

    return coordinator, tasks, plans


@pytest.mark.asyncio
async def test_planning_coordinator_materializes_proposal_as_draft(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            assert task.task_id == "task-1"
            assert context.project_context == "project"
            assert context.code_context == "code"

            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="Inspect",
                        instruction="Inspect the code.",
                    ),
                    PlanStepProposal(
                        title="Implement",
                        instruction="Implement the change.",
                    ),
                )
            )

    coordinator, tasks, plans = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    plan = await coordinator.propose_plan(
        task_id="task-1",
        plan_id="plan-1",
        step_ids=("step-1", "step-2"),
        context=PlannerContext(
            project_context="project",
            code_context="code",
        ),
    )

    assert plan.plan_id == "plan-1"
    assert plan.revision == 1
    assert plan.state is PlanState.DRAFT

    steps = await plans.list_steps("plan-1")

    assert [step.step_id for step in steps] == [
        "step-1",
        "step-2",
    ]
    assert [step.step_index for step in steps] == [0, 1]
    assert [step.title for step in steps] == [
        "Inspect",
        "Implement",
    ]
    assert all(
        step.state is PlanStepState.PENDING
        for step in steps
    )

    task = await tasks.get("task-1")

    assert task is not None
    assert task.state is TaskState.PLANNING
    assert task.active_plan_id is None


@pytest.mark.asyncio
async def test_planning_coordinator_does_not_activate_new_revision(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="Next",
                        instruction="Do the next revision.",
                    ),
                )
            )

    coordinator, tasks, plans = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    first = await coordinator.propose_plan(
        task_id="task-1",
        plan_id="plan-1",
        step_ids=("step-1",),
        context=PlannerContext(),
    )

    plan_service = PlanService(
        task_store=tasks,
        plan_store=plans,
    )
    await plan_service.activate_plan(first.plan_id)

    second = await coordinator.propose_plan(
        task_id="task-1",
        plan_id="plan-2",
        step_ids=("step-2",),
        context=PlannerContext(),
    )

    assert second.revision == 2
    assert second.state is PlanState.DRAFT

    old = await plans.get_plan("plan-1")
    assert old is not None
    assert old.state is PlanState.ACTIVE

    task = await tasks.get("task-1")
    assert task is not None
    assert task.state is TaskState.EXECUTING
    assert task.active_plan_id == "plan-1"


@pytest.mark.asyncio
async def test_planning_coordinator_requires_matching_step_ids(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="One",
                        instruction="First.",
                    ),
                    PlanStepProposal(
                        title="Two",
                        instruction="Second.",
                    ),
                )
            )

    coordinator, _, _ = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    with pytest.raises(ValueError, match="step ids"):
        await coordinator.propose_plan(
            task_id="task-1",
            plan_id="plan-1",
            step_ids=("step-1",),
            context=PlannerContext(),
        )


@pytest.mark.asyncio
async def test_planning_coordinator_rejects_unknown_task(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            raise AssertionError("planner must not be called")

    coordinator, _, _ = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    with pytest.raises(KeyError, match="unknown task"):
        await coordinator.propose_plan(
            task_id="missing",
            plan_id="plan-1",
            step_ids=("step-1",),
            context=PlannerContext(),
        )


@pytest.mark.asyncio
async def test_planning_coordinator_does_not_call_planner_for_terminal_task(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            raise AssertionError("planner must not be called")

    coordinator, tasks, plans = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    task = await tasks.get("task-1")
    assert task is not None

    task.transition(TaskState.CANCELLED)
    await tasks.save(task)

    with pytest.raises(ValueError, match="terminal task"):
        await coordinator.propose_plan(
            task_id="task-1",
            plan_id="plan-1",
            step_ids=("step-1",),
            context=PlannerContext(),
        )

    assert await plans.get_plan("plan-1") is None


@pytest.mark.asyncio
async def test_planning_coordinator_rejects_duplicate_step_ids_before_write(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="One",
                        instruction="First.",
                    ),
                    PlanStepProposal(
                        title="Two",
                        instruction="Second.",
                    ),
                )
            )

    coordinator, _, plans = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    with pytest.raises(ValueError, match="unique"):
        await coordinator.propose_plan(
            task_id="task-1",
            plan_id="plan-1",
            step_ids=("step-1", "step-1"),
            context=PlannerContext(),
        )

    # Invalid materialization input must not leave a partial draft behind.
    assert await plans.get_plan("plan-1") is None
    assert await plans.list_steps("plan-1") == ()


@pytest.mark.asyncio
async def test_planning_coordinator_does_not_leave_partial_plan_on_step_write_failure(
    tmp_path,
) -> None:
    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="One",
                        instruction="First step.",
                    ),
                    PlanStepProposal(
                        title="Two",
                        instruction="Second step.",
                    ),
                )
            )

    coordinator, _, plans = await build_coordinator(
        tmp_path,
        FakePlanner(),
    )

    original_insert_step = plans._insert_step
    calls = 0

    def failing_insert_step(connection, step) -> None:
        nonlocal calls
        calls += 1

        if calls == 2:
            raise RuntimeError(
                "simulated step write failure"
            )

        original_insert_step(connection, step)

    plans._insert_step = failing_insert_step

    with pytest.raises(
        RuntimeError,
        match="simulated step write failure",
    ):
        await coordinator.propose_plan(
            task_id="task-1",
            plan_id="plan-1",
            step_ids=("step-1", "step-2"),
            context=PlannerContext(),
        )

    # Materializing one proposal must be atomic:
    # either the complete Draft Plan exists, or nothing exists.
    assert await plans.get_plan("plan-1") is None
    assert await plans.list_steps("plan-1") == ()
