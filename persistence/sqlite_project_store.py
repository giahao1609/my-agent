from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from core.project import ProjectRecord


class SQLiteProjectStore:
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
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                "project_id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "workspace_path TEXT NOT NULL, "
                "current_phase TEXT, "
                "active_task_id TEXT, "
                "last_checkpoint_id TEXT, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL"
                ")"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS app_state ("
                "key TEXT PRIMARY KEY, "
                "value TEXT"
                ")"
            )

    async def create(self, project: ProjectRecord) -> None:
        await asyncio.to_thread(self._create_sync, project)

    def _create_sync(self, project: ProjectRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    project_id, name, workspace_path, current_phase,
                    active_task_id, last_checkpoint_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    project.name,
                    project.workspace_path,
                    project.current_phase,
                    project.active_task_id,
                    project.last_checkpoint_id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )

    async def get(self, project_id: str) -> ProjectRecord | None:
        return await asyncio.to_thread(self._get_sync, project_id)

    def _get_sync(self, project_id: str) -> ProjectRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return self._row_to_project(row) if row is not None else None

    async def list(self) -> tuple[ProjectRecord, ...]:
        return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> tuple[ProjectRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return tuple(self._row_to_project(row) for row in rows)

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> ProjectRecord:
        from datetime import datetime

        return ProjectRecord(
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            workspace_path=str(row["workspace_path"]),
            current_phase=row["current_phase"],
            active_task_id=row["active_task_id"],
            last_checkpoint_id=row["last_checkpoint_id"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    async def update(self, project: ProjectRecord) -> None:
        project.touch()
        await asyncio.to_thread(self._update_sync, project)

    def _update_sync(self, project: ProjectRecord) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE projects SET
                    name = ?,
                    workspace_path = ?,
                    current_phase = ?,
                    active_task_id = ?,
                    last_checkpoint_id = ?,
                    created_at = ?,
                    updated_at = ?
                WHERE project_id = ?
                """,
                (
                    project.name,
                    project.workspace_path,
                    project.current_phase,
                    project.active_task_id,
                    project.last_checkpoint_id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.project_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown project: {project.project_id}")

    async def get_active(self) -> ProjectRecord | None:
        return await asyncio.to_thread(self._get_active_sync)

    def _get_active_sync(self) -> ProjectRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = ?",
                ("active_project_id",),
            ).fetchone()

        if row is None or row["value"] is None:
            return None

        return self._get_sync(str(row["value"]))

    async def set_active(self, project_id: str) -> ProjectRecord:
        return await asyncio.to_thread(self._set_active_sync, project_id)

    def _set_active_sync(self, project_id: str) -> ProjectRecord:
        project = self._get_sync(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("active_project_id", project_id),
            )

        return project

    async def reset_state(self, project_id: str) -> ProjectRecord:
        return await asyncio.to_thread(self._reset_state_sync, project_id)

    def _reset_state_sync(self, project_id: str) -> ProjectRecord:
        project = self._get_sync(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")

        project.current_phase = None
        project.active_task_id = None
        project.last_checkpoint_id = None
        project.touch()
        self._update_sync(project)
        return project

    async def delete(self, project_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, project_id)

    def _delete_sync(self, project_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM projects WHERE project_id = ?",
                (project_id,),
            )
            connection.execute(
                """
                UPDATE app_state
                SET value = NULL
                WHERE key = ? AND value = ?
                """,
                ("active_project_id", project_id),
            )
        return cursor.rowcount > 0
