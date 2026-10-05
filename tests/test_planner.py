from __future__ import annotations

import pytest

from core.planner import (
    Planner,
    PlannerContext,
    PlanProposal,
    PlanStepProposal,
)
from core.task import TaskRecord


def test_plan_step_proposal_requires_title() -> None:
    with pytest.raises(ValueError, match="title"):
        PlanStepProposal(
            title="",
            instruction="Inspect the implementation.",
        )


def test_plan_step_proposal_requires_instruction() -> None:
    with pytest.raises(ValueError, match="instruction"):
        PlanStepProposal(
            title="Inspect",
            instruction="",
        )


def test_plan_proposal_requires_at_least_one_step() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        PlanProposal(steps=())


def test_plan_proposal_normalizes_steps_to_tuple() -> None:
    step = PlanStepProposal(
        title="Inspect",
        instruction="Inspect the failing code.",
    )

    proposal = PlanProposal(steps=[step])

    assert proposal.steps == (step,)
    assert isinstance(proposal.steps, tuple)


@pytest.mark.asyncio
async def test_planner_protocol_proposes_without_owning_durable_state() -> None:
    class FakePlanner:
        async def propose(
            self,
            task: TaskRecord,
            *,
            context: PlannerContext,
        ) -> PlanProposal:
            assert task.task_id == "task-1"
            assert task.objective == "Implement planner"
            assert context.project_context == "project summary"
            assert context.code_context == "symbol context"

            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="Inspect",
                        instruction="Inspect the relevant symbols.",
                    ),
                    PlanStepProposal(
                        title="Implement",
                        instruction="Implement the required changes.",
                    ),
                )
            )

    planner: Planner = FakePlanner()

    task = TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Implement planner",
    )

    proposal = await planner.propose(
        task,
        context=PlannerContext(
            project_context="project summary",
            code_context="symbol context",
        ),
    )

    assert [step.title for step in proposal.steps] == [
        "Inspect",
        "Implement",
    ]

    assert not hasattr(proposal, "plan_id")
    assert not hasattr(proposal, "state")
