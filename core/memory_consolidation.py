from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Sequence

from .context import ExecutionContext


class MemoryLevel(StrEnum):
    L0_EPISODIC = "L0_episodic"      # Short-term session chat memory
    L1_WORKING = "L1_working"        # Task/plan-scoped active context
    L2_SEMANTIC = "L2_semantic"      # Project rules, conventions, and architectural patterns
    L3_LONG_TERM = "L3_long_term"    # System-wide durable learned knowledge


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class MemoryEntry:
    memory_id: str
    level: MemoryLevel
    key: str
    content: str
    source: str
    recall_count: int = 0
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.recall_count += 1
        self.updated_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "level": self.level.value,
            "key": self.key,
            "content": self.content,
            "source": self.source,
            "recall_count": self.recall_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        return cls(
            memory_id=data["memory_id"],
            level=MemoryLevel(data["level"]),
            key=data["key"],
            content=data["content"],
            source=data.get("source", "system"),
            recall_count=data.get("recall_count", 0),
            created_at=data.get("created_at", utc_now_iso()),
            updated_at=data.get("updated_at", utc_now_iso()),
        )


class MemoryConsolidator:
    """Manages multi-tier memory consolidation (L0 -> L1 -> L2 -> L3)."""

    def consolidate_session(
        self,
        context: ExecutionContext,
        session_messages: Sequence[dict[str, str]],
    ) -> list[MemoryEntry]:
        """Summarizes short-term L0 session messages into L1 working memories."""
        consolidated: list[MemoryEntry] = []
        user_requests = [m["content"] for m in session_messages if m.get("role") == "user"]

        if user_requests:
            combined_summary = " | ".join(user_requests[:5])
            mem = MemoryEntry(
                memory_id=f"mem-{uuid.uuid4().hex[:12]}",
                level=MemoryLevel.L1_WORKING,
                key=f"working_task_{context.task_id or 'default'}",
                content=f"Task objective summary: {combined_summary[:300]}",
                source=f"session:{context.session_id or 'default'}",
            )
            consolidated.append(mem)

        return consolidated

    def promote_memories(
        self,
        memories: Sequence[MemoryEntry],
        min_recall_for_promotion: int = 3,
    ) -> list[MemoryEntry]:
        """Promotes frequently recalled memories to higher tiers (L1 -> L2 -> L3)."""
        promoted: list[MemoryEntry] = []

        for mem in memories:
            if mem.recall_count >= min_recall_for_promotion:
                if mem.level == MemoryLevel.L0_EPISODIC:
                    mem.level = MemoryLevel.L1_WORKING
                    mem.touch()
                    promoted.append(mem)
                elif mem.level == MemoryLevel.L1_WORKING:
                    mem.level = MemoryLevel.L2_SEMANTIC
                    mem.touch()
                    promoted.append(mem)
                elif mem.level == MemoryLevel.L2_SEMANTIC:
                    mem.level = MemoryLevel.L3_LONG_TERM
                    mem.touch()
                    promoted.append(mem)

        return promoted
