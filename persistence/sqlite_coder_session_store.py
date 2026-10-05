from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.context import ExecutionContext
from core.session import SessionRecord, SessionState


class SQLiteCoderSessionStore:
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
                CREATE TABLE IF NOT EXISTS coder_sessions (
                    session_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT,
                    agent_id TEXT,
                    context_session_id TEXT,
                    task_id TEXT,
                    project_id TEXT,
                    runtime_id TEXT,
                    model_id TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_coder_sessions_project
                ON coder_sessions(project_id, updated_at)
                """
            )

    async def save(self, session: SessionRecord) -> None:
        await asyncio.to_thread(self._save_sync, session)

    def _save_sync(self, session: SessionRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        context = session.context

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO coder_sessions (
                    session_id,
                    workspace_id,
                    user_id,
                    agent_id,
                    context_session_id,
                    task_id,
                    project_id,
                    runtime_id,
                    model_id,
                    state,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    user_id = excluded.user_id,
                    agent_id = excluded.agent_id,
                    context_session_id = excluded.context_session_id,
                    task_id = excluded.task_id,
                    project_id = excluded.project_id,
                    runtime_id = excluded.runtime_id,
                    model_id = excluded.model_id,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_id,
                    context.workspace_id,
                    context.user_id,
                    context.agent_id,
                    context.session_id,
                    context.task_id,
                    context.project_id,
                    session.runtime_id,
                    session.model_id,
                    session.state.value,
                    now,
                    now,
                ),
            )

    async def get(self, session_id: str) -> SessionRecord | None:
        return await asyncio.to_thread(self._get_sync, session_id)

    def _get_sync(self, session_id: str) -> SessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM coder_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        return self._row_to_session(row) if row is not None else None

    async def list_for_project(
        self,
        project_id: str,
    ) -> tuple[SessionRecord, ...]:
        return await asyncio.to_thread(
            self._list_for_project_sync,
            project_id,
        )

    def _list_for_project_sync(
        self,
        project_id: str,
    ) -> tuple[SessionRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM coder_sessions
                WHERE project_id = ?
                ORDER BY updated_at DESC, session_id ASC
                """,
                (project_id,),
            ).fetchall()

        return tuple(self._row_to_session(row) for row in rows)

    async def get_latest_resumable(
        self,
        project_id: str,
        task_id: str | None = None,
    ) -> SessionRecord | None:
        return await asyncio.to_thread(
            self._get_latest_resumable_sync,
            project_id,
            task_id,
        )

    def _get_latest_resumable_sync(
        self,
        project_id: str,
        task_id: str | None,
    ) -> SessionRecord | None:
        resumable_states = (
            SessionState.CREATED.value,
            SessionState.STARTING.value,
            SessionState.RUNNING.value,
            SessionState.WAITING_APPROVAL.value,
        )

        with self._connect() as connection:
            if task_id is None:
                row = connection.execute(
                    """
                    SELECT *
                    FROM coder_sessions
                    WHERE project_id = ?
                      AND state IN (?, ?, ?, ?)
                    ORDER BY updated_at DESC, session_id ASC
                    LIMIT 1
                    """,
                    (
                        project_id,
                        *resumable_states,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT *
                    FROM coder_sessions
                    WHERE project_id = ?
                      AND task_id = ?
                      AND state IN (?, ?, ?, ?)
                    ORDER BY updated_at DESC, session_id ASC
                    LIMIT 1
                    """,
                    (
                        project_id,
                        task_id,
                        *resumable_states,
                    ),
                ).fetchone()

        return self._row_to_session(row) if row is not None else None

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
        context = ExecutionContext(
            workspace_id=str(row["workspace_id"]),
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            session_id=row["context_session_id"],
            task_id=row["task_id"],
            project_id=row["project_id"],
        )

        return SessionRecord(
            session_id=str(row["session_id"]),
            context=context,
            runtime_id=row["runtime_id"],
            model_id=row["model_id"],
            state=SessionState(str(row["state"])),
        )
