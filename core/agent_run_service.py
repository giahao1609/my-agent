from __future__ import annotations

import uuid
from typing import Any

from .agent_role import AgentRole
from .agent_run import AgentRunRecord, AgentRunState, AgentRunStore
from .agent_work_result import AgentWorkResult


class AgentRunService:
    """Deterministic service orchestrating AgentRun lifecycles and enforcing terminal state invariants."""

    def __init__(self, store: AgentRunStore) -> None:
        self._store = store

    async def create_run(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_id: str,
        execution_role: AgentRole,
        plan_id: str | None = None,
        step_id: str | None = None,
        model_id: str | None = None,
        runtime_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
        existing = await self._store.get(rid)
        if existing is not None:
            raise ValueError(f"agent run already exists: {rid}")

        run = AgentRunRecord(
            run_id=rid,
            project_id=project_id,
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            agent_id=agent_id,
            execution_role=execution_role,
            model_id=model_id,
            runtime_id=runtime_id,
            session_id=session_id,
            state=AgentRunState.CREATED,
            metadata=metadata or {},
        )
        await self._store.save(run)
        return run

    async def start_run(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        runtime_id: str | None = None,
        model_id: str | None = None,
    ) -> AgentRunRecord:
        run = await self._require_run(run_id)
        updated = run.mark_running(
            session_id=session_id,
            runtime_id=runtime_id,
            model_id=model_id,
        )
        await self._store.save(updated)
        return updated

    async def complete_run(
        self,
        run_id: str,
        result: AgentWorkResult,
    ) -> AgentRunRecord:
        run = await self._require_run(run_id)

        # Idempotency check: duplicate completion with identical or equivalent summary
        if run.state == AgentRunState.COMPLETED:
            if run.result is not None and run.result.summary == result.summary:
                return run
            raise ValueError(f"cannot complete already terminal completed run {run_id} with conflicting result")

        updated = run.complete(result)
        await self._store.save(updated)
        return updated

    async def fail_run(
        self,
        run_id: str,
        result: AgentWorkResult | None = None,
        reason: str = "",
    ) -> AgentRunRecord:
        run = await self._require_run(run_id)
        if run.state == AgentRunState.FAILED:
            return run

        updated = run.fail(result=result, reason=reason)
        await self._store.save(updated)
        return updated

    async def cancel_run(
        self,
        run_id: str,
        reason: str = "",
    ) -> AgentRunRecord:
        run = await self._require_run(run_id)
        if run.state == AgentRunState.CANCELLED:
            return run

        updated = run.cancel(reason=reason)
        await self._store.save(updated)
        return updated

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        return await self._store.get(run_id)

    async def list_runs_for_step(self, step_id: str) -> tuple[AgentRunRecord, ...]:
        return await self._store.list_for_step(step_id)

    async def list_runs_for_task(self, task_id: str) -> tuple[AgentRunRecord, ...]:
        return await self._store.list_for_task(task_id)

    async def list_runs_for_plan(self, plan_id: str) -> tuple[AgentRunRecord, ...]:
        return await self._store.list_for_plan(plan_id)

    async def get_latest_completed_run_for_step(self, step_id: str) -> AgentRunRecord | None:
        return await self._store.get_latest_completed_for_step(step_id)

    async def _require_run(self, run_id: str) -> AgentRunRecord:
        run = await self._store.get(run_id)
        if run is None:
            raise KeyError(f"agent run not found: {run_id}")
        return run
