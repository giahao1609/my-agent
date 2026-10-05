from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from core.task import TaskRecord, TaskState


class SQLiteTaskStore:
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
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    state TEXT NOT NULL,
                    active_plan_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_project
                ON tasks(project_id, updated_at DESC)
                """
            )

    async def save(self, task: TaskRecord) -> None:
        await asyncio.to_thread(self._save_sync, task)

    def _save_sync(self, task: TaskRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id,
                    project_id,
                    objective,
                    state,
                    active_plan_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    objective = excluded.objective,
                    state = excluded.state,
                    active_plan_id = excluded.active_plan_id,
                    updated_at = excluded.updated_at
                """,
                (
                    task.task_id,
                    task.project_id,
                    task.objective,
                    task.state.value,
                    task.active_plan_id,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    async def get(self, task_id: str) -> TaskRecord | None:
        return await asyncio.to_thread(self._get_sync, task_id)

    def _get_sync(self, task_id: str) -> TaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM tasks
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()

        return self._row_to_task(row) if row is not None else None

    async def list_for_project(
        self,
        project_id: str,
    ) -> tuple[TaskRecord, ...]:
        return await asyncio.to_thread(
            self._list_for_project_sync,
            project_id,
        )

    def _list_for_project_sync(
        self,
        project_id: str,
    ) -> tuple[TaskRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM tasks
                WHERE project_id = ?
                ORDER BY created_at ASC, task_id ASC
                """,
                (project_id,),
            ).fetchall()

        return tuple(self._row_to_task(row) for row in rows)

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            project_id=str(row["project_id"]),
            objective=str(row["objective"]),
            state=TaskState(str(row["state"])),
            active_plan_id=row["active_plan_id"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
