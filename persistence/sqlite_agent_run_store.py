from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

from core.agent_role import AgentRole
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_work_result import AgentWorkResult


class SQLiteAgentRunStore:
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
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT,
                    step_id TEXT,
                    agent_id TEXT NOT NULL,
                    execution_role TEXT NOT NULL,
                    model_id TEXT,
                    runtime_id TEXT,
                    session_id TEXT,
                    state TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    metadata_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_runs_step
                ON agent_runs(step_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_runs_task
                ON agent_runs(task_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_runs_plan
                ON agent_runs(plan_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_runs_project
                ON agent_runs(project_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_runs_session
                ON agent_runs(session_id, created_at DESC)
                """
            )

    async def save(self, run: AgentRunRecord) -> None:
        await asyncio.to_thread(self._save_sync, run)

    def _save_sync(self, run: AgentRunRecord) -> None:
        result_json = json.dumps(run.result.to_dict()) if run.result else None
        metadata_json = json.dumps(run.metadata) if run.metadata else None

        role_str = run.execution_role.value if isinstance(run.execution_role, AgentRole) else str(run.execution_role)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id,
                    project_id,
                    task_id,
                    plan_id,
                    step_id,
                    agent_id,
                    execution_role,
                    model_id,
                    runtime_id,
                    session_id,
                    state,
                    started_at,
                    finished_at,
                    created_at,
                    updated_at,
                    result_json,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    task_id = excluded.task_id,
                    plan_id = excluded.plan_id,
                    step_id = excluded.step_id,
                    agent_id = excluded.agent_id,
                    execution_role = excluded.execution_role,
                    model_id = excluded.model_id,
                    runtime_id = excluded.runtime_id,
                    session_id = excluded.session_id,
                    state = excluded.state,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    updated_at = excluded.updated_at,
                    result_json = excluded.result_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    run.run_id,
                    run.project_id,
                    run.task_id,
                    run.plan_id,
                    run.step_id,
                    run.agent_id,
                    role_str,
                    run.model_id,
                    run.runtime_id,
                    run.session_id,
                    run.state.value,
                    run.started_at,
                    run.finished_at,
                    run.created_at,
                    run.updated_at,
                    result_json,
                    metadata_json,
                ),
            )

    async def get(self, run_id: str) -> AgentRunRecord | None:
        return await asyncio.to_thread(self._get_sync, run_id)

    def _get_sync(self, run_id: str) -> AgentRunRecord | None:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    async def list_for_task(self, task_id: str) -> tuple[AgentRunRecord, ...]:
        return await asyncio.to_thread(self._list_for_task_sync, task_id)

    def _list_for_task_sync(self, task_id: str) -> tuple[AgentRunRecord, ...]:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            )
            return tuple(self._row_to_record(r) for r in cursor.fetchall())

    async def list_for_plan(self, plan_id: str) -> tuple[AgentRunRecord, ...]:
        return await asyncio.to_thread(self._list_for_plan_sync, plan_id)

    def _list_for_plan_sync(self, plan_id: str) -> tuple[AgentRunRecord, ...]:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE plan_id = ? ORDER BY created_at ASC",
                (plan_id,),
            )
            return tuple(self._row_to_record(r) for r in cursor.fetchall())

    async def list_for_step(self, step_id: str) -> tuple[AgentRunRecord, ...]:
        return await asyncio.to_thread(self._list_for_step_sync, step_id)

    def _list_for_step_sync(self, step_id: str) -> tuple[AgentRunRecord, ...]:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE step_id = ? ORDER BY created_at ASC",
                (step_id,),
            )
            return tuple(self._row_to_record(r) for r in cursor.fetchall())

    async def get_latest_for_step(self, step_id: str) -> AgentRunRecord | None:
        return await asyncio.to_thread(self._get_latest_for_step_sync, step_id)

    def _get_latest_for_step_sync(self, step_id: str) -> AgentRunRecord | None:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE step_id = ? ORDER BY created_at DESC LIMIT 1",
                (step_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    async def get_latest_completed_for_step(self, step_id: str) -> AgentRunRecord | None:
        return await asyncio.to_thread(self._get_latest_completed_for_step_sync, step_id)

    def _get_latest_completed_for_step_sync(self, step_id: str) -> AgentRunRecord | None:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE step_id = ? AND state = 'completed' ORDER BY created_at DESC LIMIT 1",
                (step_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    async def list_recent_for_project(self, project_id: str, limit: int = 20) -> tuple[AgentRunRecord, ...]:
        return await asyncio.to_thread(self._list_recent_for_project_sync, project_id, limit)

    def _list_recent_for_project_sync(self, project_id: str, limit: int = 20) -> tuple[AgentRunRecord, ...]:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
            return tuple(self._row_to_record(r) for r in cursor.fetchall())

    async def list_for_session(self, session_id: str) -> tuple[AgentRunRecord, ...]:
        return await asyncio.to_thread(self._list_for_session_sync, session_id)

    def _list_for_session_sync(self, session_id: str) -> tuple[AgentRunRecord, ...]:
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT * FROM agent_runs WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            return tuple(self._row_to_record(r) for r in cursor.fetchall())

    def _row_to_record(self, row: sqlite3.Row) -> AgentRunRecord:
        result = None
        if row["result_json"]:
            result = AgentWorkResult.from_dict(json.loads(row["result_json"]))

        metadata = {}
        if row["metadata_json"]:
            metadata = json.loads(row["metadata_json"])

        try:
            role = AgentRole(row["execution_role"])
        except ValueError:
            role = AgentRole.BACKEND_CODER

        return AgentRunRecord(
            run_id=row["run_id"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            plan_id=row["plan_id"],
            step_id=row["step_id"],
            agent_id=row["agent_id"],
            execution_role=role,
            model_id=row["model_id"],
            runtime_id=row["runtime_id"],
            session_id=row["session_id"],
            state=AgentRunState(row["state"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=result,
            metadata=metadata,
        )
