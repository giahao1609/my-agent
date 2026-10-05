from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence

import pytest

from agents.coder_agent import CoderAgent
from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.orchestrator import Orchestrator
from core.status import CapabilityStatus
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolPolicy
from core.tool_registry import ToolRegistry
from core.tools import ToolDefinition


class FakeRuntime:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str, Mapping[str, object]]] = []

    async def start(self, context: ExecutionContext) -> str:
        return "session-1"

    async def resume(self, session_id: str, context: ExecutionContext) -> None:
        return None

    async def send(
        self,
        session_id: str,
        message: str,
        context: ExecutionContext,
    ) -> None:
        return None

    async def cancel(self, session_id: str) -> None:
        return None

    async def set_execution_target(
        self,
        session_id: str,
        *,
        runtime_id: str | None,
        model_id: str | None,
        history: tuple[Mapping[str, object], ...] | None = None,
    ) -> None:
        return None


    async def submit_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        result: Mapping[str, object],
        context: ExecutionContext,
    ) -> None:
        self.submitted.append((session_id, tool_call_id, result))

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()

    async def _events(self, session_id: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type=EventType.TOOL_USE,
            session_id=session_id,
            payload={
                "tool_call_id": "call-1",
                "name": "read_value",
                "arguments": {"value": 42},
            },
        )
        yield AgentEvent(
            type=EventType.STOP,
            session_id=session_id,
            payload={"reason": "complete"},
        )

    def stream_events(self, session_id: str) -> AsyncIterator[AgentEvent]:
        return self._events(session_id)


class FakeMemory:
    async def recall(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]:
        return ()

    async def capture(
        self,
        context: ExecutionContext,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        return None

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()


class FakeKnowledge:
    async def search(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]:
        return ()

    async def get_node(
        self,
        context: ExecutionContext,
        node_id: str,
    ) -> Mapping[str, object] | None:
        return None

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()


@pytest.mark.asyncio
async def test_coder_agent_executes_tool_and_submits_result():
    async def read_value(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return {"status": "ok", "value": arguments["value"]}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_value",
            description="Read a test value.",
            input_schema={"type": "object"},
            handler=read_value,
        )
    )

    runtime = FakeRuntime()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)

    context = ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        agent_id="coder",
    )

    events = [
        event
        async for event in agent.run(
            "inspect the value",
            context,
        )
    ]

    assert [event.type for event in events] == [
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.STOP,
    ]
    assert runtime.submitted == [
        (
            "session-1",
            "call-1",
            {"status": "ok", "value": 42},
        )
    ]


@pytest.mark.asyncio
async def test_coder_agent_emits_tool_confirm_for_approval_required():
    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return {"status": "ok"}

    from core.tools import ToolPermission

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_value",
            description="Delete a test value.",
            input_schema={"type": "object"},
            handler=destructive_action,
            permissions=frozenset({ToolPermission.DESTRUCTIVE}),
        )
    )

    runtime = FakeRuntime()

    async def events(session_id: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type=EventType.TOOL_USE,
            session_id=session_id,
            payload={
                "tool_call_id": "call-1",
                "name": "delete_value",
                "arguments": {},
            },
        )
        yield AgentEvent(
            type=EventType.STOP,
            session_id=session_id,
            payload={"reason": "complete"},
        )

    runtime._events = events

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)

    events_seen = [
        event
        async for event in agent.run(
            "delete the value",
            ExecutionContext(
                workspace_id="workspace-1",
                user_id="user-1",
                agent_id="coder",
            ),
        )
    ]

    assert [event.type for event in events_seen] == [
        EventType.TOOL_USE,
        EventType.TOOL_CONFIRM,
        EventType.STOP,
    ]
    assert runtime.submitted == []


@pytest.mark.asyncio
async def test_coder_agent_submits_tool_errors_without_crashing():
    async def failing_tool(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="failing_tool",
            description="Fail for testing.",
            input_schema={"type": "object"},
            handler=failing_tool,
        )
    )

    runtime = FakeRuntime()

    async def events(session_id: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type=EventType.TOOL_USE,
            session_id=session_id,
            payload={
                "tool_call_id": "call-1",
                "name": "failing_tool",
                "arguments": {},
            },
        )
        yield AgentEvent(
            type=EventType.STOP,
            session_id=session_id,
            payload={"reason": "complete"},
        )

    runtime._events = events

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)

    events_seen = [
        event
        async for event in agent.run(
            "run the failing tool",
            ExecutionContext(
                workspace_id="workspace-1",
                user_id="user-1",
                agent_id="coder",
            ),
        )
    ]

    assert [event.type for event in events_seen] == [
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.STOP,
    ]
    assert runtime.submitted[0][2] == {
        "status": "error",
        "error_type": "RuntimeError",
        "message": "boom",
    }


@pytest.mark.asyncio
async def test_coder_agent_runs_existing_session_without_starting_new_one():
    class ExistingSessionRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.resumed: list[str] = []

        async def start(self, context: ExecutionContext) -> str:
            raise AssertionError("run_session must not start a new session")

        async def resume(
            self,
            session_id: str,
            context: ExecutionContext,
        ) -> None:
            self.resumed.append(session_id)

    runtime = ExistingSessionRuntime()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)

    events = [
        event
        async for event in agent.run_session(
            "session-existing",
            "continue working",
            ExecutionContext(
                workspace_id="workspace-1",
                user_id="user-1",
                agent_id="coder",
            ),
        )
    ]

    assert [event.type for event in events] == [
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.STOP,
    ]
    assert runtime.resumed == ["session-existing"]


@pytest.mark.asyncio
async def test_coder_agent_submits_tool_result_after_approval():
    from core.tools import ToolPermission

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {"status": "ok", "deleted": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_value",
            description="Delete a test value.",
            input_schema={"type": "object"},
            handler=destructive_action,
            permissions=frozenset({ToolPermission.DESTRUCTIVE}),
        )
    )

    runtime = FakeRuntime()

    async def events(session_id: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type=EventType.TOOL_USE,
            session_id=session_id,
            payload={
                "tool_call_id": "call-approval",
                "name": "delete_value",
                "arguments": {"value": 42},
            },
        )
        yield AgentEvent(
            type=EventType.STOP,
            session_id=session_id,
            payload={"reason": "complete"},
        )

    runtime._events = events

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)

    events_seen = [
        event
        async for event in agent.run(
            "delete the value",
            ExecutionContext(
                workspace_id="workspace-1",
                user_id="user-1",
                agent_id="coder",
            ),
        )
    ]

    assert [event.type for event in events_seen] == [
        EventType.TOOL_USE,
        EventType.TOOL_CONFIRM,
        EventType.STOP,
    ]
    assert calls == []
    assert runtime.submitted == []

    result_event = await agent.resolve_tool_approval(
        "session-1",
        "call-approval",
        approved=True,
    )

    assert result_event.type is EventType.TOOL_RESULT
    assert result_event.payload["result"] == {
        "status": "ok",
        "deleted": True,
    }
    assert calls == [{"value": 42}]
    assert runtime.submitted == [
        (
            "session-1",
            "call-approval",
            {"status": "ok", "deleted": True},
        )
    ]


@pytest.mark.asyncio
async def test_coder_agent_submits_rejected_result_after_approval_denied():
    from core.tools import ToolPermission

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {"status": "ok"}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_value",
            description="Delete a test value.",
            input_schema={"type": "object"},
            handler=destructive_action,
            permissions=frozenset({ToolPermission.DESTRUCTIVE}),
        )
    )

    runtime = FakeRuntime()

    async def events(session_id: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type=EventType.TOOL_USE,
            session_id=session_id,
            payload={
                "tool_call_id": "call-rejected",
                "name": "delete_value",
                "arguments": {"value": 42},
            },
        )
        yield AgentEvent(
            type=EventType.STOP,
            session_id=session_id,
            payload={"reason": "complete"},
        )

    runtime._events = events

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(registry, ToolPolicy()),
    )
    agent = CoderAgent(orchestrator)

    events_seen = [
        event
        async for event in agent.run(
            "delete the value",
            ExecutionContext(
                workspace_id="workspace-1",
                user_id="user-1",
                agent_id="coder",
            ),
        )
    ]

    assert [event.type for event in events_seen] == [
        EventType.TOOL_USE,
        EventType.TOOL_CONFIRM,
        EventType.STOP,
    ]
    assert runtime.submitted == []
    assert calls == []

    result_event = await agent.resolve_tool_approval(
        "session-1",
        "call-rejected",
        approved=False,
    )

    assert result_event.type is EventType.TOOL_RESULT
    assert result_event.payload["result"] == {
        "status": "rejected",
        "tool": "delete_value",
    }
    assert calls == []
    assert runtime.submitted == [
        (
            "session-1",
            "call-rejected",
            {
                "status": "rejected",
                "tool": "delete_value",
            },
        )
    ]
