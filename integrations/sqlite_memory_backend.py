from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4

from core.context import ExecutionContext
from core.memory import MemoryLevel, MemoryRecord
from core.status import Availability, CapabilityStatus
from persistence.sqlite_memory_store import SQLiteMemoryStore


def _memory_payload(record: MemoryRecord) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "project_id": record.project_id,
        "level": record.level.value,
        "kind": record.kind,
        "content": record.content,
        "importance": record.importance,
        "metadata": dict(record.metadata),
        "created_at": record.created_at.isoformat(),
    }


class SQLiteMemoryBackend:
    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store

    @staticmethod
    def _project_id(context: ExecutionContext) -> str:
        if context.project_id is None or not context.project_id.strip():
            raise ValueError("project_id is required for memory")
        return context.project_id

    async def recall(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[Mapping[str, object], ...]:
        project_id = self._project_id(context)
        records = await self._store.search(
            project_id,
            query,
            limit=limit,
        )
        return tuple(_memory_payload(record) for record in records)

    async def capture(
        self,
        context: ExecutionContext,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        project_id = self._project_id(context)
        memories: list[MemoryRecord] = []

        for item in records:
            kind = item.get("kind")
            content = item.get("content")

            if not isinstance(kind, str) or not kind.strip():
                raise ValueError("memory kind must be a non-empty string")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("memory content must be a non-empty string")

            importance_value = item.get("importance", 0.5)
            if not isinstance(importance_value, int | float):
                raise TypeError("memory importance must be a number")

            level_value = item.get("level", MemoryLevel.L1.value)
            try:
                level = MemoryLevel(str(level_value))
            except ValueError as exc:
                raise ValueError(
                    f"invalid memory level: {level_value}"
                ) from exc

            metadata = item.get("metadata", {})
            if not isinstance(metadata, Mapping):
                raise TypeError("memory metadata must be a mapping")

            memory_id_value = item.get("memory_id")
            memory_id = (
                memory_id_value
                if isinstance(memory_id_value, str)
                and memory_id_value.strip()
                else f"mem-{uuid4().hex}"
            )

            memories.append(
                MemoryRecord(
                    memory_id=memory_id,
                    project_id=project_id,
                    level=level,
                    kind=kind,
                    content=content,
                    importance=float(importance_value),
                    metadata=dict(metadata),
                )
            )

        await self._store.add_many(tuple(memories))

    async def capabilities(self) -> tuple[CapabilityStatus, ...]:
        return (
            CapabilityStatus(
                name="sqlite_memory",
                state=Availability.READY,
            ),
        )
