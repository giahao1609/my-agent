from __future__ import annotations

import pytest

from core.context import ExecutionContext
from core.memory_consolidation import MemoryConsolidator, MemoryEntry, MemoryLevel


def test_memory_entry_serialization() -> None:
    entry = MemoryEntry(
        memory_id="mem-1",
        level=MemoryLevel.L1_WORKING,
        key="test_key",
        content="Test content",
        source="test_source",
        recall_count=2,
    )
    data = entry.to_dict()
    assert data["memory_id"] == "mem-1"
    assert data["level"] == "L1_working"

    restored = MemoryEntry.from_dict(data)
    assert restored.memory_id == entry.memory_id
    assert restored.level == MemoryLevel.L1_WORKING


def test_memory_consolidator_session_consolidation() -> None:
    consolidator = MemoryConsolidator()
    context = ExecutionContext(project_id="proj-1", workspace_id="ws-1", task_id="task-100", session_id="sess-100")
    messages = [
        {"role": "user", "content": "Build payment system"},
        {"role": "assistant", "content": "I will create plan"},
    ]

    memories = consolidator.consolidate_session(context, messages)
    assert len(memories) == 1
    assert memories[0].level == MemoryLevel.L1_WORKING
    assert "Build payment system" in memories[0].content


def test_memory_promotion() -> None:
    consolidator = MemoryConsolidator()
    mem = MemoryEntry(
        memory_id="mem-1",
        level=MemoryLevel.L1_WORKING,
        key="key1",
        content="Frequent memory",
        source="test",
        recall_count=3,
    )

    promoted = consolidator.promote_memories([mem], min_recall_for_promotion=3)
    assert len(promoted) == 1
    assert promoted[0].level == MemoryLevel.L2_SEMANTIC
