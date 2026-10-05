from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from agents.model_planner import ModelPlanner
from core.context import ExecutionContext
from core.model import (
    ExecutionTarget,
    ModelMessage,
    ModelToolCall,
    ModelTurn,
)
from core.planner import PlannerContext
from core.task import TaskRecord


class FakeModelBackend:
    def __init__(self, turn: ModelTurn) -> None:
        self.turn = turn
        self.calls: list[
            tuple[
                tuple[ModelMessage, ...],
                tuple[Mapping[str, object], ...],
                ExecutionContext,
                ExecutionTarget | None,
            ]
        ] = []

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        self.calls.append(
            (
                tuple(messages),
                tuple(tools),
                context,
                target,
            )
        )
        return self.turn


def make_task() -> TaskRecord:
    return TaskRecord(
        task_id="task-1",
        project_id="project-1",
        objective="Implement durable planning",
    )


@pytest.mark.asyncio
async def test_model_planner_generates_plan_proposal_without_tools() -> None:
    model = FakeModelBackend(
        ModelTurn(
            text=(
                '{"steps":['
                '{"title":"Inspect","instruction":"Inspect the code."},'
                '{"title":"Implement","instruction":"Implement the change."}'
                ']}'
            ),
            stop=True,
        )
    )

    planner = ModelPlanner(model=model)

    proposal = await planner.propose(
        make_task(),
        context=PlannerContext(
            workspace_id="workspace-1",
            project_context="Project architecture summary",
            code_context="Relevant symbol context",
        ),
    )

    assert [step.title for step in proposal.steps] == [
        "Inspect",
        "Implement",
    ]
    assert [step.instruction for step in proposal.steps] == [
        "Inspect the code.",
        "Implement the change.",
    ]

    assert len(model.calls) == 1

    messages, tools, execution_context, target = model.calls[0]

    # Planning must not expose tool execution to the model.
    assert tools == ()

    assert execution_context.workspace_id == "workspace-1"
    assert execution_context.agent_id == "planner"
    assert execution_context.task_id == "task-1"
    assert execution_context.project_id == "project-1"
    assert execution_context.session_id is None

    assert target is None

    prompt = "\n".join(message.content for message in messages)

    assert "Implement durable planning" in prompt
    assert "Project architecture summary" in prompt
    assert "Relevant symbol context" in prompt


@pytest.mark.asyncio
async def test_model_planner_passes_explicit_execution_target() -> None:
    model = FakeModelBackend(
        ModelTurn(
            text=(
                '{"steps":['
                '{"title":"Inspect","instruction":"Inspect."}'
                ']}'
            ),
            stop=True,
        )
    )

    target = ExecutionTarget(
        runtime_id="planning-runtime",
        model_id="planning-model",
    )

    planner = ModelPlanner(
        model=model,
        target=target,
    )

    await planner.propose(
        make_task(),
        context=PlannerContext(
            workspace_id="workspace-1",
        ),
    )

    assert model.calls[0][3] == target


@pytest.mark.asyncio
async def test_model_planner_requires_workspace_identity() -> None:
    model = FakeModelBackend(
        ModelTurn(
            text=(
                '{"steps":['
                '{"title":"Inspect","instruction":"Inspect."}'
                ']}'
            )
        )
    )

    planner = ModelPlanner(model=model)

    with pytest.raises(ValueError, match="workspace"):
        await planner.propose(
            make_task(),
            context=PlannerContext(),
        )

    assert model.calls == []


@pytest.mark.asyncio
async def test_model_planner_rejects_tool_calls() -> None:
    model = FakeModelBackend(
        ModelTurn(
            text=None,
            tool_calls=(
                ModelToolCall(
                    tool_call_id="call-1",
                    name="write_file",
                    arguments={"path": "x"},
                ),
            ),
        )
    )

    planner = ModelPlanner(model=model)

    with pytest.raises(ValueError, match="tool"):
        await planner.propose(
            make_task(),
            context=PlannerContext(
                workspace_id="workspace-1",
            ),
        )


@pytest.mark.asyncio
async def test_model_planner_rejects_invalid_response_shape() -> None:
    model = FakeModelBackend(
        ModelTurn(
            text='{"steps":[{"title":"Inspect"}]}',
            stop=True,
        )
    )

    planner = ModelPlanner(model=model)

    with pytest.raises(ValueError, match="instruction"):
        await planner.propose(
            make_task(),
            context=PlannerContext(
                workspace_id="workspace-1",
            ),
        )
