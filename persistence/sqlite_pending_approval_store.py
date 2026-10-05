from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any


class SqlitePendingApprovalStore:
    """Durable SQLite-backed store for pending tool approval requests.

    Pending approvals are written to disk immediately so they survive process
    restarts or host switches.  The Control Plane loads them on startup and
    presents them to the user before continuing task execution.
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
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    tool_call_id  TEXT PRIMARY KEY,
                    task_id       TEXT,
                    tool_name     TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    reason        TEXT NOT NULL DEFAULT '',
                    risk_explanation TEXT NOT NULL DEFAULT '',
                    prompt        TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_task
                ON pending_approvals(task_id)
                """
            )

    async def save(self, approval: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_sync, approval)

    def _save_sync(self, approval: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_approvals
                    (tool_call_id, task_id, tool_name, arguments_json,
                     reason, risk_explanation, prompt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval["tool_call_id"],
                    approval.get("task_id"),
                    approval.get("tool_name", ""),
                    json.dumps(approval.get("arguments") or {}),
                    approval.get("reason", ""),
                    approval.get("risk_explanation", ""),
                    approval.get("prompt", "May MyAgent perform this sensitive action?"),
                ),
            )

    async def get(self, tool_call_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_sync, tool_call_id)

    def _get_sync(self, tool_call_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    async def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_by_task_sync, task_id)

    def _list_by_task_sync(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_all_sync)

    def _list_all_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals ORDER BY created_at"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def delete(self, tool_call_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._delete_sync, tool_call_id)

    def _delete_sync(self, tool_call_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM pending_approvals WHERE tool_call_id = ?",
                    (tool_call_id,),
                )
        return self._row_to_dict(row) if row else None

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["arguments"] = json.loads(d.pop("arguments_json", "{}"))
        return d
