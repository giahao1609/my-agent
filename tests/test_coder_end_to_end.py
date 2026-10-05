from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from agents.coder_agent import CoderAgent
from agents.coder_runtime_worker import CoderRuntimeWorker
from core.context import ExecutionContext
from core.events import EventType
from core.model import ModelMessage, ModelToolCall, ModelTurn
from core.orchestrator import Orchestrator
from core.status import Availability, CapabilityStatus
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolPolicy
from integrations.runtime_bridge import RuntimeBridge
from tools.coder_tools import build_coder_tool_registry


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
        return (
            CapabilityStatus(
                name="fake_memory",
                state=Availability.READY,
            ),
        )


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
        return (
            CapabilityStatus(
                name="fake_knowledge",
                state=Availability.READY,
            ),
        )


class ReadFileModel:
    def __init__(self) -> None:
        self.calls: list[tuple[ModelMessage, ...]] = []

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
    ) -> ModelTurn:
        self.calls.append(tuple(messages))

        if len(self.calls) == 1:
            return ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        tool_call_id="call-read",
                        name="read_file",
                        arguments={"path": "app.py"},
                    ),
                ),
            )

        tool_message = messages[-1]
        assert tool_message.role == "tool"
        assert tool_message.tool_call_id == "call-read"
        assert "hello from app" in tool_message.content

        return ModelTurn(
            text="I inspected app.py successfully.",
            stop=True,
        )


@pytest.mark.asyncio
async def test_coder_agent_runs_real_tool_loop_end_to_end(tmp_path) -> None:
    (tmp_path / "app.py").write_text(
        'print("hello from app")\n',
        encoding="utf-8",
    )

    runtime = RuntimeBridge()
    registry = build_coder_tool_registry()

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(
            registry,
            ToolPolicy(),
        ),
    )
    agent = CoderAgent(orchestrator)
    model = ReadFileModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
    )

    session_id = await orchestrator.start_session(context)
    worker_task = asyncio.create_task(
        worker.run_session(session_id)
    )

    events = [
        event
        async for event in agent.run_session(
            session_id,
            "Inspect app.py",
            context,
        )
    ]

    await worker_task

    assert [event.type for event in events] == [
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.TEXT,
        EventType.STOP,
    ]

    tool_result = events[1].payload["result"]
    assert tool_result["status"] == "ok"
    assert tool_result["path"] == "app.py"
    assert 'hello from app' in tool_result["content"]

    assert events[2].payload["text"] == (
        "I inspected app.py successfully."
    )
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_coder_agent_repairs_code_after_failed_test(tmp_path) -> None:
    import sys

    (tmp_path / "app.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    test_argv = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "assert Path('app.py').read_text(encoding='utf-8') "
            "== 'VALUE = 2\\n'"
        ),
    ]

    class RepairModel:
        def __init__(self) -> None:
            self.calls: list[tuple[ModelMessage, ...]] = []

        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
        ) -> ModelTurn:
            self.calls.append(tuple(messages))

            if len(self.calls) == 1:
                return ModelTurn(
                    tool_calls=(
                        ModelToolCall(
                            tool_call_id="test-1",
                            name="run_command",
                            arguments={"argv": test_argv},
                        ),
                    ),
                )

            if len(self.calls) == 2:
                result = messages[-1].content
                assert '"status": "error"' in result

                return ModelTurn(
                    tool_calls=(
                        ModelToolCall(
                            tool_call_id="edit-1",
                            name="edit_file",
                            arguments={
                                "path": "app.py",
                                "old_text": "VALUE = 1",
                                "new_text": "VALUE = 2",
                            },
                        ),
                    ),
                )

            if len(self.calls) == 3:
                assert '"status": "ok"' in messages[-1].content

                return ModelTurn(
                    tool_calls=(
                        ModelToolCall(
                            tool_call_id="test-2",
                            name="run_command",
                            arguments={"argv": test_argv},
                        ),
                    ),
                )

            assert '"status": "ok"' in messages[-1].content

            return ModelTurn(
                text="Fixed app.py and the test now passes.",
                stop=True,
            )

    from core.sandbox_runtime import SandboxRuntime
    from integrations.local_sandbox_backend import LocalSandboxBackend

    runtime = RuntimeBridge()
    sandbox_runtime = SandboxRuntime(
        LocalSandboxBackend(enabled=True)
    )
    registry = build_coder_tool_registry(
        sandbox_runtime=sandbox_runtime,
    )

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(
            registry,
            ToolPolicy(),
        ),
    )

    agent = CoderAgent(orchestrator)
    model = RepairModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
    )

    session_id = await orchestrator.start_session(context)
    worker_task = asyncio.create_task(
        worker.run_session(session_id)
    )

    events = [
        event
        async for event in agent.run_session(
            session_id,
            "Make the test pass.",
            context,
        )
    ]

    await worker_task

    assert (tmp_path / "app.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"

    assert [event.type for event in events] == [
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.TOOL_USE,
        EventType.TOOL_RESULT,
        EventType.TEXT,
        EventType.STOP,
    ]

    assert events[1].payload["result"]["status"] == "error"
    assert events[3].payload["result"]["status"] == "ok"
    assert events[5].payload["result"]["status"] == "ok"
    assert len(model.calls) == 4


@pytest.mark.asyncio
async def test_coder_agent_hot_switches_execution_target_in_same_session(
    tmp_path,
) -> None:
    from core.model import ExecutionTarget

    class SwitchingModel:
        def __init__(self) -> None:
            self.calls: list[
                tuple[
                    tuple[ModelMessage, ...],
                    ExecutionTarget,
                ]
            ] = []

        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
            *,
            target: ExecutionTarget,
        ) -> ModelTurn:
            self.calls.append(
                (
                    tuple(messages),
                    target,
                )
            )

            return ModelTurn(
                text=f"using {target.runtime_id}:{target.model_id}",
                stop=False,
            )

    runtime = RuntimeBridge()
    registry = build_coder_tool_registry()

    orchestrator = Orchestrator(
        runtime=runtime,
        memory=FakeMemory(),
        knowledge=FakeKnowledge(),
        tools=ToolExecutor(
            registry,
            ToolPolicy(),
        ),
    )

    agent = CoderAgent(orchestrator)
    model = SwitchingModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=registry,
    )

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
    )

    session_id = await orchestrator.start_session(context)
    worker_task = asyncio.create_task(
        worker.run_session(session_id)
    )

    await orchestrator.set_execution_target(
        session_id,
        runtime_id="legacy-runtime",
        model_id="legacy-model-a",
    )

    first_events = agent.run_session(
        session_id,
        "Start the task",
        context,
    )
    first = await asyncio.wait_for(
        anext(first_events),
        timeout=1,
    )

    assert first.type is EventType.TEXT
    assert first.payload["text"] == "using legacy-runtime:legacy-model-a"

    await orchestrator.set_execution_target(
        session_id,
        runtime_id="codex",
        model_id="codex-model",
    )

    await orchestrator.send(
        session_id,
        "Continue the same task",
        ExecutionContext(
            workspace_id=str(tmp_path),
            user_id="user-1",
            agent_id="coder",
            session_id=session_id,
        ),
    )

    second = await asyncio.wait_for(
        anext(first_events),
        timeout=1,
    )

    assert second.type is EventType.TEXT
    assert second.payload["text"] == "using codex:codex-model"

    await orchestrator.cancel(session_id)
    stop = await asyncio.wait_for(
        anext(first_events),
        timeout=1,
    )
    assert stop.type is EventType.STOP

    await worker_task

    assert len(model.calls) == 2

    first_messages, first_target = model.calls[0]
    second_messages, second_target = model.calls[1]

    assert first_target == ExecutionTarget(
        runtime_id="legacy-runtime",
        model_id="legacy-model-a",
    )
    assert second_target == ExecutionTarget(
        runtime_id="codex",
        model_id="codex-model",
    )

    assert [
        message.content
        for message in second_messages
        if message.role == "user"
    ] == [
        "Start the task",
        "Continue the same task",
    ]

    assert orchestrator.get_session(session_id).runtime_id == "codex"
    assert orchestrator.get_session(session_id).model_id == "codex-model"
