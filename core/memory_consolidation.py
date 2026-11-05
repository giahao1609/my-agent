from __future__ import annotations

import uuid
from collections.abc import Sequence

from core.context import ExecutionContext
from core.memory import (
    LegacyMemoryLevel,
    MemoryEntry,
    MemoryRecord,
    legacy_entry_to_memory_record,
    memory_record_to_legacy_entry,
    utc_now_iso,
)

MemoryLevel = LegacyMemoryLevel

__all__ = [
    "LegacyMemoryLevel",
    "MemoryConsolidator",
    "MemoryEntry",
    "MemoryLevel",
    "legacy_entry_to_memory_record",
    "memory_record_to_legacy_entry",
    "utc_now_iso",
]


class MemoryConsolidator:
    """Manages multi-tier memory consolidation (L0 -> L1 -> L2 -> L3).
    
    NOTE: Operates purely in-memory. Does NOT automatically persist records.
    Durable persistence pipeline wiring is deferred to Phase 04.
    """

    def consolidate_session(
        self,
        context: ExecutionContext,
        session_messages: Sequence[dict[str, str]],
    ) -> list[MemoryEntry]:
        """Summarizes short-term L0 session messages into L1 working memories (legacy format)."""
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

    def consolidate_session_records(
        self,
        context: ExecutionContext,
        session_messages: Sequence[dict[str, str]],
    ) -> list[MemoryRecord]:
        """Summarizes short-term L0 session messages into canonical MemoryRecord objects.
        
        Pure in-memory operation. Does NOT write to persistence.
        """
        entries = self.consolidate_session(context, session_messages)
        return [entry.to_canonical(project_id=context.project_id) for entry in entries]

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

