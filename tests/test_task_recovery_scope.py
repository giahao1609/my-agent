from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest

from core.checkpoint import CheckpointRecord
from core.context import ExecutionContext
from core.session import SessionRecord, SessionState
from persistence.sqlite_checkpoint_store import SQLiteCheckpointStore
from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore


@pytest.mark.asyncio
async def test_checkpoint_store_can_scope_latest_checkpoint_by_task(
    tmp_path,
) -> None:
    store = SQLiteCheckpointStore(tmp_path / "agent.db")
    await store.initialize()

    await store.save(
        CheckpointRecord(
            checkpoint_id="checkpoint-task-1",
            project_id="project-1",
            task_id="task-1",
            summary="Task 1 checkpoint",
            created_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
        )
    )
    await store.save(
        CheckpointRecord(
            checkpoint_id="checkpoint-task-2",
            project_id="project-1",
            task_id="task-2",
            summary="Task 2 checkpoint",
            created_at=datetime(2026, 8, 24, 2, 0, tzinfo=UTC),
        )
    )

    task_1_latest = await store.latest(
        "project-1",
        task_id="task-1",
    )
    task_2_latest = await store.latest(
        "project-1",
        task_id="task-2",
    )

    assert task_1_latest is not None
    assert task_1_latest.checkpoint_id == "checkpoint-task-1"
    assert task_1_latest.task_id == "task-1"

    assert task_2_latest is not None
    assert task_2_latest.checkpoint_id == "checkpoint-task-2"
    assert task_2_latest.task_id == "task-2"


@pytest.mark.asyncio
async def test_checkpoint_latest_without_task_keeps_project_behavior(
    tmp_path,
) -> None:
    store = SQLiteCheckpointStore(tmp_path / "agent.db")
    await store.initialize()

    await store.save(
        CheckpointRecord(
            checkpoint_id="older",
            project_id="project-1",
            task_id="task-1",
            summary="Older",
            created_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
        )
    )
    await store.save(
        CheckpointRecord(
            checkpoint_id="newer",
            project_id="project-1",
            task_id="task-2",
            summary="Newer",
            created_at=datetime(2026, 8, 24, 2, 0, tzinfo=UTC),
        )
    )

    latest = await store.latest("project-1")

    assert latest is not None
    assert latest.checkpoint_id == "newer"
    assert latest.task_id == "task-2"


@pytest.mark.asyncio
async def test_coder_session_store_can_scope_latest_resumable_by_task(
    tmp_path,
) -> None:
    store = SQLiteCoderSessionStore(tmp_path / "agent.db")
    await store.initialize()

    await store.save(
        SessionRecord(
            session_id="task-1-session",
            context=ExecutionContext(
                workspace_id="workspace-1",
                session_id="task-1-session",
                task_id="task-1",
                project_id="project-1",
            ),
            state=SessionState.RUNNING,
        )
    )

    await asyncio.sleep(0.01)

    # This is newer, but belongs to another task.
    await store.save(
        SessionRecord(
            session_id="task-2-session",
            context=ExecutionContext(
                workspace_id="workspace-1",
                session_id="task-2-session",
                task_id="task-2",
                project_id="project-1",
            ),
            state=SessionState.RUNNING,
        )
    )

    task_1_latest = await store.get_latest_resumable(
        "project-1",
        task_id="task-1",
    )
    project_latest = await store.get_latest_resumable("project-1")

    assert task_1_latest is not None
    assert task_1_latest.session_id == "task-1-session"
    assert task_1_latest.context.task_id == "task-1"

    # No task filter preserves the old project-scoped behavior.
    assert project_latest is not None
    assert project_latest.session_id == "task-2-session"


@pytest.mark.asyncio
async def test_checkpoint_store_migrates_legacy_schema_without_losing_data(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
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
        connection.execute(
            """
            INSERT INTO checkpoints (
                checkpoint_id,
                project_id,
                summary,
                next_action,
                conversation_id,
                session_id,
                files_changed_json,
                tests_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-checkpoint",
                "project-1",
                "Legacy checkpoint",
                None,
                None,
                None,
                "[]",
                "[]",
                datetime(2026, 8, 24, 1, 0, tzinfo=UTC).isoformat(),
            ),
        )

    store = SQLiteCheckpointStore(database_path)
    await store.initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(checkpoints)"
            ).fetchall()
        }

    assert "task_id" in columns

    restored = await store.latest("project-1")

    assert restored is not None
    assert restored.checkpoint_id == "legacy-checkpoint"
    assert restored.task_id is None
