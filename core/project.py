from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ProjectRecord:
    project_id: str
    name: str
    workspace_path: str
    current_phase: str | None = None
    active_task_id: str | None = None
    last_checkpoint_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError('project_id must not be empty')
        if not self.name.strip():
            raise ValueError('project name must not be empty')
        if not self.workspace_path.strip():
            raise ValueError('workspace_path must not be empty')

    def touch(self) -> None:
        self.updated_at = utc_now()
