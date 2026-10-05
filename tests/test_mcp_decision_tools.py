from __future__ import annotations

import pytest

from core.decision import DecisionSeverity
from core.decision_service import DecisionService
from core.project import ProjectRecord
from my_agent_mcp import server
from persistence.sqlite_decision_store import SQLiteDecisionStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


async def build_decision_test_stores(tmp_path):
    database_path = tmp_path / "agent.db"
    projects = SQLiteProjectStore(database_path)
    tasks = SQLiteTaskStore(database_path)
    plans = SQLitePlanStore(database_path)
    decisions = SQLiteDecisionStore(database_path)

    await projects.initialize()
    await tasks.initialize()
    await plans.initialize()
    await decisions.initialize()

    await projects.create(
        ProjectRecord(
            project_id="proj-mcp-1",
            name="MCP Test Project",
            workspace_path=str(tmp_path),
        )
    )
    await projects.set_active("proj-mcp-1")

    return projects, tasks, plans, decisions


def patch_decision_server(monkeypatch, projects, tasks, plans, decisions) -> None:
    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "projects", projects)
    monkeypatch.setattr(server, "tasks", tasks, raising=False)
    monkeypatch.setattr(server, "plans", plans, raising=False)
    monkeypatch.setattr(server, "decisions", decisions, raising=False)
    monkeypatch.setattr(server, "_decision_service", DecisionService(decisions), raising=False)


@pytest.mark.asyncio
async def test_mcp_decision_tools_lifecycle(tmp_path, monkeypatch) -> None:
    projects, tasks, plans, decisions = await build_decision_test_stores(tmp_path)
    patch_decision_server(monkeypatch, projects, tasks, plans, decisions)

    # 1. Create Decision
    opts = [
        {"option_id": "opt-1", "title": "Postgres", "description": "Relational DB", "recommended": True},
        {"option_id": "opt-2", "title": "MongoDB", "description": "Document DB"},
    ]
    created = await server.create_decision(
        task_id="task-123",
        prompt="Select database engine",
        severity="high",
        options=opts,
    )
    assert created["status"] == "created"
    dec_id = created["decision"]["decision_id"]

    # 2. Get Decision
    get_res = await server.get_decision(dec_id)
    assert get_res["status"] == "ok"
    assert get_res["decision"]["decision_id"] == dec_id

    # 3. List Open Decisions
    list_res = await server.list_open_decisions(task_id="task-123")
    assert list_res["status"] == "ok"
    assert len(list_res["decisions"]) == 1

    # 4. Resolve Decision
    resolve_res = await server.resolve_decision(
        decision_id=dec_id,
        selected_option_id="opt-1",
        rationale="We need ACID compliance",
    )
    assert resolve_res["status"] == "resolved"
    assert resolve_res["decision"]["state"] == "resolved"
    assert resolve_res["decision"]["selected_option_id"] == "opt-1"

    # 5. List Open Decisions again (should be 0)
    list_after = await server.list_open_decisions(task_id="task-123")
    assert len(list_after["decisions"]) == 0


@pytest.mark.asyncio
async def test_mcp_front_agent_request_tool(tmp_path, monkeypatch) -> None:
    projects, tasks, plans, decisions = await build_decision_test_stores(tmp_path)
    patch_decision_server(monkeypatch, projects, tasks, plans, decisions)

    res = await server.front_agent_request(
        user_input="Build User Management Service",
        project_id="proj-mcp-1",
    )
    assert res["status"] == "ok"
    assert "response" in res
    assert "Build User Management Service" in res["response"]["message"]
