"""Phase 04 — Durable Memory Pipeline.

DurableMemoryService is the ONE orchestration boundary for all durable
memory operations.  It owns:

    write()                  — persist one or more MemoryRecord objects,
                               with optional conflict detection via composite
                               conflict identity (subject + scope + memory_type
                               + logical_key) and atomic superseding of stale
                               records.

    retrieve()               — scored retrieval from the durable store with
                               status, tier, type, importance, and
                               include_global filters.  Returns MemoryMatch
                               objects containing score and deterministic
                               scoring reasons.

    archive()                — explicit lifecycle transition to ARCHIVED status.

    consolidate_and_persist() — convenience wrapper: calls MemoryConsolidator
                               (pure, side-effect-free) then writes the
                               resulting canonical MemoryRecord objects through
                               this service.

Design constraints honoured:
    * Consumes core.memory.MemoryRecord exclusively (Phase 03 canonical model).
    * SQLiteMemoryStore is the durable source of truth.
    * No vector database.  No background daemon.
    * No automatic extraction or consolidation (that is a caller concern).
    * Superseding is explicit: the caller must supply the logical_key.
    * A single SQLite transaction guards all supersede operations.
    * Conflict identity = (project_id, subject, scope, memory_type, logical_key).
      Two records with the same logical_key but different subject/scope/type
      are NOT treated as conflicts.
    * No UserModel, ContextBuilder, Reflection, vector embeddings, or any
      Phase 05+ concern.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Sequence

from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    are_canonically_equivalent,
)
from persistence.sqlite_memory_store import SQLiteMemoryStore

if TYPE_CHECKING:
    from core.context import ExecutionContext
    from core.memory_consolidation import MemoryConsolidator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GLOBAL_PROJECT_ID: str | None = None
"""Persistence identifier for global (cross-project) memories (project_id=None)."""


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryMatch:
    """Scored retrieval result wrapping a MemoryRecord.

    Attributes
    ----------
    record:  The canonical MemoryRecord.
    score:   Deterministic relevance score (>= 0.0).
    reasons: Tuple of deterministic scoring facts, e.g.
             ("exact_phrase", "token_overlap:3", "importance:0.90").
             Never exposes chain-of-thought.
    """

    record: MemoryRecord
    score: float
    reasons: tuple[str, ...]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.record, name)

    @property
    def memory_id(self) -> str:
        return self.record.memory_id

    @property
    def content(self) -> str:
        return self.record.content

    @property
    def project_id(self) -> str:
        return self.record.project_id

    @property
    def status(self) -> MemoryStatus:
        return self.record.status

    @property
    def importance(self) -> float:
        return self.record.importance

    @property
    def memory_type(self) -> MemoryType:
        return self.record.memory_type

    @property
    def tier(self) -> MemoryLevel:
        return self.record.tier

    @property
    def level(self) -> MemoryLevel:
        return self.record.level

    @property
    def subject(self) -> str:
        return self.record.subject

    @property
    def scope(self) -> str:
        return self.record.scope

    @property
    def confidence(self) -> float:
        return self.record.confidence

    @property
    def logical_key(self) -> str | None:
        return self.record.logical_key

    @property
    def supersedes(self) -> str | None:
        return self.record.supersedes


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


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

        Idempotency contract
        --------------------
        * Same memory_id + same content: idempotent — stored only once via
          INSERT OR IGNORE semantics.
        * Same memory_id + different content without logical_key: INSERT OR
          IGNORE silently ignores the duplicate.  Callers needing explicit
          replacement MUST use logical_key or call supersede() directly.

        Conflict resolution (opt-in, requires logical_key in metadata)
        ---------------------------------------------------------------
        Conflict identity = (project_id, subject, scope, memory_type, logical_key).

        Two records with the same logical_key but different subject, scope,
        or memory_type are NOT treated as conflicts.

        When resolve_conflicts is True and a record has a logical_key, the
        service checks for any ACTIVE record with the same composite identity.
        If found, the older record is atomically SUPERSEDED and a new record
        is created that references it via ``supersedes``.

        Returns a tuple of persisted memory_ids (new or superseding records).
        """
        if not records:
            return ()

        persisted_ids: list[str] = []

        for record in records:
            if record.project_id == "__global__":
                raise ValueError(
                    "RESERVED_SENTINEL: project_id='__global__' is a reserved legacy sentinel "
                    "and cannot be used for new durable writes. Use project_id=None with "
                    "scope='global' for canonical global memories."
                )

            # Idempotency check: verify if record with this memory_id already exists.
            existing_by_id = await self._store.get_by_id(record.memory_id)
            if existing_by_id is not None:
                if are_canonically_equivalent(existing_by_id, record):
                    # CASE A: same ID + identical canonical semantics -> idempotent success.
                    persisted_ids.append(record.memory_id)
                    continue
                # CASE B: same ID + different canonical semantics without explicit replacement.
                # Silent overwrite forbidden -> reject/conflict.
                raise ValueError(
                    f"Conflict: memory record '{record.memory_id}' already exists with different "
                    "canonical semantics. Silent overwrite is forbidden; explicit replacement/supersede required."
                )

            logical_key: str | None = None
            if isinstance(record.metadata.get("logical_key"), str):
                lk = record.metadata["logical_key"].strip()
                if lk:
                    logical_key = lk

            if resolve_conflicts and logical_key and record.project_id:
                # Composite conflict identity: subject + scope + type + logical_key.
                existing = await self._store.get_by_conflict_identity(
                    project_id=record.project_id,
                    logical_key=logical_key,
                    subject=record.subject,
                    scope=record.scope,
                    memory_type=record.memory_type,
                    status_filter=MemoryStatus.ACTIVE,
                )

                if existing:
                    target = existing[0]

                    if target.memory_id == record.memory_id:
                        # Same ID re-written: check equivalence.
                        if are_canonically_equivalent(target, record):
                            persisted_ids.append(record.memory_id)
                            continue
                        raise ValueError(
                            f"Conflict: memory record '{record.memory_id}' already exists with different "
                            "canonical semantics. Silent overwrite is forbidden; explicit replacement/supersede required."
                        )

                    # Build superseding record referencing the old one.
                    superseding = _rebuild_with_supersedes(record, target.memory_id)
                    await self._store.supersede(target.memory_id, superseding)
                    persisted_ids.append(superseding.memory_id)
                    continue

            # No conflict path: plain write.
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
        include_global: bool = False,
    ) -> tuple[MemoryMatch, ...]:
        """Retrieve memory records from the durable store.

        Returns MemoryMatch objects (record + score + reasons) ranked by
        relevance score.

        Parameters
        ----------
        project_id:      Required.  Only this project's memories are returned
                         (plus global memories if include_global=True).
                         Project B's memories are NEVER returned for project A.
        query:           Lexical query.  When non-empty, records with zero
                         lexical relevance are excluded (fail-closed policy).
                         Pass "" for browse/filter mode (all candidates returned).
        include_global:  If True, eligible global memories (project_id ==
                         GLOBAL_PROJECT_ID) are merged and re-ranked together.
        """
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required for memory retrieval")

        fetch_limit = limit * 2 if include_global else limit

        project_matches = await self._store.retrieve_scored(
            project_id,
            query,
            limit=fetch_limit,
            status_filter=status_filter,
            tier_filter=tier_filter,
            memory_type_filter=memory_type_filter,
            min_importance=min_importance,
        )

        if include_global:
            global_matches = await self._store.retrieve_scored(
                None,
                query,
                limit=limit,
                status_filter=status_filter,
                tier_filter=tier_filter,
                memory_type_filter=memory_type_filter,
                min_importance=min_importance,
            )
            tagged_global = [
                MemoryMatch(
                    record=m.record,
                    score=m.score,
                    reasons=m.reasons + ("global_scope",),
                )
                for m in global_matches
            ]
            tagged_project = [
                MemoryMatch(
                    record=m.record,
                    score=m.score,
                    reasons=m.reasons + ("project_scope",),
                )
                for m in project_matches
            ]
            combined = sorted(
                tagged_project + tagged_global,
                key=lambda m: m.score,
                reverse=True,
            )
            return tuple(combined[:limit])

        return tuple(
            MemoryMatch(
                record=m.record,
                score=m.score,
                reasons=m.reasons + ("project_scope",),
            )
            for m in project_matches[:limit]
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

    # ------------------------------------------------------------------
    # Consolidate and persist (explicit durable path)
    # ------------------------------------------------------------------

    async def consolidate_and_persist(
        self,
        consolidator: MemoryConsolidator,
        context: ExecutionContext,
        session_messages: Sequence[dict[str, str]],
        *,
        resolve_conflicts: bool = True,
    ) -> tuple[str, ...]:
        """Consolidate session messages and durably persist the results.

        This is the ONLY explicit path that bridges MemoryConsolidator
        (pure, side-effect-free) to DurableMemoryService (persistent).

        Pure consolidation (consolidate_session_records) is called first and
        produces canonical MemoryRecord objects WITHOUT any side effects.
        Those records are then written through this service into SQLite.

        Callers must provide an ExecutionContext with a non-empty project_id
        so that records are correctly scoped.

        Returns the memory_ids of all durably persisted records.
        """
        canonical_records = consolidator.consolidate_session_records(
            context, session_messages
        )
        if not canonical_records:
            return ()
        return await self.write(canonical_records, resolve_conflicts=resolve_conflicts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rebuild_with_supersedes(
    record: MemoryRecord,
    old_memory_id: str,
) -> MemoryRecord:
    """Return a new MemoryRecord identical to record but with supersedes set."""
    if record.supersedes == old_memory_id:
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
