from __future__ import annotations

from pathlib import Path

import pytest

from core.context import ExecutionContext
from core.session import SessionRecord, SessionState
from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore


@pytest.mark.asyncio
async def test_coder_session_store_survives_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"

    store = SQLiteCoderSessionStore(database_path)
    await store.initialize()

    context = ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        agent_id="coder",
        session_id="session-1",
        task_id="task-1",
        project_id="project-1",
    )
    session = SessionRecord(
        session_id="session-1",
        context=context,
        runtime_id="antigravity",
        model_id="model-a",
        state=SessionState.RUNNING,
    )

    await store.save(session)

    session.set_runtime("codex")
    session.set_model("model-b")
    session.transition(SessionState.WAITING_APPROVAL)
    await store.save(session)

    reopened = SQLiteCoderSessionStore(database_path)
    await reopened.initialize()

    restored = await reopened.get("session-1")

    assert restored is not None
    assert restored.session_id == "session-1"
    assert restored.context == context
    assert restored.project_id == "project-1"
    assert restored.runtime_id == "codex"
    assert restored.model_id == "model-b"
    assert restored.state is SessionState.WAITING_APPROVAL

    sessions = await reopened.list_for_project("project-1")
    assert sessions == (restored,)


@pytest.mark.asyncio
async def test_coder_session_store_keeps_projects_isolated(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"

    store = SQLiteCoderSessionStore(database_path)
    await store.initialize()

    await store.save(
        SessionRecord(
            session_id="session-a",
            context=ExecutionContext(
                workspace_id="workspace-a",
                session_id="session-a",
                project_id="project-a",
            ),
            state=SessionState.RUNNING,
        )
    )
    await store.save(
        SessionRecord(
            session_id="session-b",
            context=ExecutionContext(
                workspace_id="workspace-b",
                session_id="session-b",
                project_id="project-b",
            ),
            state=SessionState.STOPPED,
        )
    )

    sessions = await store.list_for_project("project-a")

    assert tuple(session.session_id for session in sessions) == ("session-a",)


@pytest.mark.asyncio
async def test_coder_session_store_returns_latest_resumable_session(
    tmp_path: Path,
) -> None:
    import asyncio

    database_path = tmp_path / "state.db"
    store = SQLiteCoderSessionStore(database_path)
    await store.initialize()

    def make_session(
        session_id: str,
        state: SessionState,
    ) -> SessionRecord:
        return SessionRecord(
            session_id=session_id,
            context=ExecutionContext(
                workspace_id="workspace-1",
                session_id=session_id,
                project_id="project-1",
            ),
            state=state,
        )

    await store.save(
        make_session("older-running", SessionState.RUNNING)
    )
    await asyncio.sleep(0.01)

    await store.save(
        make_session("newer-stopped", SessionState.STOPPED)
    )
    await asyncio.sleep(0.01)

    await store.save(
        make_session(
            "latest-resumable",
            SessionState.WAITING_APPROVAL,
        )
    )

    latest = await store.get_latest_resumable("project-1")

    assert latest is not None
    assert latest.session_id == "latest-resumable"
    assert latest.state is SessionState.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_coder_session_store_returns_none_without_resumable_session(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    store = SQLiteCoderSessionStore(database_path)
    await store.initialize()

    await store.save(
        SessionRecord(
            session_id="failed-session",
            context=ExecutionContext(
                workspace_id="workspace-1",
                session_id="failed-session",
                project_id="project-1",
            ),
            state=SessionState.FAILED,
        )
    )

    latest = await store.get_latest_resumable("project-1")

    assert latest is None
