"""Phase 04 — Durable Memory Pipeline.

DurableMemoryService is the ONE orchestration boundary for all durable
memory operations.  It owns:

    write()              — persist one or more MemoryRecord objects, with
                           optional conflict detection via logical_key and
                           atomic superseding of stale records.

    retrieve()           — scored retrieval from the durable store with
                           status, tier, type, and importance filters.

    archive()            — explicit lifecycle transition to ARCHIVED status.

Design constraints honoured:
    * Consumes core.memory.MemoryRecord exclusively (Phase 03 canonical model).
    * SQLiteMemoryStore is the durable source of truth.
    * No vector database.  No background daemon.
    * No automatic extraction or consolidation (that is a caller concern).
    * Superseding is explicit: the caller must supply the logical_key.
    * A single SQLite transaction guards all supersede operations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Sequence

from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
)
from persistence.sqlite_memory_store import SQLiteMemoryStore


class DurableMemoryService:
    """Orchestrates durable memory write and retrieval.

    One service instance per application; the underlying SQLiteMemoryStore
    MUST already be initialised before any method is called.
    """

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write(
        self,
        records: Sequence[MemoryRecord],
        *,
        resolve_conflicts: bool = True,
    ) -> tuple[str, ...]:
        """Persist records to the durable store.

        When resolve_conflicts is True (the default) and a record carries a
        ``logical_key`` in its metadata, the service checks for any ACTIVE
        record with the same (project_id, logical_key).  If found, the older
        record is atomically SUPERSEDED and a new record is created that
        references it via ``supersedes``.

        Returns a tuple of persisted memory_ids (new or superseding records).
        """
        if not records:
            return ()

        persisted_ids: list[str] = []

        for record in records:
            logical_key: str | None = None
            if isinstance(record.metadata.get("logical_key"), str):
                lk = record.metadata["logical_key"].strip()
                if lk:
                    logical_key = lk

            if resolve_conflicts and logical_key and record.project_id:
                # Detect existing ACTIVE records with the same logical_key.
                existing = await self._store.get_by_logical_key(
                    record.project_id,
                    logical_key,
                    status_filter=MemoryStatus.ACTIVE,
                )

                if existing:
                    # Take the most recent one as the target to supersede.
                    target = existing[0]

                    if target.memory_id == record.memory_id:
                        # Same record being re-written; skip superseding.
                        await self._store.add_many((record,))
                        persisted_ids.append(record.memory_id)
                        continue

                    # Build the superseding record referencing the old one.
                    superseding = _rebuild_with_supersedes(record, target.memory_id)
                    await self._store.supersede(target.memory_id, superseding)
                    persisted_ids.append(superseding.memory_id)
                    continue

            # No conflict — plain write.
            await self._store.add_many((record,))
            persisted_ids.append(record.memory_id)

        return tuple(persisted_ids)

    # ------------------------------------------------------------------
    # Retrieve
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 10,
        status_filter: MemoryStatus | None = MemoryStatus.ACTIVE,
        tier_filter: MemoryLevel | None = None,
        memory_type_filter: MemoryType | None = None,
        min_importance: float = 0.0,
    ) -> tuple[MemoryRecord, ...]:
        """Retrieve memory records from the durable store.

        Delegates to SQLiteMemoryStore.retrieve() for filtered + scored
        retrieval.  See that method for scoring details.
        """
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required for memory retrieval")
        return await self._store.retrieve(
            project_id,
            query,
            limit=limit,
            status_filter=status_filter,
            tier_filter=tier_filter,
            memory_type_filter=memory_type_filter,
            min_importance=min_importance,
        )

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    async def archive(
        self,
        project_id: str,
        memory_id: str,
    ) -> None:
        """Explicitly transition a memory record to ARCHIVED status.

        ARCHIVED records are preserved for audit but excluded from active
        retrieval (status_filter=ACTIVE is the default in retrieve()).
        """
        updated_at = datetime.now(UTC).isoformat()
        await self._store._archive_record(project_id, memory_id, updated_at)  # noqa: SLF001


def _rebuild_with_supersedes(
    record: MemoryRecord,
    old_memory_id: str,
) -> MemoryRecord:
    """Return a new MemoryRecord identical to record but with supersedes set."""
    if record.supersedes == old_memory_id:
        # Already correctly referencing the old record.
        return record

    new_id = f"mem-{uuid.uuid4().hex}"
    meta = dict(record.metadata)

    return MemoryRecord(
        memory_id=new_id,
        content=record.content,
        project_id=record.project_id,
        memory_type=record.memory_type,
        tier=record.tier,
        importance=record.importance,
        confidence=record.confidence,
        subject=record.subject,
        scope=record.scope,
        source=record.source,
        status=record.status,
        supersedes=old_memory_id,
        metadata=meta,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_accessed_at=record.last_accessed_at,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
    )
