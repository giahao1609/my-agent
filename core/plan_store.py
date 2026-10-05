from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .plan import PlanRecord, PlanStepRecord


class PlanStore(Protocol):
    async def save_plan(self, plan: PlanRecord) -> None: ...

    async def save_plan_with_steps(
        self,
        plan: PlanRecord,
        steps: Sequence[PlanStepRecord],
    ) -> None: ...

    async def get_plan(
        self,
        plan_id: str,
    ) -> PlanRecord | None: ...

    async def list_for_task(
        self,
        task_id: str,
    ) -> Sequence[PlanRecord]: ...

    async def save_step(
        self,
        step: PlanStepRecord,
    ) -> None: ...

    async def get_step(
        self,
        step_id: str,
    ) -> PlanStepRecord | None: ...

    async def list_steps(
        self,
        plan_id: str,
    ) -> Sequence[PlanStepRecord]: ...
