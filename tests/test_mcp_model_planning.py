from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.plan import PlanState
from core.planner import PlanProposal, PlanStepProposal
from core.project import ProjectRecord
from core.task import TaskState
from core.task_service import TaskService
from my_agent_mcp import server
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_stores(tmp_path):
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

    await task_service.create_task(
        project_id="project-1",
        task_id="task-1",
        objective="Implement generated planning",
    )
    await task_service.start_task("task-1")

    return projects, tasks, plans


def patch_server(
    monkeypatch,
    *,
    projects,
    tasks,
    plans,
    planner,
) -> None:
    async def fake_initialize() -> None:
        return None

    async def fake_get_model_composition():
        return SimpleNamespace(
            planner=planner,
        )

    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "projects",
        projects,
    )
    monkeypatch.setattr(
        server,
        "tasks",
        tasks,
    )
    monkeypatch.setattr(
        server,
        "plans",
        plans,
    )
    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )


@pytest.mark.asyncio
async def test_mcp_propose_task_plan_materializes_model_proposal(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)

    class FakePlanner:
        def __init__(self) -> None:
            self.calls = []

        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            self.calls.append((task, context))

            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="Inspect",
                        instruction="Inspect relevant code.",
                    ),
                    PlanStepProposal(
                        title="Implement",
                        instruction="Implement the change.",
                    ),
                )
            )

    planner = FakePlanner()

    patch_server(
        monkeypatch,
        projects=projects,
        tasks=tasks,
        plans=plans,
        planner=planner,
    )

    result = await server.propose_task_plan(
        task_id="task-1",
        plan_id="plan-1",
        project_context="Project summary",
        code_context="Relevant symbols",
    )

    assert result["status"] == "proposed"

    assert result["plan"]["plan_id"] == "plan-1"
    assert result["plan"]["state"] == PlanState.DRAFT.value
    assert result["plan"]["revision"] == 1

    assert len(result["steps"]) == 2
    assert [
        step["step_index"]
        for step in result["steps"]
    ] == [0, 1]
    assert [
        step["title"]
        for step in result["steps"]
    ] == [
        "Inspect",
        "Implement",
    ]

    assert len(planner.calls) == 1

    task_arg, context_arg = planner.calls[0]

    assert task_arg.task_id == "task-1"
    assert context_arg.workspace_id == str(tmp_path)
    assert context_arg.project_context == "Project summary"
    assert context_arg.code_context == "Relevant symbols"

    task = await tasks.get("task-1")

    assert task is not None

    # Proposal creation must not activate execution.
    assert task.state is TaskState.PLANNING
    assert task.active_plan_id is None


@pytest.mark.asyncio
async def test_mcp_propose_task_plan_rejects_unknown_task_before_model(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)

    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            raise AssertionError(
                "planner must not be called"
            )

    patch_server(
        monkeypatch,
        projects=projects,
        tasks=tasks,
        plans=plans,
        planner=FakePlanner(),
    )

    with pytest.raises(
        KeyError,
        match="unknown task",
    ):
        await server.propose_task_plan(
            task_id="missing",
            plan_id="plan-1",
        )

    assert await plans.get_plan("plan-1") is None


@pytest.mark.asyncio
async def test_mcp_propose_task_plan_generates_plan_id(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)

    class FakePlanner:
        async def propose(
            self,
            task,
            *,
            context,
        ) -> PlanProposal:
            return PlanProposal(
                steps=(
                    PlanStepProposal(
                        title="Inspect",
                        instruction="Inspect.",
                    ),
                )
            )

    patch_server(
        monkeypatch,
        projects=projects,
        tasks=tasks,
        plans=plans,
        planner=FakePlanner(),
    )

    result = await server.propose_task_plan(
        task_id="task-1",
    )

    plan_id = result["plan"]["plan_id"]

    assert plan_id.startswith("plan-")
    assert result["steps"][0]["plan_id"] == plan_id


@pytest.mark.asyncio
async def test_mcp_propose_task_plan_rejects_terminal_task_before_model(
    tmp_path,
    monkeypatch,
) -> None:
    projects, tasks, plans = await build_stores(tmp_path)

    task = await tasks.get("task-1")
    assert task is not None

    task.transition(TaskState.CANCELLED)
    await tasks.save(task)

    async def fail_if_model_touched():
        raise AssertionError(
            "model composition must not be loaded for terminal task"
        )

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "projects",
        projects,
    )
    monkeypatch.setattr(
        server,
        "tasks",
        tasks,
    )
    monkeypatch.setattr(
        server,
        "plans",
        plans,
    )
    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fail_if_model_touched,
    )

    with pytest.raises(
        ValueError,
        match="terminal task",
    ):
        await server.propose_task_plan(
            task_id="task-1",
            plan_id="plan-1",
        )

    assert await plans.get_plan("plan-1") is None
