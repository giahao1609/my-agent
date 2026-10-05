from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class CheckpointRecord:
    checkpoint_id: str
    project_id: str
    summary: str
    next_action: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    files_changed: tuple[str, ...] = field(default_factory=tuple)
    tests: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.checkpoint_id.strip():
            raise ValueError("checkpoint_id must not be empty")
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.summary.strip():
            raise ValueError("checkpoint summary must not be empty")
        if self.task_id is not None and not self.task_id.strip():
            raise ValueError("task_id must not be empty")
