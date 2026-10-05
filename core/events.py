from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class EventType(StrEnum):
    TEXT = 'text'
    THINKING = 'thinking'
    TOOL_USE = 'tool_use'
    TOOL_RESULT = 'tool_result'
    LOG = 'log'
    ASK_USER = 'ask_user'
    TOOL_CONFIRM = 'tool_confirm'
    PHASE = 'phase'
    ARTIFACT = 'artifact'
    STOP = 'stop'


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: EventType
    session_id: str
    payload: Mapping[str, object] = field(default_factory=dict)
    agent_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError('session_id must not be empty')
