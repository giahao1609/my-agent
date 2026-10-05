from __future__ import annotations

import pytest

from core.project import ProjectRecord
from my_agent_mcp.server import (
    _initialize,
    complete_agent_run,
    fail_agent_run,
    get_agent_handoff_context,
    get_agent_run,
    list_agent_runs,
    projects,
    start_agent_run,
)


@pytest.mark.asyncio
async def test_mcp_agent_run_tools_lifecycle():
    await _initialize()

    # Ensure active project
    active = await projects.get_active()
    if active is None:
        proj = ProjectRecord(
            project_id="my-agent",
            name="my-agent",
            workspace_path="/Users/haohg/Project/my-agent",
        )
        await projects.create(proj)
        await projects.set_active("my-agent")

    # 1. Start agent run
    start_res = await start_agent_run(
        task_id="task-mcp-run-1",
        agent_id="antigravity",
        execution_role="backend_coder",
        step_id="step-mcp-1",
        model_id="gemini-3.7-flash",
    )
    assert start_res["status"] == "started"
    run_dict = start_res["run"]
    run_id = run_dict["run_id"]
    assert run_dict["state"] == "running"

    # 2. Get agent run
    get_res = await get_agent_run(run_id)
    assert get_res["status"] == "ok"
    assert get_res["run"]["run_id"] == run_id

    # 3. Complete agent run
    complete_res = await complete_agent_run(
        run_id=run_id,
        result={
            "run_id": run_id,
            "status": "completed",
            "summary": "Completed MCP agent run test",
            "changed_files": [{"path": "server.py", "change_type": "modified"}],
            "created_files": ["new_tool.py"],
            "tests": [{"passed": 2, "failed": 0, "summary": "2 tests passed"}],
            "remaining_work": ["Deploy to staging"],
        },
    )
    assert complete_res["status"] == "completed"
    assert complete_res["run"]["state"] == "completed"
    assert "Bàn Giao Nhiệm Vụ" in complete_res["handoff_summary"]

    # 4. List agent runs for step
    list_res = await list_agent_runs(step_id="step-mcp-1")
    assert list_res["status"] == "ok"
    assert len(list_res["runs"]) >= 1

    # 5. Get agent handoff context for next role
    handoff_res = await get_agent_handoff_context(
        task_id="task-mcp-run-1",
        target_role="tester",
        step_id="step-mcp-1",
    )
    assert handoff_res["status"] == "ok"
    assert "server.py" in handoff_res["handoff_context"]["changed_files"]
    assert "new_tool.py" in handoff_res["handoff_context"]["created_files"]
    assert "Ngữ Cảnh Bàn Giao Từ Lượt Thực Thi Trước" in handoff_res["prompt_brief"]
