from __future__ import annotations

import pytest

from core.context import ExecutionContext
from integrations.sqlite_memory_backend import SQLiteMemoryBackend
from persistence.sqlite_memory_store import SQLiteMemoryStore


@pytest.mark.asyncio
async def test_memory_backend_capture_and_recall(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.initialize()
    backend = SQLiteMemoryBackend(store)

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        project_id="demo",
    )

    await backend.capture(
        context,
        (
            {
                "kind": "decision",
                "content": "Use PostgreSQL for transactional data.",
                "importance": 0.9,
            },
            {
                "kind": "note",
                "content": "Frontend uses React.",
                "importance": 0.5,
            },
        ),
    )

    records = await backend.recall(
        context,
        "PostgreSQL",
        limit=10,
    )

    assert len(records) == 1
    assert records[0]["project_id"] == "demo"
    assert records[0]["kind"] == "decision"
    assert records[0]["content"] == "Use PostgreSQL for transactional data."
    assert records[0]["importance"] == 0.9


@pytest.mark.asyncio
async def test_memory_backend_is_project_scoped(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.initialize()
    backend = SQLiteMemoryBackend(store)

    await backend.capture(
        ExecutionContext(
            workspace_id=str(tmp_path),
            project_id="project-a",
        ),
        ({"kind": "note", "content": "secret-a"},),
    )

    records = await backend.recall(
        ExecutionContext(
            workspace_id=str(tmp_path),
            project_id="project-b",
        ),
        "secret",
    )

    assert records == ()


@pytest.mark.asyncio
async def test_memory_backend_requires_project_scope(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.initialize()
    backend = SQLiteMemoryBackend(store)

    context = ExecutionContext(workspace_id=str(tmp_path))

    with pytest.raises(ValueError, match="project_id"):
        await backend.recall(context, "anything")

    with pytest.raises(ValueError, match="project_id"):
        await backend.capture(
            context,
            ({"kind": "note", "content": "anything"},),
        )
