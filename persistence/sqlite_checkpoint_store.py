from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.checkpoint import CheckpointRecord


class SQLiteCheckpointStore:
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
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_id TEXT,
                    summary TEXT NOT NULL,
                    next_action TEXT,
                    conversation_id TEXT,
                    session_id TEXT,
                    files_changed_json TEXT NOT NULL,
                    tests_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(checkpoints)"
                ).fetchall()
            }

            # Backward-compatible migration for databases created before
            # durable task-scoped checkpoints existed.
            if "task_id" not in columns:
                connection.execute(
                    "ALTER TABLE checkpoints ADD COLUMN task_id TEXT"
                )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_checkpoints_project
                ON checkpoints(project_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_checkpoints_project_task
                ON checkpoints(project_id, task_id, created_at DESC)
                """
            )

    async def save(self, checkpoint: CheckpointRecord) -> None:
        await asyncio.to_thread(self._save_sync, checkpoint)

    def _save_sync(self, checkpoint: CheckpointRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints (
                    checkpoint_id,
                    project_id,
                    task_id,
                    summary,
                    next_action,
                    conversation_id,
                    session_id,
                    files_changed_json,
                    tests_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.project_id,
                    checkpoint.task_id,
                    checkpoint.summary,
                    checkpoint.next_action,
                    checkpoint.conversation_id,
                    checkpoint.session_id,
                    json.dumps(
                        checkpoint.files_changed,
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        checkpoint.tests,
                        ensure_ascii=False,
                    ),
                    checkpoint.created_at.isoformat(),
                ),
            )

    async def latest(
        self,
        project_id: str,
        task_id: str | None = None,
    ) -> CheckpointRecord | None:
        return await asyncio.to_thread(
            self._latest_sync,
            project_id,
            task_id,
        )

    def _latest_sync(
        self,
        project_id: str,
        task_id: str | None,
    ) -> CheckpointRecord | None:
        with self._connect() as connection:
            if task_id is None:
                row = connection.execute(
                    """
                    SELECT *
                    FROM checkpoints
                    WHERE project_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT *
                    FROM checkpoints
                    WHERE project_id = ?
                      AND task_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (project_id, task_id),
                ).fetchone()

        return self._row_to_checkpoint(row) if row is not None else None

    async def list_for_project(
        self,
        project_id: str,
        task_id: str | None = None,
    ) -> tuple[CheckpointRecord, ...]:
        return await asyncio.to_thread(
            self._list_for_project_sync,
            project_id,
            task_id,
        )

    def _list_for_project_sync(
        self,
        project_id: str,
        task_id: str | None,
    ) -> tuple[CheckpointRecord, ...]:
        with self._connect() as connection:
            if task_id is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM checkpoints
                    WHERE project_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM checkpoints
                    WHERE project_id = ?
                      AND task_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    """,
                    (project_id, task_id),
                ).fetchall()

        return tuple(
            self._row_to_checkpoint(row)
            for row in rows
        )

    async def delete_for_project(self, project_id: str) -> int:
        return await asyncio.to_thread(
            self._delete_for_project_sync,
            project_id,
        )

    def _delete_for_project_sync(self, project_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM checkpoints WHERE project_id = ?",
                (project_id,),
            )

        return cursor.rowcount

    @staticmethod
    def _row_to_checkpoint(row: sqlite3.Row) -> CheckpointRecord:
        return CheckpointRecord(
            checkpoint_id=str(row["checkpoint_id"]),
            project_id=str(row["project_id"]),
            task_id=row["task_id"],
            summary=str(row["summary"]),
            next_action=row["next_action"],
            conversation_id=row["conversation_id"],
            session_id=row["session_id"],
            files_changed=tuple(
                json.loads(str(row["files_changed_json"]))
            ),
            tests=tuple(
                json.loads(str(row["tests_json"]))
            ),
            created_at=datetime.fromisoformat(
                str(row["created_at"])
            ),
        )
