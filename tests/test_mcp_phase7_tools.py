from __future__ import annotations

import json
from pathlib import Path
import pytest

from my_agent_mcp import server


@pytest.mark.asyncio
async def test_mcp_budget_tools():
    # Set custom budget
    res = await server.set_task_budget(
        task_id="task-budget-1",
        max_cost_usd=15.0,
        max_tokens=200_000,
        max_duration_seconds=300.0,
    )
    assert res["max_cost_usd"] == 15.0
    assert res["max_tokens"] == 200_000
    assert res["is_exceeded"] is False

    # Get budget status
    status = await server.get_task_budget(task_id="task-budget-1")
    assert status["max_cost_usd"] == 15.0
    assert status["remaining_cost_usd"] == 15.0


@pytest.mark.asyncio
async def test_mcp_provider_health_tool():
    health = await server.get_provider_health_status()
    assert "openai" in health
    assert "anthropic" in health
    assert "google" in health
    assert health["openai"]["state"] == "closed"


@pytest.mark.asyncio
async def test_mcp_sbom_and_release_tools(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("pytest>=8.0\n")
    (tmp_path / "app.py").write_text("print('ready')\n")

    sbom = await server.generate_sbom(workspace_path=str(tmp_path), project_name="test-app")
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["total_components"] >= 1

    readiness = await server.check_release_readiness(
        workspace_path=str(tmp_path),
        task_id="task-rel-10",
    )
    assert "is_ready_for_release" in readiness

    attestation = await server.create_release_attestation(
        workspace_path=str(tmp_path),
        task_id="task-rel-10",
        version="v2.0.0",
    )
    assert attestation["task_id"] == "task-rel-10"
    assert attestation["version"] == "v2.0.0"
    assert len(attestation["attestation_signature"]) == 64
