from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.memory import MemoryLevel, MemoryRecord


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
                        record.project_id,
                        record.level.value,
                        record.kind,
                        record.content,
                        record.importance,
                        json.dumps(
                            dict(record.metadata),
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
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            project_id=str(row["project_id"]),
            level=MemoryLevel(str(row["level"])),
            kind=str(row["kind"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            metadata=json.loads(str(row["metadata_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
