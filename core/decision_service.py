from __future__ import annotations

import uuid
from typing import Sequence

from .decision import (
    DecisionOption,
    DecisionPolicy,
    DecisionRecord,
    DecisionSeverity,
    DecisionState,
    DecisionStore,
)


class DecisionService:
    def __init__(self, decision_store: DecisionStore) -> None:
        self._store = decision_store

    async def create_decision(
        self,
        *,
        task_id: str,
        prompt: str,
        severity: DecisionSeverity,
        options: Sequence[DecisionOption],
        step_id: str | None = None,
        plan_id: str | None = None,
        session_id: str | None = None,
        decision_id: str | None = None,
    ) -> DecisionRecord:
        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        options_tuple = tuple(options)
        DecisionPolicy.validate_creation(severity=severity, options=options_tuple)

        rec = DecisionRecord(
            decision_id=decision_id or f"dec-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            step_id=step_id,
            severity=severity,
            state=DecisionState.OPEN,
            prompt=prompt,
            options=options_tuple,
            plan_id=plan_id,
            session_id=session_id,
            expires_at=DecisionPolicy.compute_expires_at(severity),
        )
        await self._store.save(rec)
        return rec

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        return await self._store.get(decision_id)

    async def resolve_decision(
        self,
        decision_id: str,
        *,
        selected_option_id: str,
        rationale: str | None = None,
    ) -> DecisionRecord:
        decision = await self._store.get(decision_id)
        if decision is None:
            raise KeyError(f"decision not found: {decision_id}")

        resolved = decision.resolve(selected_option_id=selected_option_id, rationale=rationale)
        await self._store.save(resolved)
        return resolved

    async def cancel_decision(
        self,
        decision_id: str,
        *,
        rationale: str | None = None,
    ) -> DecisionRecord:
        decision = await self._store.get(decision_id)
        if decision is None:
            raise KeyError(f"decision not found: {decision_id}")

        cancelled = decision.cancel(rationale=rationale)
        await self._store.save(cancelled)
        return cancelled

    async def expire_stale_decisions(self) -> list[DecisionRecord]:
        """Finds all OPEN decisions that have passed their expiry deadline and
        transitions them to EXPIRED.  HIGH-severity decisions never auto-expire.

        Returns the list of newly-expired DecisionRecords.
        """
        stale = await self._store.list_expired_open()
        expired: list[DecisionRecord] = []
        for rec in stale:
            if rec.is_expired():
                exp = rec.expire()
                await self._store.save(exp)
                expired.append(exp)
        return expired

    async def list_open_decisions(self, task_id: str | None = None) -> tuple[DecisionRecord, ...]:
        return await self._store.list_open(task_id=task_id)

    async def list_decisions_by_task(self, task_id: str) -> tuple[DecisionRecord, ...]:
        return await self._store.list_by_task(task_id)
