from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from core.memory import (
    MemoryLevel,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    normalize_memory_status,
    normalize_memory_tier,
    normalize_memory_type,
)


class SQLiteMemoryStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Phase 04: additive columns — safe for pre-existing databases.
            for col_def in (
                "status TEXT NOT NULL DEFAULT 'active'",
                "logical_key TEXT",
            ):
                col_name = col_def.split()[0]
                try:
                    connection.execute(
                        f"ALTER TABLE memories ADD COLUMN {col_def}"
                    )
                except sqlite3.OperationalError:
                    # Column already exists — idempotent.
                    pass
                _ = col_name  # suppress unused-variable warning
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_project
                ON memories(project_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_lookup
                ON memories(project_id, kind, importance)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_status
                ON memories(project_id, status, importance)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_logical_key
                ON memories(project_id, logical_key)
                """
            )

    async def add_many(
        self,
        records: tuple[MemoryRecord, ...],
    ) -> None:
        await asyncio.to_thread(self._add_many_sync, records)

    @staticmethod
    def _serialize_metadata(record: MemoryRecord) -> dict[str, object]:
        if "__canonical__" in record.metadata:
            raise ValueError(
                "metadata key '__canonical__' is reserved for internal store persistence"
            )
        meta = dict(record.metadata)
        meta["__canonical__"] = {
            "memory_type": record.memory_type.value,
            "tier": record.tier.value,
            "confidence": record.confidence,
            "subject": record.subject,
            "scope": record.scope,
            "source": record.source,
            "status": record.status.value,
            "supersedes": record.supersedes,
            "project_id": record.project_id,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            "last_accessed_at": record.last_accessed_at.isoformat() if record.last_accessed_at else None,
            "valid_from": record.valid_from.isoformat() if record.valid_from else None,
            "valid_until": record.valid_until.isoformat() if record.valid_until else None,
            "custom_kind": record._custom_kind,
        }
        return meta

    def _add_many_sync(
        self,
        records: tuple[MemoryRecord, ...],
    ) -> None:
        if not records:
            return

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO memories (
                    memory_id,
                    project_id,
                    level,
                    kind,
                    content,
                    importance,
                    metadata_json,
                    created_at,
                    status,
                    logical_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.memory_id,
                        record.project_id if record.project_id is not None else "",
                        record.level.value,
                        record.kind,
                        record.content,
                        record.importance,
                        json.dumps(
                            self._serialize_metadata(record),
                            ensure_ascii=False,
                        ),
                        record.created_at.isoformat(),
                        record.status.value,
                        record.metadata.get("logical_key"),
                    )
                    for record in records
                ],
            )

    async def search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        return await asyncio.to_thread(
            self._search_sync,
            project_id,
            query,
            limit,
        )

    def _search_sync(
        self,
        project_id: str,
        query: str,
        limit: int,
    ) -> tuple[MemoryRecord, ...]:
        if limit < 0:
            raise ValueError("memory search limit must be >= 0")
        if limit == 0:
            return ()

        pattern = f"%{query.strip()}%"

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memories
                WHERE project_id = ?
                  AND (
                    content LIKE ?
                    OR kind LIKE ?
                  )
                ORDER BY importance DESC, created_at DESC, rowid DESC
                LIMIT ?
                """,
                (
                    project_id,
                    pattern,
                    pattern,
                    limit,
                ),
            ).fetchall()

        return tuple(self._row_to_memory(row) for row in rows)

    # ------------------------------------------------------------------
    # Phase 04: Durable Memory Pipeline — new retrieval & lifecycle API
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
        """Filtered + scored retrieval from the durable store.

        Results are ordered by a lightweight relevance score:
            score = (term_match_bonus) + importance * 0.4 + confidence * 0.1

        No vector or embedding model is required.
        """
        return await asyncio.to_thread(
            self._retrieve_sync,
            project_id,
            query,
            limit,
            status_filter,
            tier_filter,
            memory_type_filter,
            min_importance,
        )

    def _retrieve_sync(
        self,
        project_id: str,
        query: str,
        limit: int,
        status_filter: MemoryStatus | None,
        tier_filter: MemoryLevel | None,
        memory_type_filter: MemoryType | None,
        min_importance: float,
    ) -> tuple[MemoryRecord, ...]:
        if limit < 0:
            raise ValueError("retrieve limit must be >= 0")
        if limit == 0:
            return ()

        conditions: list[str] = ["project_id = ?"]
        params: list[object] = [project_id]

        if status_filter is not None:
            conditions.append("status = ?")
            params.append(status_filter.value)

        if tier_filter is not None:
            conditions.append("level = ?")
            params.append(tier_filter.value)

        if min_importance > 0.0:
            conditions.append("importance >= ?")
            params.append(min_importance)

        where_clause = " AND ".join(conditions)

        # Fetch a slightly larger candidate set for re-ranking.
        fetch_limit = min(limit * 4, 200)
        params.append(fetch_limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM memories
                WHERE {where_clause}
                ORDER BY importance DESC, created_at DESC, rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        records = [self._row_to_memory(row) for row in rows]

        # Post-filter by memory_type (stored in __canonical__, not in a dedicated column).
        if memory_type_filter is not None:
            records = [r for r in records if r.memory_type == memory_type_filter]

        # Score and rank.
        query_lower = query.strip().lower()
        query_terms = set(query_lower.split()) if query_lower else set()

        def _score(rec: MemoryRecord) -> float:
            content_lower = rec.content.lower()
            # Full-phrase bonus.
            phrase_bonus = 0.5 if query_lower and query_lower in content_lower else 0.0
            # Per-term bonus (IDF-lite: uniform 0.1 per distinct term matched).
            term_bonus = (
                sum(0.1 for t in query_terms if t in content_lower)
                if query_terms
                else 0.0
            )
            return phrase_bonus + term_bonus + rec.importance * 0.4 + rec.confidence * 0.1

        scored = sorted(records, key=_score, reverse=True)
        return tuple(scored[:limit])

    async def supersede(
        self,
        old_memory_id: str,
        new_record: MemoryRecord,
    ) -> None:
        """Atomic superseding transition: marks old record SUPERSEDED and inserts new one.

        Both operations occur within a single SQLite transaction.
        The new_record.supersedes field MUST reference old_memory_id.
        """
        await asyncio.to_thread(self._supersede_sync, old_memory_id, new_record)

    def _supersede_sync(
        self,
        old_memory_id: str,
        new_record: MemoryRecord,
    ) -> None:
        if new_record.supersedes != old_memory_id:
            raise ValueError(
                f"new_record.supersedes must equal old_memory_id '{old_memory_id}', "
                f"got '{new_record.supersedes}'"
            )
        updated_at = datetime.now(UTC).isoformat()

        with self._connect() as connection:
            # Step 1: mark old record SUPERSEDED.
            connection.execute(
                """
                UPDATE memories
                SET status = ?, metadata_json = json_patch(
                    metadata_json,
                    json_object('__status_updated_at', ?)
                )
                WHERE memory_id = ?
                """,
                (MemoryStatus.SUPERSEDED.value, updated_at, old_memory_id),
            )
            # Step 2: insert new record.
            connection.execute(
                """
                INSERT INTO memories (
                    memory_id, project_id, level, kind, content,
                    importance, metadata_json, created_at, status, logical_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_record.memory_id,
                    new_record.project_id if new_record.project_id is not None else "",
                    new_record.level.value,
                    new_record.kind,
                    new_record.content,
                    new_record.importance,
                    json.dumps(
                        self._serialize_metadata(new_record),
                        ensure_ascii=False,
                    ),
                    new_record.created_at.isoformat(),
                    new_record.status.value,
                    new_record.metadata.get("logical_key"),
                ),
            )

    async def get_by_logical_key(
        self,
        project_id: str,
        logical_key: str,
        *,
        status_filter: MemoryStatus = MemoryStatus.ACTIVE,
    ) -> tuple[MemoryRecord, ...]:
        """Fetch records matching a logical_key, optionally filtered by status."""
        return await asyncio.to_thread(
            self._get_by_logical_key_sync, project_id, logical_key, status_filter
        )

    def _get_by_logical_key_sync(
        self,
        project_id: str,
        logical_key: str,
        status_filter: MemoryStatus,
    ) -> tuple[MemoryRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE project_id = ?
                  AND logical_key = ?
                  AND status = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (project_id, logical_key, status_filter.value),
            ).fetchall()
        return tuple(self._row_to_memory(row) for row in rows)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        raw_meta = json.loads(str(row["metadata_json"]))
        canonical = raw_meta.pop("__canonical__", None) if isinstance(raw_meta, dict) else None

        if canonical and isinstance(canonical, dict):
            # Canonical record reconstruction
            resolved_project_id = canonical.get("project_id")
            if resolved_project_id is None and row["project_id"]:
                resolved_project_id = str(row["project_id"])

            return MemoryRecord(
                memory_id=str(row["memory_id"]),
                content=str(row["content"]),
                project_id=resolved_project_id,
                memory_type=normalize_memory_type(str(canonical.get("memory_type", row["kind"]))),
                tier=normalize_memory_tier(str(canonical.get("tier", row["level"]))),
                importance=float(row["importance"]),
                confidence=float(canonical.get("confidence", 1.0)),
                subject=str(canonical.get("subject", "project")),
                scope=str(canonical.get("scope", "project")),
                source=str(canonical.get("source", "runtime")),
                status=normalize_memory_status(str(canonical.get("status", MemoryStatus.ACTIVE.value))),
                supersedes=canonical.get("supersedes"),
                metadata=raw_meta,
                created_at=datetime.fromisoformat(str(row["created_at"])),
                updated_at=datetime.fromisoformat(str(canonical["updated_at"])) if canonical.get("updated_at") else None,
                last_accessed_at=datetime.fromisoformat(str(canonical["last_accessed_at"])) if canonical.get("last_accessed_at") else None,
                valid_from=datetime.fromisoformat(str(canonical["valid_from"])) if canonical.get("valid_from") else None,
                valid_until=datetime.fromisoformat(str(canonical["valid_until"])) if canonical.get("valid_until") else None,
                _custom_kind=canonical.get("custom_kind") or str(row["kind"]),
            )

        # Legacy row reconstruction (pre-Phase-03 data):
        project_val = str(row["project_id"]) if row["project_id"] else None
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            project_id=project_val,
            level=MemoryLevel(str(row["level"])),
            kind=str(row["kind"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            metadata=raw_meta if isinstance(raw_meta, dict) else {},
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    async def _archive_record(
        self,
        project_id: str,
        memory_id: str,
        updated_at: str,
    ) -> None:
        """Mark a memory record as ARCHIVED (internal; called by DurableMemoryService)."""
        await asyncio.to_thread(
            self._archive_record_sync, project_id, memory_id, updated_at
        )

    def _archive_record_sync(
        self,
        project_id: str,
        memory_id: str,
        updated_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET status = ?, metadata_json = json_patch(
                    metadata_json,
                    json_object('__status_updated_at', ?)
                )
                WHERE memory_id = ? AND project_id = ?
                """,
                (MemoryStatus.ARCHIVED.value, updated_at, memory_id, project_id),
            )

