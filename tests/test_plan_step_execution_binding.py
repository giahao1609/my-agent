from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from core.plan import PlanStepState
from core.plan_service import PlanService
from core.project import ProjectRecord
from core.task_service import TaskService
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_service(tmp_path):
    database_path = tmp_path / "agent.db"

    projects = SQLiteProjectStore(database_path)
    tasks = SQLiteTaskStore(database_path)
    plans = SQLitePlanStore(database_path)

    await projects.initialize()
    await tasks.initialize()
    await plans.initialize()

    await projects.create(
        ProjectRecord(
            project_id="project-1",
            name="Project",
            workspace_path=str(tmp_path),
        )
    )

    task_service = TaskService(
        project_store=projects,
        task_store=tasks,
    )
    plan_service = PlanService(
        task_store=tasks,
        plan_store=plans,
    )

    await task_service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Execute plan",
    )
    await task_service.start_task("task-1")

    await plan_service.create_plan(
        task_id="task-1",
        plan_id="plan-1",
    )
    await plan_service.add_step(
        plan_id="plan-1",
        step_id="step-1",
        title="Implement",
        instruction="Implement one step.",
    )
    await plan_service.activate_plan("plan-1")

    return plans, plan_service


@pytest.mark.asyncio
async def test_bind_execution_session_to_running_step(
    tmp_path,
) -> None:
    plans, service = await build_service(tmp_path)

    await service.start_step("step-1")

    bound = await service.bind_step_execution_session(
        "step-1",
        "coder-session-1",
    )

    assert bound.state is PlanStepState.RUNNING
    assert bound.execution_session_id == "coder-session-1"

    restored = await plans.get_step("step-1")
    assert restored is not None
    assert restored.execution_session_id == "coder-session-1"


@pytest.mark.asyncio
async def test_bind_execution_session_requires_running_step(
    tmp_path,
) -> None:
    plans, service = await build_service(tmp_path)

    with pytest.raises(
        ValueError,
        match="running",
    ):
        await service.bind_step_execution_session(
            "step-1",
            "coder-session-1",
        )

    restored = await plans.get_step("step-1")
    assert restored is not None
    assert restored.state is PlanStepState.PENDING
    assert restored.execution_session_id is None


@pytest.mark.asyncio
async def test_bind_execution_session_rejects_empty_session_id(
    tmp_path,
) -> None:
    _, service = await build_service(tmp_path)

    await service.start_step("step-1")

    with pytest.raises(
        ValueError,
        match="session_id",
    ):
        await service.bind_step_execution_session(
            "step-1",
            "   ",
        )


@pytest.mark.asyncio
async def test_sqlite_plan_store_migrates_legacy_plan_steps_table(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.db"
    now = datetime.now(UTC).isoformat()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE plan_steps (
                step_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                instruction TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO plan_steps (
                step_id,
                plan_id,
                step_index,
                title,
                instruction,
                state,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-step",
                "legacy-plan",
                0,
                "Legacy",
                "Legacy instruction",
                "pending",
                now,
                now,
            ),
        )

    plans = SQLitePlanStore(database_path)
    await plans.initialize()

    restored = await plans.get_step("legacy-step")

    assert restored is not None
    assert restored.execution_session_id is None

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(plan_steps)"
            ).fetchall()
        }

    assert "execution_session_id" in columns
