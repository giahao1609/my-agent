from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from core.security_scanner import SecretRedactor


class SqliteHandoffHistoryStore:
    """Durable SQLite-backed store for HandoffCoordinator decision audit history.

    Persists handoff requests and decisions (scope, roles, approval status,
    guardrail outcomes, timestamp) so that handoff decision history survives
    process restarts and host transitions. Does not store raw payloads or
    unrestricted secret data.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_sync(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS handoff_decisions (
                    handoff_id              TEXT PRIMARY KEY,
                    task_id                 TEXT NOT NULL,
                    project_id              TEXT,
                    source_role             TEXT NOT NULL,
                    target_role             TEXT NOT NULL,
                    status                  TEXT NOT NULL,
                    reasons_json            TEXT NOT NULL DEFAULT '[]',
                    guardrail_results_json  TEXT NOT NULL DEFAULT '{}',
                    timestamp               REAL NOT NULL,
                    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # Migration check using PRAGMA introspection
            existing_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(handoff_decisions)").fetchall()
            }
            for col, col_type in (
                ("project_id", "TEXT"),
            ):
                if col not in existing_cols:
                    try:
                        conn.execute(f"ALTER TABLE handoff_decisions ADD COLUMN {col} {col_type}")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" in str(exc).lower():
                            pass
                        else:
                            raise

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_handoff_decisions_task
                ON handoff_decisions(task_id, timestamp)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_handoff_decisions_project
                ON handoff_decisions(project_id, timestamp)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_handoff_decisions_status
                ON handoff_decisions(status, timestamp)
                """
            )

    async def save(self, decision: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_sync, decision)

    def save_sync(self, decision: dict[str, Any]) -> None:
        self._save_sync(decision)

    def _save_sync(self, decision: dict[str, Any]) -> None:
        reasons = decision.get("reasons") or []
        sanitized_reasons = [SecretRedactor.redact(str(r)) for r in reasons]
        reasons_json = json.dumps(sanitized_reasons)

        guardrails = decision.get("guardrail_results") or {}
        sanitized_guardrails = {
            SecretRedactor.redact(str(k)): SecretRedactor.redact(str(v))
            for k, v in guardrails.items()
        }
        guardrails_json = json.dumps(sanitized_guardrails)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO handoff_decisions
                    (handoff_id, task_id, project_id, source_role, target_role,
                     status, reasons_json, guardrail_results_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision["handoff_id"],
                    decision["task_id"],
                    decision.get("project_id"),
                    decision["source_role"],
                    decision["target_role"],
                    decision["status"],
                    reasons_json,
                    guardrails_json,
                    float(decision.get("timestamp", datetime.now(timezone.utc).timestamp())),
                ),
            )

    async def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_by_task_sync, task_id)

    def _list_by_task_sync(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM handoff_decisions
                WHERE task_id = ?
                ORDER BY timestamp ASC
                """,
                (task_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def list_by_project(self, project_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_by_project_sync, project_id)

    def _list_by_project_sync(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM handoff_decisions
                WHERE project_id = ?
                ORDER BY timestamp ASC
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def list_stale(self, accepted_before_timestamp: float) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_stale_sync, accepted_before_timestamp)

    def _list_stale_sync(self, accepted_before_timestamp: float) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM handoff_decisions
                WHERE status = 'accepted' AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                (accepted_before_timestamp,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_all_sync)

    def _list_all_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM handoff_decisions ORDER BY timestamp ASC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_history(
        self,
        *,
        task_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            query = "SELECT * FROM handoff_decisions"
            params: list[Any] = []
            conditions: list[str] = []
            if project_id is not None:
                conditions.append("project_id = ?")
                params.append(project_id)
            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp ASC"
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def list_history_async(
        self,
        *,
        task_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.list_history,
            task_id=task_id,
            project_id=project_id,
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["reasons"] = json.loads(d.pop("reasons_json", "[]"))
        d["guardrail_results"] = json.loads(d.pop("guardrail_results_json", "{}"))
        d["sanitized_payload"] = {}  # Payloads are not stored in audit history
        return d
