from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from agents.coder_runtime_worker import CoderRuntimeWorker
from core.context import ExecutionContext
from core.events import EventType
from core.model import ModelMessage, ModelToolCall, ModelTurn
from integrations.runtime_bridge import RuntimeBridge
from tools.coder_tools import build_coder_tool_registry


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                tuple[ModelMessage, ...],
                tuple[Mapping[str, object], ...],
                ExecutionContext,
            ]
        ] = []

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
    ) -> ModelTurn:
        self.calls.append(
            (
                tuple(messages),
                tuple(tools),
                context,
            )
        )

        if len(self.calls) == 1:
            return ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        tool_call_id="call-1",
                        name="read_file",
                        arguments={"path": "app.py"},
                    ),
                ),
            )

        return ModelTurn(
            text="Finished inspecting the file.",
            stop=True,
        )


@pytest.mark.asyncio
async def test_coder_runtime_worker_runs_model_tool_loop() -> None:
    runtime = RuntimeBridge()
    model = FakeModel()
    tools = build_coder_tool_registry()

    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=tools,
    )

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
    )
    session_id = await runtime.start(context)

    worker_task = asyncio.create_task(
        worker.run_session(session_id)
    )

    await runtime.send(
        session_id,
        "Inspect app.py",
        context,
    )

    events = runtime.stream_events(session_id)

    tool_use = await anext(events)

    assert tool_use.type is EventType.TOOL_USE
    assert tool_use.payload == {
        "tool_call_id": "call-1",
        "name": "read_file",
        "arguments": {"path": "app.py"},
    }

    await runtime.submit_tool_result(
        session_id,
        "call-1",
        {
            "status": "ok",
            "path": "app.py",
            "content": "print('hello')\n",
        },
        context,
    )

    text = await anext(events)
    stop = await anext(events)

    assert text.type is EventType.TEXT
    assert text.payload["text"] == "Finished inspecting the file."
    assert stop.type is EventType.STOP

    await worker_task

    assert len(model.calls) == 2

    first_messages, first_tools, _ = model.calls[0]
    assert first_messages[-1] == ModelMessage(
        role="user",
        content="Inspect app.py",
    )
    assert any(
        tool["name"] == "read_file"
        for tool in first_tools
    )

    second_messages, _, _ = model.calls[1]
    assert second_messages[-1].role == "tool"
    assert second_messages[-1].tool_call_id == "call-1"
    assert second_messages[-1].name == "read_file"
    assert '"status": "ok"' in second_messages[-1].content


@pytest.mark.asyncio
async def test_coder_runtime_worker_publishes_stop_when_model_fails() -> None:
    class FailingModel:
        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
        ) -> ModelTurn:
            raise RuntimeError("model exploded")

    runtime = RuntimeBridge()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=FailingModel(),
        tools=build_coder_tool_registry(),
    )

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
    )
    session_id = await runtime.start(context)

    worker_task = asyncio.create_task(
        worker.run_session(session_id)
    )

    await runtime.send(
        session_id,
        "Inspect the project",
        context,
    )

    events = runtime.stream_events(session_id)

    stop = await asyncio.wait_for(
        anext(events),
        timeout=1,
    )

    assert stop.type is EventType.STOP
    assert stop.payload["reason"] == "runtime_error"
    assert stop.payload["error_type"] == "RuntimeError"
    assert stop.payload["message"] == "model exploded"

    with pytest.raises(RuntimeError, match="model exploded"):
        await worker_task


@pytest.mark.asyncio
async def test_worker_switches_execution_target_without_losing_history() -> None:
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
            self.calls.append((tuple(messages), target))

            return ModelTurn(
                text=f"{target.runtime_id}:{target.model_id}",
                stop=False,
            )

    runtime = RuntimeBridge()
    model = SwitchingModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=build_coder_tool_registry(),
    )

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
    )
    session_id = await runtime.start(context)

    worker_task = asyncio.create_task(
        worker.run_session(session_id)
    )

    await runtime.set_execution_target(
        session_id,
        runtime_id="legacy-runtime",
        model_id="legacy-model-a",
    )
    await runtime.send(
        session_id,
        "First task step",
        context,
    )

    events = runtime.stream_events(session_id)
    first = await asyncio.wait_for(anext(events), timeout=1)

    assert first.type is EventType.TEXT
    assert first.payload["text"] == "legacy-runtime:legacy-model-a"

    await runtime.set_execution_target(
        session_id,
        runtime_id="codex",
        model_id="codex-model",
    )
    await runtime.send(
        session_id,
        "Continue the same task",
        context,
    )

    second = await asyncio.wait_for(anext(events), timeout=1)

    assert second.type is EventType.TEXT
    assert second.payload["text"] == "codex:codex-model"

    await runtime.cancel(session_id)
    stop = await asyncio.wait_for(anext(events), timeout=1)
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

    assert first_messages[-1].content == "First task step"
    assert [message.content for message in second_messages if message.role == "user"] == [
        "First task step",
        "Continue the same task",
    ]


@pytest.mark.asyncio
async def test_worker_rehydrates_durable_history_after_runtime_restart() -> None:
    from core.model import ExecutionTarget

    class RestoredModel:
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
            self.calls.append((tuple(messages), target))
            return ModelTurn(text="continued", stop=False)

    runtime = RuntimeBridge()
    model = RestoredModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=build_coder_tool_registry(),
    )

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
        session_id="session-restored",
        project_id="project-1",
    )

    # Simulate a fresh MCP/runtime process restoring a durable session ID.
    await runtime.resume("session-restored", context)

    worker_task = asyncio.create_task(
        worker.run_session("session-restored")
    )

    await runtime.set_execution_target(
        "session-restored",
        runtime_id="codex",
        model_id="codex-model",
        history=(
            {
                "role": "user",
                "content": "Original task",
                "metadata": {},
            },
            {
                "role": "assistant",
                "content": "Work completed before restart",
                "metadata": {"event_type": "text"},
            },
        ),
    )

    await runtime.send(
        "session-restored",
        "Continue after restart",
        context,
    )

    events = runtime.stream_events("session-restored")
    text = await asyncio.wait_for(anext(events), timeout=1)

    assert text.type is EventType.TEXT
    assert text.payload["text"] == "continued"

    await runtime.cancel("session-restored")
    stop = await asyncio.wait_for(anext(events), timeout=1)
    assert stop.type is EventType.STOP

    await worker_task

    assert len(model.calls) == 1
    messages, target = model.calls[0]

    assert target == ExecutionTarget(
        runtime_id="codex",
        model_id="codex-model",
    )
    assert [(message.role, message.content) for message in messages] == [
        ("user", "Original task"),
        ("assistant", "Work completed before restart"),
        ("user", "Continue after restart"),
    ]


@pytest.mark.asyncio
async def test_worker_rehydrates_pending_tool_call_after_runtime_restart() -> None:
    class ToolContinuationModel:
        def __init__(self) -> None:
            self.calls: list[tuple[ModelMessage, ...]] = []

        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
            *,
            target,
        ) -> ModelTurn:
            self.calls.append(tuple(messages))
            return ModelTurn(text="tool continuation complete", stop=True)

    runtime = RuntimeBridge()
    model = ToolContinuationModel()
    worker = CoderRuntimeWorker(
        runtime=runtime,
        model=model,
        tools=build_coder_tool_registry(),
    )

    context = ExecutionContext(
        workspace_id="workspace-1",
        agent_id="coder",
        session_id="session-tool-restart",
        project_id="project-1",
    )

    await runtime.resume("session-tool-restart", context)

    worker_task = asyncio.create_task(
        worker.run_session("session-tool-restart")
    )

    await runtime.set_execution_target(
        "session-tool-restart",
        runtime_id="codex",
        model_id="codex-model",
        history=(
            {
                "role": "user",
                "content": "Read the file",
                "metadata": {},
            },
            {
                "role": "assistant",
                "content": (
                    '{"arguments":{"path":"README.md"},'
                    '"name":"read_file",'
                    '"tool_call_id":"call-1"}'
                ),
                "metadata": {
                    "event_type": "tool_use",
                    "tool_call_id": "call-1",
                    "name": "read_file",
                },
            },
        ),
    )

    await runtime.submit_tool_result(
        "session-tool-restart",
        "call-1",
        {
            "status": "ok",
            "content": "restored file content",
        },
        context,
    )

    events = runtime.stream_events("session-tool-restart")

    text = await asyncio.wait_for(anext(events), timeout=1)
    assert text.type is EventType.TEXT
    assert text.payload["text"] == "tool continuation complete"

    stop = await asyncio.wait_for(anext(events), timeout=1)
    assert stop.type is EventType.STOP

    await worker_task

    assert len(model.calls) == 1
    messages = model.calls[0]

    assert messages[0].role == "user"
    assert messages[0].content == "Read the file"

    assert messages[1].role == "assistant"
    assert len(messages[1].tool_calls) == 1
    assert messages[1].tool_calls[0].tool_call_id == "call-1"
    assert messages[1].tool_calls[0].name == "read_file"

    assert messages[2].role == "tool"
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].name == "read_file"
    assert "restored file content" in messages[2].content
