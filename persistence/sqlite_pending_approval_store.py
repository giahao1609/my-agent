from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_APPROVAL_MAX_AGE_SECONDS: float = 86400.0  # 24 hours


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqlitePendingApprovalStore:
    """Durable SQLite-backed store for pending tool approval requests.

    Pending approvals are written to disk immediately so they survive process
    restarts or host switches.  The Control Plane and CoderAgent load them on
    startup/resume and verify approval status, session/project boundaries,
    and expiration TTL before continuing execution.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        default_max_age_seconds: float = DEFAULT_APPROVAL_MAX_AGE_SECONDS,
    ) -> None:
        self._database_path = Path(database_path)
        self._default_max_age_seconds = default_max_age_seconds

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
                    tool_call_id     TEXT PRIMARY KEY,
                    task_id          TEXT,
                    session_id       TEXT,
                    project_id       TEXT,
                    tool_name        TEXT NOT NULL,
                    arguments_json   TEXT NOT NULL DEFAULT '{}',
                    reason           TEXT NOT NULL DEFAULT '',
                    risk_explanation TEXT NOT NULL DEFAULT '',
                    prompt           TEXT NOT NULL DEFAULT '',
                    status           TEXT NOT NULL DEFAULT 'PENDING',
                    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                    expires_at       TEXT,
                    resolved_at      TEXT,
                    resolution_note  TEXT,
                    executed_at      TEXT,
                    execution_result_json TEXT
                )
                """
            )
            # Schema migrations for backward compatibility using PRAGMA introspection
            existing_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(pending_approvals)").fetchall()
            }
            for col, col_type in (
                ("session_id", "TEXT"),
                ("project_id", "TEXT"),
                ("status", "TEXT NOT NULL DEFAULT 'PENDING'"),
                ("expires_at", "TEXT"),
                ("resolved_at", "TEXT"),
                ("resolution_note", "TEXT"),
                ("executed_at", "TEXT"),
                ("execution_result_json", "TEXT"),
            ):
                if col not in existing_cols:
                    try:
                        conn.execute(f"ALTER TABLE pending_approvals ADD COLUMN {col} {col_type}")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" in str(exc).lower():
                            pass
                        else:
                            raise

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_task
                ON pending_approvals(task_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_session
                ON pending_approvals(session_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_project
                ON pending_approvals(project_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_status
                ON pending_approvals(status)
                """
            )

    def is_expired(self, approval: dict[str, Any], now: datetime | None = None) -> bool:
        """Determines whether a pending approval record has exceeded its expiration TTL."""
        ref_now = now or datetime.now(timezone.utc)
        expires_at_str = approval.get("expires_at")
        if expires_at_str:
            try:
                exp_dt = datetime.fromisoformat(expires_at_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                return ref_now >= exp_dt
            except (ValueError, TypeError):
                pass

        created_at_str = approval.get("created_at")
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                return (ref_now - created_dt).total_seconds() >= self._default_max_age_seconds
            except (ValueError, TypeError):
                pass
        return False

    async def save(self, approval: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_sync, approval)

    def _save_sync(self, approval: dict[str, Any]) -> None:
        tool_call_id = approval["tool_call_id"]
        now_dt = datetime.now(timezone.utc)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()

            if existing is not None:
                existing_dict = self._row_to_dict(existing)
                # 1. Scope collision checks - fail closed
                if (
                    existing_dict.get("session_id")
                    and approval.get("session_id")
                    and existing_dict.get("session_id") != approval.get("session_id")
                ):
                    raise ValueError(
                        f"tool_call_id collision across sessions: {tool_call_id} "
                        f"({existing_dict.get('session_id')} != {approval.get('session_id')})"
                    )
                if (
                    existing_dict.get("project_id")
                    and approval.get("project_id")
                    and existing_dict.get("project_id") != approval.get("project_id")
                ):
                    raise ValueError(
                        f"tool_call_id collision across projects: {tool_call_id} "
                        f"({existing_dict.get('project_id')} != {approval.get('project_id')})"
                    )
                if (
                    existing_dict.get("tool_name")
                    and approval.get("tool_name")
                    and existing_dict.get("tool_name") != approval.get("tool_name")
                ):
                    raise ValueError(
                        f"tool_call_id collision across tool_name: {tool_call_id} "
                        f"({existing_dict.get('tool_name')} != {approval.get('tool_name')})"
                    )

                # 2. Terminal state / resurrection check
                existing_status = existing_dict.get("status", "PENDING")
                is_curr_expired = self.is_expired(existing_dict, now_dt)
                if existing_status in {"APPROVED", "REJECTED", "EXPIRED"} or is_curr_expired:
                    if approval.get("status", "PENDING") == "PENDING":
                        term_reason = "EXPIRED" if is_curr_expired else existing_status
                        raise ValueError(
                            f"Cannot resurrect terminal/expired approval {tool_call_id} "
                            f"(current status: {term_reason})"
                        )

                # 3. Idempotent re-save of PENDING: preserve original created_at and expires_at!
                # Do NOT extend TTL or overwrite resolution info.
                conn.execute(
                    """
                    UPDATE pending_approvals
                    SET task_id = ?, session_id = ?, project_id = ?, tool_name = ?,
                        arguments_json = ?, reason = ?, risk_explanation = ?, prompt = ?
                    WHERE tool_call_id = ?
                    """,
                    (
                        approval.get("task_id", existing_dict.get("task_id")),
                        approval.get("session_id", existing_dict.get("session_id")),
                        approval.get("project_id", existing_dict.get("project_id")),
                        approval.get("tool_name", existing_dict.get("tool_name")),
                        json.dumps(approval.get("arguments") or existing_dict.get("arguments") or {}),
                        approval.get("reason", existing_dict.get("reason", "")),
                        approval.get("risk_explanation", existing_dict.get("risk_explanation", "")),
                        approval.get("prompt", existing_dict.get("prompt", "")),
                        tool_call_id,
                    ),
                )
                return

            # Brand new approval record insertion
            created_at = approval.get("created_at") or now_dt.isoformat()
            expires_at = approval.get("expires_at")
            if not expires_at:
                try:
                    c_dt = datetime.fromisoformat(created_at)
                    if c_dt.tzinfo is None:
                        c_dt = c_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    c_dt = now_dt
                expires_at = (c_dt + timedelta(seconds=self._default_max_age_seconds)).isoformat()

            status = approval.get("status", "PENDING")

            reason = approval.get("reason", "")
            risk_explanation = approval.get("risk_explanation", "")
            raw_args = json.dumps(approval.get("arguments") or {})
            try:
                from core.security_scanner import SecretRedactor
                reason = SecretRedactor.redact(reason)
                risk_explanation = SecretRedactor.redact(risk_explanation)
                arguments_json = SecretRedactor.redact(raw_args)
            except Exception:
                arguments_json = raw_args

            conn.execute(
                """
                INSERT INTO pending_approvals
                    (tool_call_id, task_id, session_id, project_id, tool_name,
                     arguments_json, reason, risk_explanation, prompt, status,
                     created_at, expires_at, resolved_at, resolution_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    approval.get("task_id"),
                    approval.get("session_id"),
                    approval.get("project_id"),
                    approval.get("tool_name", ""),
                    arguments_json,
                    reason,
                    risk_explanation,
                    approval.get("prompt", "May MyAgent perform this sensitive action?"),
                    status,
                    created_at,
                    expires_at,
                    approval.get("resolved_at"),
                    approval.get("resolution_note"),
                ),
            )

    async def resolve(
        self,
        tool_call_id: str,
        *,
        status: str = "APPROVED",
        session_id: str | None = None,
        project_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._resolve_sync,
            tool_call_id,
            status=status,
            session_id=session_id,
            project_id=project_id,
            note=note,
        )

    async def claim_and_resolve(
        self,
        tool_call_id: str,
        *,
        status: str = "APPROVED",
        session_id: str | None = None,
        project_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        res = await asyncio.to_thread(
            self._resolve_sync,
            tool_call_id,
            status=status,
            session_id=session_id,
            project_id=project_id,
            note=note,
        )
        if res is None:
            raise KeyError(f"no pending approval for tool call: {tool_call_id}")
        return res

    def _claim_and_resolve_sync(
        self,
        tool_call_id: str,
        *,
        status: str = "APPROVED",
        session_id: str | None = None,
        project_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        res = self._resolve_sync(
            tool_call_id,
            status=status,
            session_id=session_id,
            project_id=project_id,
            note=note,
        )
        if res is None:
            raise KeyError(f"no pending approval for tool call: {tool_call_id}")
        return res

    def _resolve_sync(
        self,
        tool_call_id: str,
        *,
        status: str = "APPROVED",
        session_id: str | None = None,
        project_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any] | None:
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if not row:
                return None

            rec = self._row_to_dict(row)
            curr_status = rec.get("status", "PENDING")

            # Scope checks
            if session_id and rec.get("session_id") and rec.get("session_id") != session_id:
                raise ValueError(
                    f"Approval session mismatch: {rec.get('session_id')} != {session_id}"
                )
            if project_id and rec.get("project_id") and rec.get("project_id") != project_id:
                raise ValueError(
                    f"Approval project mismatch: {rec.get('project_id')} != {project_id}"
                )

            # Expiry check
            if self.is_expired(rec, now_dt):
                if curr_status != "EXPIRED":
                    conn.execute(
                        """
                        UPDATE pending_approvals
                        SET status = 'EXPIRED', resolved_at = ?, resolution_note = ?
                        WHERE tool_call_id = ?
                        """,
                        (now_iso, "Approval TTL expired prior to execution", tool_call_id),
                    )
                    conn.commit()
                raise PermissionError(
                    f"Approval for tool call {tool_call_id} has expired (TTL elapsed)"
                )

            # Terminal state check
            if curr_status in {"APPROVED", "REJECTED", "EXPIRED"}:
                raise ValueError(
                    f"Approval for tool call {tool_call_id} is in non-pending state: {curr_status}"
                )

            try:
                from core.security_scanner import SecretRedactor
                clean_note = SecretRedactor.redact(note)
            except Exception:
                clean_note = note

            # Atomic CAS update: exactly one winner
            cur = conn.execute(
                """
                UPDATE pending_approvals
                SET status = ?, resolved_at = ?, resolution_note = ?
                WHERE tool_call_id = ? AND status = 'PENDING'
                """,
                (status, now_iso, clean_note, tool_call_id),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    f"Approval for tool call {tool_call_id} was claimed or resolved concurrently by another request"
                )

            updated_row = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            return self._row_to_dict(updated_row) if updated_row else None

    async def record_execution(
        self,
        tool_call_id: str,
        *,
        execution_success: bool,
        result: dict[str, Any] | None = None,
        executed_at: str | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._record_execution_sync,
            tool_call_id,
            execution_success=execution_success,
            result=result,
            executed_at=executed_at,
        )

    def _record_execution_sync(
        self,
        tool_call_id: str,
        *,
        execution_success: bool,
        result: dict[str, Any] | None = None,
        executed_at: str | None = None,
    ) -> dict[str, Any] | None:
        now_iso = executed_at or _utc_iso_now()
        raw_json = json.dumps(result or {"success": execution_success})
        try:
            from core.security_scanner import SecretRedactor
            res_json = SecretRedactor.redact(raw_json)
        except Exception:
            res_json = raw_json
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pending_approvals
                SET executed_at = ?, execution_result_json = ?
                WHERE tool_call_id = ?
                """,
                (now_iso, res_json, tool_call_id),
            )
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    async def get(self, tool_call_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_sync, tool_call_id)

    def _get_sync(self, tool_call_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    async def list_by_session(
        self,
        session_id: str,
        *,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_by_session_sync, session_id, active_only=active_only)

    def _list_by_session_sync(
        self,
        session_id: str,
        *,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        records = [self._row_to_dict(r) for r in rows]
        if active_only:
            records = [
                r for r in records
                if r.get("status") == "PENDING" and not self.is_expired(r)
            ]
        return records

    async def list_by_project(
        self,
        project_id: str,
        *,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_by_project_sync, project_id, active_only=active_only)

    def _list_by_project_sync(
        self,
        project_id: str,
        *,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        records = [self._row_to_dict(r) for r in rows]
        if active_only:
            records = [
                r for r in records
                if r.get("status") == "PENDING" and not self.is_expired(r)
            ]
        return records

    async def list_by_task(
        self,
        task_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_by_task_sync, task_id, active_only=active_only)

    def _list_by_task_sync(
        self,
        task_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        records = [self._row_to_dict(r) for r in rows]
        if active_only:
            records = [
                r for r in records
                if r.get("status") == "PENDING" and not self.is_expired(r)
            ]
        return records

    async def list_all(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_all_sync, active_only=active_only)

    def _list_all_sync(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals ORDER BY created_at"
            ).fetchall()
        records = [self._row_to_dict(r) for r in rows]
        if active_only:
            records = [
                r for r in records
                if r.get("status") == "PENDING" and not self.is_expired(r)
            ]
        return records

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
