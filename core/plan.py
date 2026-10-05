from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


from .agent_role import AgentRole


def utc_now() -> datetime:
    return datetime.now(UTC)


class PlanState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


_PLAN_TRANSITIONS: dict[PlanState, frozenset[PlanState]] = {
    PlanState.DRAFT: frozenset(
        {
            PlanState.ACTIVE,
            PlanState.SUPERSEDED,
        }
    ),
    PlanState.ACTIVE: frozenset(
        {
            PlanState.COMPLETED,
            PlanState.FAILED,
            PlanState.SUPERSEDED,
        }
    ),
    PlanState.COMPLETED: frozenset(),
    PlanState.FAILED: frozenset(),
    PlanState.SUPERSEDED: frozenset(),
}


class PlanStepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_STEP_TRANSITIONS: dict[PlanStepState, frozenset[PlanStepState]] = {
    PlanStepState.PENDING: frozenset(
        {
            PlanStepState.RUNNING,
            PlanStepState.SKIPPED,
        }
    ),
    PlanStepState.RUNNING: frozenset(
        {
            PlanStepState.COMPLETED,
            PlanStepState.FAILED,
        }
    ),
    PlanStepState.COMPLETED: frozenset(),
    PlanStepState.FAILED: frozenset(),
    PlanStepState.SKIPPED: frozenset(),
}


@dataclass(slots=True)
class PlanRecord:
    plan_id: str
    task_id: str
    revision: int = 1
    state: PlanState = PlanState.DRAFT
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("plan_id must not be empty")
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if self.revision < 1:
            raise ValueError("plan revision must be >= 1")

    def touch(self) -> None:
        self.updated_at = utc_now()

    def transition(self, new_state: PlanState) -> None:
        if new_state is self.state:
            return

        if new_state not in _PLAN_TRANSITIONS[self.state]:
            raise ValueError(
                f"invalid plan transition: {self.state} -> {new_state}"
            )

        self.state = new_state
        self.touch()

    @property
    def terminal(self) -> bool:
        return self.state in {
            PlanState.COMPLETED,
            PlanState.FAILED,
            PlanState.SUPERSEDED,
        }


@dataclass(slots=True)
class PlanStepRecord:
    step_id: str
    plan_id: str
    step_index: int
    title: str
    instruction: str
    state: PlanStepState = PlanStepState.PENDING
    execution_session_id: str | None = None
    assigned_role: AgentRole = AgentRole.BACKEND_CODER
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.step_id.strip():
            raise ValueError("step_id must not be empty")
        if not self.plan_id.strip():
            raise ValueError("plan_id must not be empty")
        if self.step_index < 0:
            raise ValueError("step_index must be >= 0")
        if not self.title.strip():
            raise ValueError("step title must not be empty")
        if not self.instruction.strip():
            raise ValueError("step instruction must not be empty")

    def touch(self) -> None:
        self.updated_at = utc_now()

    def transition(self, new_state: PlanStepState) -> None:
        if new_state is self.state:
            return

        if new_state not in _STEP_TRANSITIONS[self.state]:
            raise ValueError(
                f"invalid plan step transition: {self.state} -> {new_state}"
            )

        self.state = new_state
        self.touch()

    @property
    def terminal(self) -> bool:
        return self.state in {
            PlanStepState.COMPLETED,
            PlanStepState.FAILED,
            PlanStepState.SKIPPED,
        }
