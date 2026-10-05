from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from core.agent_role import AgentRole
from core.plan import (
    PlanRecord,
    PlanState,
    PlanStepRecord,
    PlanStepState,
)


class SQLitePlanStore:
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
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_plans_task
                ON plans(task_id, revision ASC)
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_steps (
                    step_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    state TEXT NOT NULL,
                    execution_session_id TEXT,
                    assigned_role TEXT NOT NULL DEFAULT 'backend_coder',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(plan_steps)"
                ).fetchall()
            }

            if "execution_session_id" not in columns:
                connection.execute(
                    """
                    ALTER TABLE plan_steps
                    ADD COLUMN execution_session_id TEXT
                    """
                )

            if "assigned_role" not in columns:
                connection.execute(
                    """
                    ALTER TABLE plan_steps
                    ADD COLUMN assigned_role TEXT NOT NULL DEFAULT 'backend_coder'
                    """
                )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_plan_steps_plan
                ON plan_steps(plan_id, step_index ASC)
                """
            )

    async def save_plan(self, plan: PlanRecord) -> None:
        await asyncio.to_thread(self._save_plan_sync, plan)

    def _save_plan_sync(self, plan: PlanRecord) -> None:
        with self._connect() as connection:
            self._upsert_plan(connection, plan)

    async def save_plan_with_steps(
        self,
        plan: PlanRecord,
        steps: Sequence[PlanStepRecord],
    ) -> None:
        resolved_steps = tuple(steps)

        await asyncio.to_thread(
            self._save_plan_with_steps_sync,
            plan,
            resolved_steps,
        )

    def _save_plan_with_steps_sync(
        self,
        plan: PlanRecord,
        steps: tuple[PlanStepRecord, ...],
    ) -> None:
        # The connection context commits only when the whole block
        # succeeds. Any exception rolls the transaction back.
        with self._connect() as connection:
            connection.execute("BEGIN")

            self._insert_plan(connection, plan)

            for step in steps:
                self._insert_step(connection, step)

    def _insert_plan(
        self,
        connection: sqlite3.Connection,
        plan: PlanRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO plans (
                plan_id,
                task_id,
                revision,
                state,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                plan.task_id,
                plan.revision,
                plan.state.value,
                plan.created_at.isoformat(),
                plan.updated_at.isoformat(),
            ),
        )

    def _upsert_plan(
        self,
        connection: sqlite3.Connection,
        plan: PlanRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO plans (
                plan_id,
                task_id,
                revision,
                state,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                task_id = excluded.task_id,
                revision = excluded.revision,
                state = excluded.state,
                updated_at = excluded.updated_at
            """,
            (
                plan.plan_id,
                plan.task_id,
                plan.revision,
                plan.state.value,
                plan.created_at.isoformat(),
                plan.updated_at.isoformat(),
            ),
        )

    async def get_plan(
        self,
        plan_id: str,
    ) -> PlanRecord | None:
        return await asyncio.to_thread(
            self._get_plan_sync,
            plan_id,
        )

    def _get_plan_sync(
        self,
        plan_id: str,
    ) -> PlanRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM plans
                WHERE plan_id = ?
                """,
                (plan_id,),
            ).fetchone()

        return (
            self._row_to_plan(row)
            if row is not None
            else None
        )

    async def list_for_task(
        self,
        task_id: str,
    ) -> tuple[PlanRecord, ...]:
        return await asyncio.to_thread(
            self._list_for_task_sync,
            task_id,
        )

    def _list_for_task_sync(
        self,
        task_id: str,
    ) -> tuple[PlanRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM plans
                WHERE task_id = ?
                ORDER BY revision ASC, created_at ASC, plan_id ASC
                """,
                (task_id,),
            ).fetchall()

        return tuple(
            self._row_to_plan(row)
            for row in rows
        )

    async def save_step(
        self,
        step: PlanStepRecord,
    ) -> None:
        await asyncio.to_thread(
            self._save_step_sync,
            step,
        )

    def _save_step_sync(
        self,
        step: PlanStepRecord,
    ) -> None:
        with self._connect() as connection:
            self._upsert_step(connection, step)

    def _insert_step(
        self,
        connection: sqlite3.Connection,
        step: PlanStepRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO plan_steps (
                step_id,
                plan_id,
                step_index,
                title,
                instruction,
                state,
                execution_session_id,
                assigned_role,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step.step_id,
                step.plan_id,
                step.step_index,
                step.title,
                step.instruction,
                step.state.value,
                step.execution_session_id,
                step.assigned_role.value,
                step.created_at.isoformat(),
                step.updated_at.isoformat(),
            ),
        )

    def _upsert_step(
        self,
        connection: sqlite3.Connection,
        step: PlanStepRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO plan_steps (
                step_id,
                plan_id,
                step_index,
                title,
                instruction,
                state,
                execution_session_id,
                assigned_role,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(step_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_index = excluded.step_index,
                title = excluded.title,
                instruction = excluded.instruction,
                state = excluded.state,
                execution_session_id = excluded.execution_session_id,
                assigned_role = excluded.assigned_role,
                updated_at = excluded.updated_at
            """,
            (
                step.step_id,
                step.plan_id,
                step.step_index,
                step.title,
                step.instruction,
                step.state.value,
                step.execution_session_id,
                step.assigned_role.value,
                step.created_at.isoformat(),
                step.updated_at.isoformat(),
            ),
        )

    async def get_step(
        self,
        step_id: str,
    ) -> PlanStepRecord | None:
        return await asyncio.to_thread(
            self._get_step_sync,
            step_id,
        )

    def _get_step_sync(
        self,
        step_id: str,
    ) -> PlanStepRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM plan_steps
                WHERE step_id = ?
                """,
                (step_id,),
            ).fetchone()

        return (
            self._row_to_step(row)
            if row is not None
            else None
        )

    async def list_steps(
        self,
        plan_id: str,
    ) -> tuple[PlanStepRecord, ...]:
        return await asyncio.to_thread(
            self._list_steps_sync,
            plan_id,
        )

    def _list_steps_sync(
        self,
        plan_id: str,
    ) -> tuple[PlanStepRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM plan_steps
                WHERE plan_id = ?
                ORDER BY step_index ASC, step_id ASC
                """,
                (plan_id,),
            ).fetchall()

        return tuple(
            self._row_to_step(row)
            for row in rows
        )

    @staticmethod
    def _row_to_plan(
        row: sqlite3.Row,
    ) -> PlanRecord:
        return PlanRecord(
            plan_id=str(row["plan_id"]),
            task_id=str(row["task_id"]),
            revision=int(row["revision"]),
            state=PlanState(str(row["state"])),
            created_at=datetime.fromisoformat(
                str(row["created_at"])
            ),
            updated_at=datetime.fromisoformat(
                str(row["updated_at"])
            ),
        )

    @staticmethod
    def _row_to_step(
        row: sqlite3.Row,
    ) -> PlanStepRecord:
        raw_role = row["assigned_role"] if "assigned_role" in row.keys() and row["assigned_role"] is not None else "backend_coder"
        return PlanStepRecord(
            step_id=str(row["step_id"]),
            plan_id=str(row["plan_id"]),
            step_index=int(row["step_index"]),
            title=str(row["title"]),
            instruction=str(row["instruction"]),
            state=PlanStepState(str(row["state"])),
            execution_session_id=(
                str(row["execution_session_id"])
                if row["execution_session_id"] is not None
                else None
            ),
            assigned_role=AgentRole(str(raw_role)),
            created_at=datetime.fromisoformat(
                str(row["created_at"])
            ),
            updated_at=datetime.fromisoformat(
                str(row["updated_at"])
            ),
        )
