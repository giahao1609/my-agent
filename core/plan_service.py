from __future__ import annotations

from collections.abc import Sequence

from .agent_role import AgentRole
from .plan import (
    PlanRecord,
    PlanState,
    PlanStepRecord,
    PlanStepState,
)
from .plan_store import PlanStore
from .task import TaskRecord, TaskState
from .task_store import TaskStore


class PlanService:
    def __init__(
        self,
        *,
        task_store: TaskStore,
        plan_store: PlanStore,
    ) -> None:
        self._task_store = task_store
        self._plan_store = plan_store

    async def create_plan(
        self,
        *,
        task_id: str,
        plan_id: str,
    ) -> PlanRecord:
        task = await self._require_task(task_id)

        if task.terminal:
            raise ValueError(
                f"cannot create plan for terminal task: {task_id}"
            )

        existing = await self._plan_store.get_plan(plan_id)
        if existing is not None:
            raise ValueError(f"plan already exists: {plan_id}")

        plans = await self._plan_store.list_for_task(task_id)
        revision = (
            max((plan.revision for plan in plans), default=0)
            + 1
        )

        plan = PlanRecord(
            plan_id=plan_id,
            task_id=task_id,
            revision=revision,
        )
        await self._plan_store.save_plan(plan)
        return plan

    async def materialize_plan(
        self,
        *,
        task_id: str,
        plan_id: str,
        steps: Sequence[tuple[str, str, str]],
    ) -> PlanRecord:
        task = await self._require_task(task_id)

        if task.terminal:
            raise ValueError(
                f"cannot create plan for terminal task: {task_id}"
            )

        existing_plan = await self._plan_store.get_plan(plan_id)
        if existing_plan is not None:
            raise ValueError(
                f"plan already exists: {plan_id}"
            )

        resolved_steps = tuple(steps)

        if not resolved_steps:
            raise ValueError(
                "plan requires at least one step"
            )

        step_ids = tuple(
            step_id
            for step_id, _, _ in resolved_steps
        )

        if len(set(step_ids)) != len(step_ids):
            raise ValueError(
                "step ids must be unique"
            )

        for step_id in step_ids:
            existing_step = await self._plan_store.get_step(
                step_id
            )
            if existing_step is not None:
                raise ValueError(
                    f"step already exists: {step_id}"
                )

        plans = await self._plan_store.list_for_task(task_id)
        revision = (
            max(
                (plan.revision for plan in plans),
                default=0,
            )
            + 1
        )

        plan = PlanRecord(
            plan_id=plan_id,
            task_id=task_id,
            revision=revision,
        )

        records = tuple(
            PlanStepRecord(
                step_id=step_id,
                plan_id=plan.plan_id,
                step_index=index,
                title=title,
                instruction=instruction,
            )
            for index, (
                step_id,
                title,
                instruction,
            ) in enumerate(resolved_steps)
        )

        await self._plan_store.save_plan_with_steps(
            plan,
            records,
        )

        return plan

    async def list_plans(
        self,
        task_id: str,
    ):
        await self._require_task(task_id)
        return await self._plan_store.list_for_task(task_id)

    async def activate_plan(
        self,
        plan_id: str,
    ) -> PlanRecord:
        plan = await self._require_plan(plan_id)
        task = await self._require_task(plan.task_id)

        if task.terminal:
            raise ValueError(
                f"cannot activate plan for terminal task: {task.task_id}"
            )

        if plan.state is PlanState.DRAFT:
            previous_plan_id = task.active_plan_id

            if (
                previous_plan_id is not None
                and previous_plan_id != plan.plan_id
            ):
                previous = await self._plan_store.get_plan(
                    previous_plan_id
                )
                if (
                    previous is not None
                    and previous.state is PlanState.ACTIVE
                ):
                    previous.transition(PlanState.SUPERSEDED)
                    await self._plan_store.save_plan(previous)

            plan.transition(PlanState.ACTIVE)
            await self._plan_store.save_plan(plan)

        elif plan.state is not PlanState.ACTIVE:
            raise ValueError(
                f"cannot activate plan in state: {plan.state}"
            )

        task.set_active_plan(plan.plan_id)

        if task.state is TaskState.PLANNING:
            task.transition(TaskState.EXECUTING)
        elif task.state is not TaskState.EXECUTING:
            raise ValueError(
                f"cannot execute plan for task in state: {task.state}"
            )

        await self._task_store.save(task)
        return plan

    async def add_step(
        self,
        *,
        plan_id: str,
        step_id: str,
        title: str,
        instruction: str,
        assigned_role: AgentRole = AgentRole.BACKEND_CODER,
    ) -> PlanStepRecord:
        plan = await self._require_plan(plan_id)

        if plan.terminal:
            raise ValueError(
                f"cannot add step to terminal plan: {plan_id}"
            )

        existing = await self._plan_store.get_step(step_id)
        if existing is not None:
            raise ValueError(f"step already exists: {step_id}")

        steps = await self._plan_store.list_steps(plan_id)
        step_index = (
            max((step.step_index for step in steps), default=-1)
            + 1
        )

        step = PlanStepRecord(
            step_id=step_id,
            plan_id=plan_id,
            step_index=step_index,
            title=title,
            instruction=instruction,
            assigned_role=assigned_role,
        )
        await self._plan_store.save_step(step)
        return step

    async def complete_plan(
        self,
        plan_id: str,
    ) -> PlanRecord:
        plan = await self._require_plan(plan_id)

        if plan.state is not PlanState.ACTIVE:
            raise ValueError(
                f"cannot complete plan in state: {plan.state}"
            )

        steps = await self._plan_store.list_steps(plan_id)

        unfinished = [
            step
            for step in steps
            if step.state
            not in {
                PlanStepState.COMPLETED,
                PlanStepState.SKIPPED,
            }
        ]

        if unfinished:
            raise ValueError(
                f"unfinished plan steps: {len(unfinished)}"
            )

        plan.transition(PlanState.COMPLETED)
        await self._plan_store.save_plan(plan)

        task = await self._require_task(plan.task_id)

        if task.active_plan_id == plan.plan_id:
            task.set_active_plan(None)
            await self._task_store.save(task)

        return plan

    async def start_step(
        self,
        step_id: str,
    ) -> PlanStepRecord:
        step = await self._require_step(step_id)
        plan = await self._require_plan(step.plan_id)

        if plan.state is not PlanState.ACTIVE:
            raise ValueError(
                f"cannot start step outside active plan: {step_id}"
            )

        task = await self._require_task(plan.task_id)

        if task.active_plan_id != plan.plan_id:
            raise ValueError(
                f"cannot start step outside active plan: {step_id}"
            )

        if task.state is not TaskState.EXECUTING:
            raise ValueError(
                f"cannot start step for task in state: {task.state}"
            )

        step.transition(PlanStepState.RUNNING)
        await self._plan_store.save_step(step)
        return step

    async def fail_step(
        self,
        step_id: str,
    ) -> PlanStepRecord:
        step = await self._require_step(step_id)
        step.transition(PlanStepState.FAILED)
        await self._plan_store.save_step(step)
        return step

    async def bind_step_execution_session(
        self,
        step_id: str,
        session_id: str,
    ) -> PlanStepRecord:
        if not session_id.strip():
            raise ValueError(
                "execution session_id must not be empty"
            )

        step = await self._require_step(step_id)

        if step.state is not PlanStepState.RUNNING:
            raise ValueError(
                "execution session can only be bound "
                "to a running plan step"
            )

        if (
            step.execution_session_id is not None
            and step.execution_session_id != session_id
        ):
            raise ValueError(
                "plan step already has a different "
                "execution session"
            )

        if step.execution_session_id == session_id:
            return step

        step.execution_session_id = session_id
        step.touch()
        await self._plan_store.save_step(step)
        return step

    async def complete_step(
        self,
        step_id: str,
    ) -> PlanStepRecord:
        step = await self._require_step(step_id)
        step.transition(PlanStepState.COMPLETED)
        await self._plan_store.save_step(step)
        return step

    async def _require_task(
        self,
        task_id: str,
    ) -> TaskRecord:
        task = await self._task_store.get(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        return task

    async def _require_plan(
        self,
        plan_id: str,
    ) -> PlanRecord:
        plan = await self._plan_store.get_plan(plan_id)
        if plan is None:
            raise KeyError(f"unknown plan: {plan_id}")
        return plan

    async def _require_step(
        self,
        step_id: str,
    ) -> PlanStepRecord:
        step = await self._plan_store.get_step(step_id)
        if step is None:
            raise KeyError(f"unknown plan step: {step_id}")
        return step
