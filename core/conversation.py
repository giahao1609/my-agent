from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ConversationRecord:
    conversation_id: str
    project_id: str
    title: str | None = None
    summary: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.conversation_id.strip():
            raise ValueError('conversation_id must not be empty')
        if not self.project_id.strip():
            raise ValueError('project_id must not be empty')

    def touch(self) -> None:
        self.updated_at = utc_now()
