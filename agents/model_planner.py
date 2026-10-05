from __future__ import annotations

import json
from collections.abc import Mapping

from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from core.planner import (
    PlannerContext,
    PlanProposal,
    PlanStepProposal,
)
from core.protocols import ModelBackend
from core.task import TaskRecord


class ModelPlanner:
    def __init__(
        self,
        *,
        model: ModelBackend,
        target: ExecutionTarget | None = None,
    ) -> None:
        self._model = model
        self._target = target

    async def propose(
        self,
        task: TaskRecord,
        *,
        context: PlannerContext,
    ) -> PlanProposal:
        workspace_id = context.workspace_id

        if workspace_id is None:
            raise ValueError(
                "workspace identity is required for planning"
            )

        execution_context = ExecutionContext(
            workspace_id=workspace_id,
            agent_id="planner",
            task_id=task.task_id,
            project_id=task.project_id,
        )

        messages = self._messages(
            task=task,
            context=context,
        )

        if self._target is None:
            turn = await self._model.generate(
                messages,
                (),
                execution_context,
            )
        else:
            turn = await self._model.generate(
                messages,
                (),
                execution_context,
                target=self._target,
            )

        if not isinstance(turn, ModelTurn):
            raise TypeError(
                "model.generate must return ModelTurn"
            )

        if turn.tool_calls:
            raise ValueError(
                "planner model response must not contain tool calls"
            )

        if turn.text is None or not turn.text.strip():
            raise ValueError(
                "planner model response must contain text"
            )

        return self._parse_proposal(turn.text)

    @staticmethod
    def _messages(
        *,
        task: TaskRecord,
        context: PlannerContext,
    ) -> tuple[ModelMessage, ...]:
        system = ModelMessage(
            role="system",
            content=(
                "You are a planning component. "
                "Produce only valid JSON with this shape: "
                '{"steps":[{"title":"...",'
                '"instruction":"..."}]}. '
                "Do not call tools. "
                "Do not include plan ids, revisions, lifecycle "
                "states, markdown, or commentary."
            ),
        )

        user = ModelMessage(
            role="user",
            content=(
                f"Task objective:\n{task.objective}\n\n"
                f"Project context:\n"
                f"{context.project_context}\n\n"
                f"Code context:\n"
                f"{context.code_context}"
            ),
        )

        return (system, user)

    @staticmethod
    def _parse_proposal(text: str) -> PlanProposal:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "planner response must be valid JSON"
            ) from exc

        if not isinstance(payload, Mapping):
            raise ValueError(
                "planner response must be a JSON object"
            )

        raw_steps = payload.get("steps")

        if not isinstance(raw_steps, list):
            raise ValueError(
                "planner response steps must be a list"
            )

        steps: list[PlanStepProposal] = []

        for index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, Mapping):
                raise ValueError(
                    f"planner step {index} must be an object"
                )

            title = raw_step.get("title")
            instruction = raw_step.get("instruction")

            if not isinstance(title, str):
                raise ValueError(
                    f"planner step {index} title must be a string"
                )

            if not isinstance(instruction, str):
                raise ValueError(
                    f"planner step {index} instruction "
                    "must be a string"
                )

            steps.append(
                PlanStepProposal(
                    title=title,
                    instruction=instruction,
                )
            )

        return PlanProposal(steps=steps)
