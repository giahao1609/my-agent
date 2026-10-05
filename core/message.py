from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(StrEnum):
    USER = 'user'
    ASSISTANT = 'assistant'
    SYSTEM = 'system'
    TOOL = 'tool'


@dataclass(slots=True)
class MessageRecord:
    message_id: str
    project_id: str
    conversation_id: str
    role: MessageRole
    content: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.message_id.strip():
            raise ValueError('message_id must not be empty')
        if not self.project_id.strip():
            raise ValueError('project_id must not be empty')
        if not self.conversation_id.strip():
            raise ValueError('conversation_id must not be empty')
        if not self.content.strip():
            raise ValueError('message content must not be empty')
