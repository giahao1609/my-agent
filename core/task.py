from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskState(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset(
        {
            TaskState.PLANNING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PLANNING: frozenset(
        {
            TaskState.EXECUTING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.EXECUTING: frozenset(
        {
            TaskState.PLANNING,
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    project_id: str
    objective: str
    state: TaskState = TaskState.CREATED
    active_plan_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.objective.strip():
            raise ValueError("task objective must not be empty")
        if self.active_plan_id is not None and not self.active_plan_id.strip():
            raise ValueError("active_plan_id must not be empty")

    def touch(self) -> None:
        self.updated_at = utc_now()

    def set_active_plan(self, plan_id: str | None) -> None:
        if plan_id is not None and not plan_id.strip():
            raise ValueError("plan_id must not be empty")
        self.active_plan_id = plan_id
        self.touch()

    def transition(self, new_state: TaskState) -> None:
        if new_state is self.state:
            return

        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(
                f"invalid task transition: {self.state} -> {new_state}"
            )

        self.state = new_state
        self.touch()

    @property
    def terminal(self) -> bool:
        return self.state in {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
