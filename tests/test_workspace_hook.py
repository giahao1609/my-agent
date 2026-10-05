from __future__ import annotations

import json

import pytest

from my_agent_mcp import workspace_hook


@pytest.mark.asyncio
async def test_workspace_hook_injects_continuity_protocol(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    class FakeProjects:
        async def initialize(self) -> None:
            return None

        async def list(self):
            return ()

        async def create(self, project) -> None:
            return None

        async def set_active(self, project_id: str):
            return None

    class FakeCheckpoints:
        async def initialize(self) -> None:
            return None

        async def latest(self, project_id: str):
            return None

    class FakeCodeGraph:
        async def initialize(self) -> None:
            return None

        async def get_graph_status(self, project_id: str):
            return {"status": "not_indexed"}

    monkeypatch.setattr(workspace_hook, "projects", FakeProjects())
    monkeypatch.setattr(workspace_hook, "checkpoints", FakeCheckpoints())
    monkeypatch.setattr(workspace_hook, "code_graph", FakeCodeGraph())

    payload = {
        "workspacePaths": [str(workspace)],
        "invocationNum": 0,
    }

    monkeypatch.setattr(
        workspace_hook.sys,
        "stdin",
        type(
            "FakeStdin",
            (),
            {"read": lambda self: json.dumps(payload)},
        )(),
    )

    await workspace_hook.main()

    output = json.loads(capsys.readouterr().out)
    message = output["injectSteps"][0]["ephemeralMessage"]

    assert "YOU ARE ALWAYS MYAGENT" in message
    assert "STRICT NO-EMOJI RULE" in message
    assert "MyAgent owns coding-task continuity" in message
    assert "Antigravity, Codex, and Claude" in message
    assert "resume_project" in message
    assert "get_latest_resumable_coder_session" in message
    assert "handoff_coder_session" in message
    assert "next_coder_command" in message
    assert "set_coder_execution_target" in message
