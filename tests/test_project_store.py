from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ProjectRecord
from persistence.sqlite_project_store import SQLiteProjectStore


@pytest.mark.asyncio
async def test_project_store_persists_and_isolates_projects(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    workspace_a = tmp_path / "project-a"
    workspace_b = tmp_path / "project-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    store = SQLiteProjectStore(database_path)
    await store.initialize()

    project_a = ProjectRecord(
        project_id="project-a",
        name="Project A",
        workspace_path=str(workspace_a),
        current_phase="phase-2",
        active_task_id="task-a",
        last_checkpoint_id="checkpoint-a",
    )
    project_b = ProjectRecord(
        project_id="project-b",
        name="Project B",
        workspace_path=str(workspace_b),
        current_phase="phase-1",
    )

    await store.create(project_a)
    await store.create(project_b)
    active = await store.set_active("project-b")

    assert active.project_id == "project-b"
    assert (await store.get_active()).project_id == "project-b"

    reopened = SQLiteProjectStore(database_path)
    await reopened.initialize()

    projects = await reopened.list()
    assert {project.project_id for project in projects} == {"project-a", "project-b"}
    assert (await reopened.get_active()).project_id == "project-b"

    reset = await reopened.reset_state("project-a")
    assert reset.current_phase is None
    assert reset.active_task_id is None
    assert reset.last_checkpoint_id is None
    assert (await reopened.get("project-b")).current_phase == "phase-1"

    deleted = await reopened.delete("project-a")
    assert deleted is True
    assert await reopened.get("project-a") is None
    assert await reopened.get("project-b") is not None

    assert workspace_a.exists()
    assert workspace_b.exists()
