from __future__ import annotations

import pytest
from my_agent_mcp import server


@pytest.mark.asyncio
async def test_mcp_get_allowed_handoff_targets():
    planner_targets = await server.get_allowed_handoff_targets("planner")
    assert "backend_coder" in planner_targets
    assert "architect" in planner_targets
    assert "release" not in planner_targets

    coder_targets = await server.get_allowed_handoff_targets("backend_coder")
    assert "tester" in coder_targets
    assert "reviewer" in coder_targets
    assert "release" not in coder_targets


@pytest.mark.asyncio
async def test_mcp_request_agent_handoff_flow():
    # Valid handoff request
    res = await server.request_agent_handoff(
        source_role="backend_coder",
        target_role="tester",
        task_id="mcp-task-1",
        payload={"diff_summary": "Added payment validation endpoint"},
        reason="Code completed, handing off for automated tests",
    )
    assert res["status"] == "accepted"
    assert res["source_role"] == "backend_coder"
    assert res["target_role"] == "tester"

    # Query handoff history
    history = await server.get_handoff_history(task_id="mcp-task-1")
    assert len(history) >= 1
    assert history[-1]["task_id"] == "mcp-task-1"


@pytest.mark.asyncio
async def test_mcp_request_agent_handoff_rejected():
    # Unauthorized bypass attempt
    res = await server.request_agent_handoff(
        source_role="backend_coder",
        target_role="release",
        task_id="mcp-task-hack",
        reason="Direct release attempt without review",
    )
    assert res["status"] == "rejected"
    assert len(res["reasons"]) > 0
