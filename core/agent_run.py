from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from .agent_role import AgentRole
from .agent_work_result import AgentWorkResult


class AgentRunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_RUN_STATES = frozenset({
    AgentRunState.COMPLETED,
    AgentRunState.FAILED,
    AgentRunState.CANCELLED,
})


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    """Durable ledger record capturing who performed what work for which Task/PlanStep."""

    run_id: str
    project_id: str
    task_id: str
    agent_id: str
    execution_role: AgentRole
    plan_id: str | None = None
    step_id: str | None = None
    model_id: str | None = None
    runtime_id: str | None = None
    session_id: str | None = None
    state: AgentRunState = AgentRunState.CREATED
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str = field(default_factory=utc_iso_now)
    updated_at: str = field(default_factory=utc_iso_now)
    result: AgentWorkResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_RUN_STATES

    def mark_running(
        self,
        *,
        session_id: str | None = None,
        runtime_id: str | None = None,
        model_id: str | None = None,
    ) -> AgentRunRecord:
        if self.is_terminal:
            raise ValueError(f"cannot transition terminal run {self.run_id} ({self.state.value}) to running")
        now = utc_iso_now()
        return AgentRunRecord(
            run_id=self.run_id,
            project_id=self.project_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            execution_role=self.execution_role,
            plan_id=self.plan_id,
            step_id=self.step_id,
            model_id=model_id or self.model_id,
            runtime_id=runtime_id or self.runtime_id,
            session_id=session_id or self.session_id,
            state=AgentRunState.RUNNING,
            started_at=self.started_at or now,
            finished_at=None,
            created_at=self.created_at,
            updated_at=now,
            result=self.result,
            metadata=self.metadata,
        )

    def complete(self, result: AgentWorkResult) -> AgentRunRecord:
        if self.is_terminal:
            raise ValueError(f"cannot complete already terminal run {self.run_id} ({self.state.value})")
        now = utc_iso_now()
        return AgentRunRecord(
            run_id=self.run_id,
            project_id=self.project_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            execution_role=self.execution_role,
            plan_id=self.plan_id,
            step_id=self.step_id,
            model_id=self.model_id,
            runtime_id=self.runtime_id,
            session_id=self.session_id,
            state=AgentRunState.COMPLETED,
            started_at=self.started_at or now,
            finished_at=now,
            created_at=self.created_at,
            updated_at=now,
            result=result,
            metadata=self.metadata,
        )

    def fail(self, result: AgentWorkResult | None = None, reason: str = "") -> AgentRunRecord:
        if self.is_terminal:
            raise ValueError(f"cannot fail already terminal run {self.run_id} ({self.state.value})")
        now = utc_iso_now()
        meta = dict(self.metadata)
        if reason:
            meta["failure_reason"] = reason

        work_result = result
        if work_result is None:
            work_result = AgentWorkResult(
                run_id=self.run_id,
                status="failed",
                summary=reason or "Agent run failed",
            )

        return AgentRunRecord(
            run_id=self.run_id,
            project_id=self.project_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            execution_role=self.execution_role,
            plan_id=self.plan_id,
            step_id=self.step_id,
            model_id=self.model_id,
            runtime_id=self.runtime_id,
            session_id=self.session_id,
            state=AgentRunState.FAILED,
            started_at=self.started_at or now,
            finished_at=now,
            created_at=self.created_at,
            updated_at=now,
            result=work_result,
            metadata=meta,
        )

    def cancel(self, reason: str = "") -> AgentRunRecord:
        if self.is_terminal:
            raise ValueError(f"cannot cancel already terminal run {self.run_id} ({self.state.value})")
        now = utc_iso_now()
        meta = dict(self.metadata)
        if reason:
            meta["cancel_reason"] = reason

        return AgentRunRecord(
            run_id=self.run_id,
            project_id=self.project_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            execution_role=self.execution_role,
            plan_id=self.plan_id,
            step_id=self.step_id,
            model_id=self.model_id,
            runtime_id=self.runtime_id,
            session_id=self.session_id,
            state=AgentRunState.CANCELLED,
            started_at=self.started_at or now,
            finished_at=now,
            created_at=self.created_at,
            updated_at=now,
            result=self.result,
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "execution_role": self.execution_role.value if isinstance(self.execution_role, AgentRole) else str(self.execution_role),
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "model_id": self.model_id,
            "runtime_id": self.runtime_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result.to_dict() if self.result else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRunRecord:
        res = None
        if data.get("result"):
            res = AgentWorkResult.from_dict(data["result"])

        role_raw = data.get("execution_role", "backend_coder")
        try:
            role = AgentRole(role_raw)
        except ValueError:
            role = AgentRole.BACKEND_CODER

        return cls(
            run_id=data["run_id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            execution_role=role,
            plan_id=data.get("plan_id"),
            step_id=data.get("step_id"),
            model_id=data.get("model_id"),
            runtime_id=data.get("runtime_id"),
            session_id=data.get("session_id"),
            state=AgentRunState(data.get("state", "created")),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            created_at=data.get("created_at", utc_iso_now()),
            updated_at=data.get("updated_at", utc_iso_now()),
            result=res,
            metadata=data.get("metadata", {}),
        )


class AgentRunStore(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, run: AgentRunRecord) -> None: ...
    async def get(self, run_id: str) -> AgentRunRecord | None: ...
    async def list_for_task(self, task_id: str) -> tuple[AgentRunRecord, ...]: ...
    async def list_for_plan(self, plan_id: str) -> tuple[AgentRunRecord, ...]: ...
    async def list_for_step(self, step_id: str) -> tuple[AgentRunRecord, ...]: ...
    async def get_latest_for_step(self, step_id: str) -> AgentRunRecord | None: ...
    async def get_latest_completed_for_step(self, step_id: str) -> AgentRunRecord | None: ...
    async def list_recent_for_project(self, project_id: str, limit: int = 20) -> tuple[AgentRunRecord, ...]: ...
    async def list_for_session(self, session_id: str) -> tuple[AgentRunRecord, ...]: ...
