from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

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

    async def add_many(
        self,
        records: tuple[MemoryRecord, ...],
    ) -> None:
        await asyncio.to_thread(self._add_many_sync, records)

    @staticmethod
    def _serialize_metadata(record: MemoryRecord) -> dict[str, object]:
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
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

