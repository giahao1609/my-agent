from __future__ import annotations

from collections.abc import Callable, Sequence

from .plan import PlanRecord
from .plan_service import PlanService
from .planner import Planner, PlannerContext
from .task_store import TaskStore


class PlanningCoordinator:
    def __init__(
        self,
        *,
        planner: Planner,
        task_store: TaskStore,
        plan_service: PlanService,
    ) -> None:
        self._planner = planner
        self._task_store = task_store
        self._plan_service = plan_service

    async def propose_plan_with_generated_step_ids(
        self,
        *,
        task_id: str,
        plan_id: str,
        step_id_factory: Callable[[], str],
        context: PlannerContext,
    ) -> PlanRecord:
        task = await self._task_store.get(task_id)

        if task is None:
            raise KeyError(f"unknown task: {task_id}")

        if task.terminal:
            raise ValueError(
                f"cannot plan terminal task: {task_id}"
            )

        proposal = await self._planner.propose(
            task,
            context=context,
        )

        step_ids = tuple(
            step_id_factory()
            for _ in proposal.steps
        )

        if len(set(step_ids)) != len(step_ids):
            raise ValueError(
                "generated step ids must be unique"
            )

        materialized_steps = tuple(
            (
                step_id,
                proposed_step.title,
                proposed_step.instruction,
            )
            for step_id, proposed_step in zip(
                step_ids,
                proposal.steps,
                strict=True,
            )
        )

        return await self._plan_service.materialize_plan(
            task_id=task_id,
            plan_id=plan_id,
            steps=materialized_steps,
        )

    async def propose_plan(
        self,
        *,
        task_id: str,
        plan_id: str,
        step_ids: Sequence[str],
        context: PlannerContext,
    ) -> PlanRecord:
        task = await self._task_store.get(task_id)

        if task is None:
            raise KeyError(f"unknown task: {task_id}")

        if task.terminal:
            raise ValueError(
                f"cannot plan terminal task: {task_id}"
            )

        resolved_step_ids = tuple(step_ids)

        if len(set(resolved_step_ids)) != len(resolved_step_ids):
            raise ValueError(
                "step ids must be unique"
            )

        proposal = await self._planner.propose(
            task,
            context=context,
        )

        if len(resolved_step_ids) != len(proposal.steps):
            raise ValueError(
                "step ids must match proposal steps"
            )

        materialized_steps = tuple(
            (
                step_id,
                proposed_step.title,
                proposed_step.instruction,
            )
            for step_id, proposed_step in zip(
                resolved_step_ids,
                proposal.steps,
                strict=True,
            )
        )

        return await self._plan_service.materialize_plan(
            task_id=task_id,
            plan_id=plan_id,
            steps=materialized_steps,
        )
