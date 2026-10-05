from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from core.decision import (
    DecisionOption,
    DecisionRecord,
    DecisionSeverity,
    DecisionState,
)


class SQLiteDecisionStore:
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
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id       TEXT PRIMARY KEY,
                    task_id           TEXT NOT NULL,
                    step_id           TEXT,
                    severity          TEXT NOT NULL,
                    state             TEXT NOT NULL,
                    prompt            TEXT NOT NULL,
                    options_json      TEXT NOT NULL,
                    selected_option_id TEXT,
                    rationale         TEXT,
                    created_at        TEXT NOT NULL,
                    resolved_at       TEXT,
                    plan_id           TEXT,
                    session_id        TEXT,
                    expires_at        TEXT
                )
                """
            )
            # Migrate existing databases by adding new columns if they don't exist
            for col, col_type in [
                ("plan_id", "TEXT"),
                ("session_id", "TEXT"),
                ("expires_at", "TEXT"),
            ]:
                try:
                    connection.execute(f"ALTER TABLE decisions ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass  # Column already exists

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decisions_task
                ON decisions(task_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decisions_state
                ON decisions(state, created_at DESC)
                """
            )
            # Composite index for expiry sweep queries
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decisions_state_expires
                ON decisions(state, expires_at)
                WHERE state = 'open' AND expires_at IS NOT NULL
                """
            )

    async def save(self, record: DecisionRecord) -> None:
        await asyncio.to_thread(self._save_sync, record)

    def _save_sync(self, record: DecisionRecord) -> None:
        options_json = json.dumps([opt.to_dict() for opt in record.options])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decisions (
                    decision_id,
                    task_id,
                    step_id,
                    severity,
                    state,
                    prompt,
                    options_json,
                    selected_option_id,
                    rationale,
                    created_at,
                    resolved_at,
                    plan_id,
                    session_id,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    task_id = excluded.task_id,
                    step_id = excluded.step_id,
                    severity = excluded.severity,
                    state = excluded.state,
                    prompt = excluded.prompt,
                    options_json = excluded.options_json,
                    selected_option_id = excluded.selected_option_id,
                    rationale = excluded.rationale,
                    resolved_at = excluded.resolved_at,
                    plan_id = excluded.plan_id,
                    session_id = excluded.session_id,
                    expires_at = excluded.expires_at
                """,
                (
                    record.decision_id,
                    record.task_id,
                    record.step_id,
                    record.severity.value,
                    record.state.value,
                    record.prompt,
                    options_json,
                    record.selected_option_id,
                    record.rationale,
                    record.created_at,
                    record.resolved_at,
                    record.plan_id,
                    record.session_id,
                    record.expires_at,
                ),
            )

    async def get(self, decision_id: str) -> DecisionRecord | None:
        return await asyncio.to_thread(self._get_sync, decision_id)

    def _get_sync(self, decision_id: str) -> DecisionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._row_to_record(row) if row else None

    async def list_by_task(self, task_id: str) -> tuple[DecisionRecord, ...]:
        return await asyncio.to_thread(self._list_by_task_sync, task_id)

    def _list_by_task_sync(self, task_id: str) -> tuple[DecisionRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM decisions WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            return tuple(self._row_to_record(r) for r in rows)

    async def list_open(self, task_id: str | None = None) -> tuple[DecisionRecord, ...]:
        return await asyncio.to_thread(self._list_open_sync, task_id)

    def _list_open_sync(self, task_id: str | None) -> tuple[DecisionRecord, ...]:
        with self._connect() as connection:
            if task_id:
                rows = connection.execute(
                    "SELECT * FROM decisions WHERE state = 'open' AND task_id = ? ORDER BY created_at ASC",
                    (task_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM decisions WHERE state = 'open' ORDER BY created_at ASC"
                ).fetchall()
            return tuple(self._row_to_record(r) for r in rows)

    async def list_expired_open(self) -> tuple[DecisionRecord, ...]:
        """Returns OPEN decisions whose expires_at has passed (for expiry sweep)."""
        return await asyncio.to_thread(self._list_expired_open_sync)

    def _list_expired_open_sync(self) -> tuple[DecisionRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM decisions
                WHERE state = 'open'
                  AND expires_at IS NOT NULL
                  AND datetime(expires_at) <= datetime('now')
                ORDER BY expires_at ASC
                """
            ).fetchall()
            return tuple(self._row_to_record(r) for r in rows)

    def _row_to_record(self, row: sqlite3.Row) -> DecisionRecord:
        options_raw = json.loads(row["options_json"])
        options = tuple(DecisionOption.from_dict(opt) for opt in options_raw)
        return DecisionRecord(
            decision_id=row["decision_id"],
            task_id=row["task_id"],
            step_id=row["step_id"],
            severity=DecisionSeverity(row["severity"]),
            state=DecisionState(row["state"]),
            prompt=row["prompt"],
            options=options,
            selected_option_id=row["selected_option_id"],
            rationale=row["rationale"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            expires_at=row["expires_at"],
        )
