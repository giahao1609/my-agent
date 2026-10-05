from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence

import pytest

from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.orchestrator import Orchestrator
from core.session import SessionState
from core.status import CapabilityStatus
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolPolicy
from core.tool_registry import ToolRegistry


class FakeRuntime:
    async def start(self, context: ExecutionContext) -> str:
        return 'session-1'

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
        return None

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()

    async def _events(self, session_id: str) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type=EventType.TEXT,
            session_id=session_id,
            payload={'text': 'hello'},
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
        return ({'kind': 'memory', 'query': query},)

    async def capture(
        self,
        context: ExecutionContext,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        return None

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()


class FakeSandboxRuntime:
    def __init__(self) -> None:
        self.terminated: list[str] = []

    async def terminate_session(self, session_id: str) -> None:
        self.terminated.append(session_id)


class FakeKnowledge:
    async def search(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]:
        return ({'kind': 'knowledge', 'query': query},)

    async def get_node(
        self,
        context: ExecutionContext,
        node_id: str,
    ) -> Mapping[str, object] | None:
        return None

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()


def make_orchestrator() -> Orchestrator:
    return Orchestrator(
        runtime=FakeRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )


@pytest.mark.asyncio
async def test_orchestrator_wiring() -> None:
    orchestrator = make_orchestrator()
    context = ExecutionContext(workspace_id='workspace-1')

    session_id = await orchestrator.start_session(context)
    memory = await orchestrator.recall(context, 'abc')
    knowledge = await orchestrator.search_knowledge(context, 'xyz')

    assert session_id == 'session-1'
    assert memory[0]['kind'] == 'memory'
    assert knowledge[0]['kind'] == 'knowledge'

    events = [event async for event in orchestrator.stream_events(session_id)]
    assert events[0].type is EventType.TEXT
    assert events[0].payload['text'] == 'hello'


@pytest.mark.asyncio
async def test_orchestrator_tracks_started_session() -> None:
    orchestrator = make_orchestrator()
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await orchestrator.start_session(context)
    session = orchestrator.get_session(session_id)

    assert session.session_id == "session-1"
    assert session.context == context
    assert session.state.value == "running"


@pytest.mark.asyncio
async def test_orchestrator_cancel_stops_tracked_session() -> None:
    orchestrator = make_orchestrator()
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await orchestrator.start_session(context)
    await orchestrator.cancel(session_id)

    assert orchestrator.get_session(session_id).state.value == "stopped"


@pytest.mark.asyncio
async def test_orchestrator_resumes_untracked_session() -> None:
    class ResumeRuntime(FakeRuntime):
        def __init__(self) -> None:
            self.resumed: list[tuple[str, ExecutionContext]] = []

        async def resume(
            self,
            session_id: str,
            context: ExecutionContext,
        ) -> None:
            self.resumed.append((session_id, context))

    runtime = ResumeRuntime()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )
    context = ExecutionContext(workspace_id="workspace-1")

    await orchestrator.resume_session("session-existing", context)

    assert runtime.resumed == [("session-existing", context)]
    session = orchestrator.get_session("session-existing")
    assert session.state.value == "running"
    assert session.context == context


@pytest.mark.asyncio
async def test_orchestrator_does_not_resume_tracked_running_session() -> None:
    class ResumeRuntime(FakeRuntime):
        def __init__(self) -> None:
            self.resumed: list[str] = []

        async def resume(
            self,
            session_id: str,
            context: ExecutionContext,
        ) -> None:
            self.resumed.append(session_id)

    runtime = ResumeRuntime()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await orchestrator.start_session(context)
    await orchestrator.resume_session(session_id, context)

    assert runtime.resumed == []
    assert orchestrator.get_session(session_id).state.value == "running"


@pytest.mark.asyncio
async def test_orchestrator_marks_session_stopped_on_stop_event() -> None:
    class StopRuntime(FakeRuntime):
        async def _events(
            self,
            session_id: str,
        ) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(
                type=EventType.TEXT,
                session_id=session_id,
                payload={"text": "working"},
            )
            yield AgentEvent(
                type=EventType.STOP,
                session_id=session_id,
                payload={"reason": "complete"},
            )

    orchestrator = Orchestrator(
        runtime=StopRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await orchestrator.start_session(context)
    events = [
        event
        async for event in orchestrator.stream_events(session_id)
    ]

    assert [event.type for event in events] == [
        EventType.TEXT,
        EventType.STOP,
    ]
    assert orchestrator.get_session(session_id).state.value == "stopped"


@pytest.mark.asyncio
async def test_orchestrator_enters_waiting_approval_state() -> None:
    class ApprovalTools:
        async def execute(
            self,
            name,
            arguments,
            context,
            *,
            approved=False,
        ):
            return {
                "status": "approval_required",
                "tool": name,
                "reason": "confirmation required",
            }

    orchestrator = Orchestrator(
        runtime=FakeRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ApprovalTools(),
    )
    base_context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(base_context)
    session_context = ExecutionContext(
        workspace_id="workspace-1",
        session_id=session_id,
    )

    result = await orchestrator.execute_tool(
        "dangerous_tool",
        {},
        session_context,
    )

    assert result["status"] == "approval_required"
    assert orchestrator.get_session(session_id).state.value == "waiting_approval"


@pytest.mark.asyncio
async def test_orchestrator_leaves_waiting_approval_after_tool_result() -> None:
    class ApprovalTools:
        async def execute(
            self,
            name,
            arguments,
            context,
            *,
            approved=False,
        ):
            return {
                "status": "approval_required",
                "tool": name,
            }

    orchestrator = Orchestrator(
        runtime=FakeRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ApprovalTools(),
    )
    base_context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(base_context)
    session_context = ExecutionContext(
        workspace_id="workspace-1",
        session_id=session_id,
    )

    await orchestrator.execute_tool(
        "dangerous_tool",
        {},
        session_context,
    )
    await orchestrator.submit_tool_result(
        session_id,
        "call-1",
        {"status": "rejected", "tool": "dangerous_tool"},
        session_context,
    )

    assert orchestrator.get_session(session_id).state.value == "running"


@pytest.mark.asyncio
async def test_orchestrator_marks_session_failed_when_event_stream_fails() -> None:
    class FailingRuntime(FakeRuntime):
        async def _events(
            self,
            session_id: str,
        ) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(
                type=EventType.TEXT,
                session_id=session_id,
                payload={"text": "working"},
            )
            raise RuntimeError("stream failed")

    sandbox = FakeSandboxRuntime()
    orchestrator = Orchestrator(
        runtime=FailingRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
        sandbox_runtime=sandbox,
    )
    context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(context)

    with pytest.raises(RuntimeError, match="stream failed"):
        [
            event
            async for event in orchestrator.stream_events(session_id)
        ]

    assert orchestrator.get_session(session_id).state.value == "failed"
    assert sandbox.terminated == [session_id]


@pytest.mark.asyncio
async def test_orchestrator_marks_session_failed_when_resume_fails() -> None:
    class FailingResumeRuntime(FakeRuntime):
        async def resume(
            self,
            session_id: str,
            context: ExecutionContext,
        ) -> None:
            raise RuntimeError("resume failed")

    sandbox = FakeSandboxRuntime()
    orchestrator = Orchestrator(
        runtime=FailingResumeRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
        sandbox_runtime=sandbox,
    )
    context = ExecutionContext(
        workspace_id="workspace-1",
        session_id="session-existing",
    )

    with pytest.raises(RuntimeError, match="resume failed"):
        await orchestrator.resume_session(
            "session-existing",
            context,
        )

    assert (
        orchestrator.get_session("session-existing").state
        is SessionState.FAILED
    )
    assert sandbox.terminated == ["session-existing"]


@pytest.mark.asyncio
async def test_orchestrator_marks_session_failed_when_cancel_fails() -> None:
    class FailingCancelRuntime(FakeRuntime):
        async def cancel(self, session_id: str) -> None:
            raise RuntimeError("cancel failed")

    sandbox = FakeSandboxRuntime()
    orchestrator = Orchestrator(
        runtime=FailingCancelRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
        sandbox_runtime=sandbox,
    )
    context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(context)

    with pytest.raises(RuntimeError, match="cancel failed"):
        await orchestrator.cancel(session_id)

    assert orchestrator.get_session(session_id).state is SessionState.FAILED
    assert sandbox.terminated == [session_id]


@pytest.mark.asyncio
async def test_orchestrator_marks_session_failed_on_runtime_error_stop() -> None:
    class RuntimeErrorStopRuntime(FakeRuntime):
        async def _events(
            self,
            session_id: str,
        ) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(
                type=EventType.STOP,
                session_id=session_id,
                payload={
                    "reason": "runtime_error",
                    "error_type": "RuntimeError",
                    "message": "model exploded",
                },
            )

    orchestrator = Orchestrator(
        runtime=RuntimeErrorStopRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )
    context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(context)

    events = [
        event
        async for event in orchestrator.stream_events(session_id)
    ]

    assert events[-1].type is EventType.STOP
    assert orchestrator.get_session(session_id).state is SessionState.FAILED


@pytest.mark.asyncio
async def test_orchestrator_switches_runtime_and_model_without_new_session() -> None:
    orchestrator = make_orchestrator()
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await orchestrator.start_session(context)
    original = orchestrator.get_session(session_id)

    await orchestrator.set_execution_target(
        session_id,
        runtime_id="antigravity",
        model_id="anti-model-a",
    )

    assert orchestrator.get_session(session_id) is original
    assert original.runtime_id == "antigravity"
    assert original.model_id == "anti-model-a"
    assert original.state is SessionState.RUNNING

    await orchestrator.set_execution_target(
        session_id,
        runtime_id="codex",
        model_id="codex-model",
    )

    assert orchestrator.get_session(session_id) is original
    assert original.runtime_id == "codex"
    assert original.model_id == "codex-model"
    assert original.state is SessionState.RUNNING


@pytest.mark.asyncio
async def test_orchestrator_can_clear_execution_target() -> None:
    orchestrator = make_orchestrator()
    context = ExecutionContext(workspace_id="workspace-1")

    session_id = await orchestrator.start_session(context)

    await orchestrator.set_execution_target(
        session_id,
        runtime_id="claude",
        model_id="claude-model",
    )
    await orchestrator.set_execution_target(
        session_id,
        runtime_id=None,
        model_id=None,
    )

    session = orchestrator.get_session(session_id)
    assert session.runtime_id is None
    assert session.model_id is None
    assert session.state is SessionState.RUNNING


@pytest.mark.asyncio
async def test_resume_session_restores_durable_execution_target_after_restart(
    tmp_path,
) -> None:
    from core.context import ExecutionContext
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore

    database_path = tmp_path / "agent.db"
    session_store = SQLiteCoderSessionStore(database_path)
    await session_store.initialize()

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
        session_id="session-1",
        project_id="project-1",
    )

    await session_store.save(
        SessionRecord(
            session_id="session-1",
            context=context,
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.RUNNING,
        )
    )

    class ResumeRuntime:
        def __init__(self) -> None:
            self.resumed: list[tuple[str, ExecutionContext]] = []

        async def resume(
            self,
            session_id: str,
            resume_context: ExecutionContext,
        ) -> None:
            self.resumed.append((session_id, resume_context))

    runtime = ResumeRuntime()

    class DummyMemory:
        pass

    class DummyKnowledge:
        pass

    class DummyTools:
        pass

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=DummyMemory(),
        knowledge=DummyKnowledge(),
        tools=DummyTools(),
        session_store=session_store,
    )

    await orchestrator.resume_session("session-1", context)

    restored = orchestrator.get_session("session-1")

    assert runtime.resumed == [("session-1", context)]
    assert restored.session_id == "session-1"
    assert restored.runtime_id == "codex"
    assert restored.model_id == "codex-model"
    assert restored.state is SessionState.RUNNING


@pytest.mark.asyncio
async def test_orchestrator_send_attaches_memory_and_knowledge_context() -> None:
    class CapturingRuntime(FakeRuntime):
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []

        async def send(
            self,
            session_id: str,
            message: str,
            context: ExecutionContext,
            *,
            retrieval_context: Mapping[str, object] | None = None,
        ) -> None:
            self.sent.append(
                {
                    "session_id": session_id,
                    "message": message,
                    "context": context,
                    "retrieval_context": retrieval_context,
                }
            )

    runtime = CapturingRuntime()
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
    )
    context = ExecutionContext(
        workspace_id="workspace-1",
        project_id="project-1",
    )

    session_id = await orchestrator.start_session(context)
    await orchestrator.send(
        session_id,
        "continue the implementation",
        context,
    )

    assert len(runtime.sent) == 1
    sent = runtime.sent[0]
    assert sent["message"] == "continue the implementation"
    assert sent["retrieval_context"] == {
        "memory": [
            {
                "kind": "memory",
                "query": "continue the implementation",
            }
        ],
        "knowledge": [
            {
                "kind": "knowledge",
                "query": "continue the implementation",
            }
        ],
    }


@pytest.mark.asyncio
async def test_orchestrator_terminates_sandbox_on_cancel() -> None:
    class RecordingSandboxRuntime:
        def __init__(self) -> None:
            self.terminated: list[str] = []

        async def terminate_session(self, session_id: str) -> None:
            self.terminated.append(session_id)

    sandbox = RecordingSandboxRuntime()
    orchestrator = Orchestrator(
        runtime=FakeRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
        sandbox_runtime=sandbox,
    )

    context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(context)

    await orchestrator.cancel(session_id)

    assert sandbox.terminated == [session_id]


@pytest.mark.asyncio
async def test_orchestrator_terminates_sandbox_on_stop_event() -> None:
    class StopRuntime(FakeRuntime):
        async def _events(
            self,
            session_id: str,
        ) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(
                type=EventType.STOP,
                session_id=session_id,
                payload={"reason": "complete"},
            )

    class RecordingSandboxRuntime:
        def __init__(self) -> None:
            self.terminated: list[str] = []

        async def terminate_session(self, session_id: str) -> None:
            self.terminated.append(session_id)

    sandbox = RecordingSandboxRuntime()
    orchestrator = Orchestrator(
        runtime=StopRuntime(),
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(ToolRegistry(), ToolPolicy()),
        sandbox_runtime=sandbox,
    )

    context = ExecutionContext(workspace_id="workspace-1")
    session_id = await orchestrator.start_session(context)

    events = [
        event
        async for event in orchestrator.stream_events(session_id)
    ]

    assert events[-1].type is EventType.STOP
    assert sandbox.terminated == [session_id]


@pytest.mark.asyncio
async def test_cancel_restores_and_stops_durable_session_after_restart(
    tmp_path,
) -> None:
    from core.context import ExecutionContext
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore

    database_path = tmp_path / "agent.db"
    session_store = SQLiteCoderSessionStore(database_path)
    await session_store.initialize()

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
        session_id="session-1",
        task_id="task-1",
        project_id="project-1",
    )

    await session_store.save(
        SessionRecord(
            session_id="session-1",
            context=context,
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.RUNNING,
        )
    )

    class CancelRuntime:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def cancel(self, session_id: str) -> None:
            self.cancelled.append(session_id)

    runtime = CancelRuntime()

    class DummyMemory:
        pass

    class DummyKnowledge:
        pass

    class DummyTools:
        pass

    # Simulates a fresh process: no session exists in orchestrator._sessions.
    orchestrator = Orchestrator(
        runtime=runtime,
        memory=DummyMemory(),
        knowledge=DummyKnowledge(),
        tools=DummyTools(),
        session_store=session_store,
    )

    await orchestrator.cancel("session-1")

    restored = await session_store.get("session-1")

    assert runtime.cancelled == ["session-1"]
    assert restored is not None
    assert restored.state is SessionState.STOPPED


@pytest.mark.asyncio
async def test_cancel_failure_after_restart_persists_failed_state(
    tmp_path,
) -> None:
    from core.context import ExecutionContext
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_coder_session_store import SQLiteCoderSessionStore

    database_path = tmp_path / "agent.db"
    session_store = SQLiteCoderSessionStore(database_path)
    await session_store.initialize()

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
        session_id="session-1",
        task_id="task-1",
        project_id="project-1",
    )

    await session_store.save(
        SessionRecord(
            session_id="session-1",
            context=context,
            state=SessionState.RUNNING,
        )
    )

    class FailingCancelRuntime:
        async def cancel(self, session_id: str) -> None:
            raise RuntimeError("cancel failed")

    class DummyMemory:
        pass

    class DummyKnowledge:
        pass

    class DummyTools:
        pass

    orchestrator = Orchestrator(
        runtime=FailingCancelRuntime(),
        memory=DummyMemory(),
        knowledge=DummyKnowledge(),
        tools=DummyTools(),
        session_store=session_store,
    )

    with pytest.raises(RuntimeError, match="cancel failed"):
        await orchestrator.cancel("session-1")

    restored = await session_store.get("session-1")

    assert restored is not None
    assert restored.state is SessionState.FAILED
