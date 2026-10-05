from __future__ import annotations

import pytest

from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from integrations.runtime_bridge import RuntimeBridge, RuntimeCommandType


@pytest.mark.asyncio
async def test_runtime_bridge_moves_commands_and_events_bidirectionally():
    runtime = RuntimeBridge()
    context = ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        agent_id="coder",
    )

    session_id = await runtime.start(context)

    start = await runtime.next_command(session_id)
    assert start.type is RuntimeCommandType.START
    assert start.session_id == session_id

    await runtime.send(session_id, "inspect code", context)
    message = await runtime.next_command(session_id)
    assert message.type is RuntimeCommandType.MESSAGE
    assert message.payload["message"] == "inspect code"

    await runtime.publish_event(
        AgentEvent(
            type=EventType.TEXT,
            session_id=session_id,
            payload={"text": "working"},
        )
    )
    await runtime.publish_event(
        AgentEvent(
            type=EventType.STOP,
            session_id=session_id,
            payload={"reason": "complete"},
        )
    )

    events = [
        event
        async for event in runtime.stream_events(session_id)
    ]

    assert [event.type for event in events] == [
        EventType.TEXT,
        EventType.STOP,
    ]


@pytest.mark.asyncio
async def test_runtime_bridge_forwards_tool_results():
    runtime = RuntimeBridge()
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await runtime.start(context)
    await runtime.next_command(session_id)

    await runtime.submit_tool_result(
        session_id,
        "call-1",
        {"status": "ok", "value": 42},
        context,
    )

    command = await runtime.next_command(session_id)

    assert command.type is RuntimeCommandType.TOOL_RESULT
    assert command.payload == {
        "tool_call_id": "call-1",
        "result": {"status": "ok", "value": 42},
    }


@pytest.mark.asyncio
async def test_runtime_bridge_reports_ready_capability():
    runtime = RuntimeBridge()

    capabilities = await runtime.capabilities()

    assert len(capabilities) == 1
    assert capabilities[0].name == "runtime_bridge"
    assert capabilities[0].available is True


@pytest.mark.asyncio
async def test_runtime_bridge_forwards_retrieval_context_with_message():
    runtime = RuntimeBridge()
    context = ExecutionContext(
        workspace_id="workspace-1",
        project_id="project-1",
    )

    session_id = await runtime.start(context)
    await runtime.next_command(session_id)

    retrieval_context = {
        "memory": [
            {
                "kind": "preference",
                "content": "Prefer small focused changes.",
                "importance": 0.9,
            }
        ],
        "knowledge": [
            {
                "node_id": "node-1",
                "kind": "function",
                "name": "run",
                "path": "agents/coder_agent.py",
            }
        ],
    }

    await runtime.send(
        session_id,
        "continue the implementation",
        context,
        retrieval_context=retrieval_context,
    )

    command = await runtime.next_command(session_id)

    assert command.type is RuntimeCommandType.MESSAGE
    assert command.payload["message"] == "continue the implementation"
    assert command.payload["retrieval_context"] == retrieval_context
