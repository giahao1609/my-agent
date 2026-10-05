from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class MemoryLevel(StrEnum):
    L0 = "l0"
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    project_id: str
    level: MemoryLevel
    kind: str
    content: str
    importance: float = 0.5
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.memory_id.strip():
            raise ValueError("memory_id must not be empty")
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.kind.strip():
            raise ValueError("memory kind must not be empty")
        if not self.content.strip():
            raise ValueError("memory content must not be empty")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
