from __future__ import annotations

import pytest

from core.context import ExecutionContext

from agents.coder_agent import CoderAgent
from agents.coder_stack import CoderAgentStack, build_coder_agent_stack
from integrations.runtime_bridge import RuntimeBridge


@pytest.mark.asyncio
async def test_build_coder_agent_stack_wires_default_components(tmp_path):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")

    assert isinstance(stack, CoderAgentStack)
    assert isinstance(stack.agent, CoderAgent)
    assert isinstance(stack.runtime, RuntimeBridge)

    tools = stack.tool_registry.all()
    assert {tool.name for tool in tools} == {
        "read_file",
        "write_file",
        "run_command",
        "list_directory",
        "find_files",
        "search_text",
        "edit_file",
        "rename_path",
        "delete_path",
        "capture_memory",
    }

    memory_capabilities = await stack.memory.capabilities()
    knowledge_capabilities = await stack.knowledge.capabilities()
    runtime_capabilities = await stack.runtime.capabilities()

    assert memory_capabilities[0].available is True
    assert knowledge_capabilities[0].available is True
    assert runtime_capabilities[0].available is True


@pytest.mark.asyncio
async def test_build_coder_agent_stack_accepts_explicit_sandbox_runtime(tmp_path):
    from core.sandbox_runtime import SandboxRuntime
    from integrations.local_sandbox_backend import LocalSandboxBackend

    sandbox_runtime = SandboxRuntime(
        LocalSandboxBackend(enabled=True)
    )

    stack = await build_coder_agent_stack(
        tmp_path / "agent.db",
        sandbox_runtime=sandbox_runtime,
    )

    run_command = stack.tool_registry.get("run_command")

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        session_id="session-1",
        project_id="project-1",
    )

    result = await run_command.handler(
        context,
        {
            "argv": ["/usr/bin/printf", "sandbox-ok"],
            "timeout": 2,
        },
    )

    assert result["status"] == "ok"
    assert result["stdout"] == "sandbox-ok"


@pytest.mark.asyncio
async def test_coder_agent_stack_cleans_up_tool_sandbox_on_cancel(tmp_path):
    from core.sandbox_runtime import SandboxRuntime
    from integrations.local_sandbox_backend import LocalSandboxBackend

    sandbox_runtime = SandboxRuntime(
        LocalSandboxBackend(enabled=True)
    )
    stack = await build_coder_agent_stack(
        tmp_path / "agent.db",
        sandbox_runtime=sandbox_runtime,
    )

    base_context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        project_id="project-1",
    )
    session_id = await stack.orchestrator.start_session(base_context)

    tool_context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id=session_id,
        project_id="project-1",
    )

    run_command = stack.tool_registry.get("run_command")
    result = await run_command.handler(
        tool_context,
        {
            "argv": ["/usr/bin/printf", "sandbox-ok"],
            "timeout": 2,
        },
    )

    assert result["status"] == "ok"
    assert await sandbox_runtime.status(session_id) is not None

    await stack.orchestrator.cancel(session_id)

    assert await sandbox_runtime.status(session_id) is None
