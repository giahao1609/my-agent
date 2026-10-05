from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.config import CoderRuntimeMode
from my_agent_mcp import server


class FakeWorker:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.release = asyncio.Event()

    async def run_session(self, session_id: str) -> None:
        self.calls.append(session_id)
        await self.release.wait()


@pytest.mark.asyncio
async def test_ensure_in_process_worker_is_noop_in_external_mode(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.EXTERNAL,
        ),
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {},
        raising=False,
    )

    async def fail_if_composition_touched():
        raise AssertionError(
            "model composition must not be loaded in external mode"
        )

    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fail_if_composition_touched,
    )

    result = await server._ensure_in_process_coder_worker(
        "session-1"
    )

    assert result is None
    assert server._coder_worker_tasks == {}


@pytest.mark.asyncio
async def test_ensure_in_process_worker_starts_worker(
    monkeypatch,
) -> None:
    worker = FakeWorker()

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {},
        raising=False,
    )

    async def fake_get_model_composition():
        return SimpleNamespace(
            coder_worker=worker,
        )

    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )

    task = await server._ensure_in_process_coder_worker(
        "session-1"
    )

    assert isinstance(task, asyncio.Task)

    await asyncio.sleep(0)

    assert worker.calls == ["session-1"]
    assert server._coder_worker_tasks["session-1"] is task

    worker.release.set()
    await task


@pytest.mark.asyncio
async def test_ensure_in_process_worker_is_idempotent_per_session(
    monkeypatch,
) -> None:
    worker = FakeWorker()

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {},
        raising=False,
    )

    async def fake_get_model_composition():
        return SimpleNamespace(
            coder_worker=worker,
        )

    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )

    first = await server._ensure_in_process_coder_worker(
        "session-1"
    )
    second = await server._ensure_in_process_coder_worker(
        "session-1"
    )

    assert first is second

    await asyncio.sleep(0)

    assert worker.calls == ["session-1"]
    assert len(server._coder_worker_tasks) == 1

    worker.release.set()
    await first


@pytest.mark.asyncio
async def test_start_coder_session_ensures_worker_after_runtime_start(
    tmp_path,
    monkeypatch,
) -> None:
    from agents.coder_stack import build_coder_agent_stack
    from core.project import ProjectRecord
    from persistence.sqlite_conversation_store import (
        SQLiteConversationStore,
    )

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    calls: list[str] = []

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        return project

    async def fake_get_coder_stack():
        return stack

    async def fake_ensure_worker(
        session_id: str,
    ):
        # Orchestrator must already have created RuntimeBridge session.
        assert session_id in stack.runtime._sessions
        calls.append("worker")
        return None

    async def fake_run_coder_session(
        stack_arg,
        session_id: str,
        message,
        context,
    ) -> None:
        calls.append("agent")

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "conversations",
        conversation_store,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_ensure_in_process_coder_worker",
        fake_ensure_worker,
    )
    monkeypatch.setattr(
        server,
        "_run_coder_session",
        fake_run_coder_session,
    )
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
    )

    result = await server.start_coder_session(
        "inspect workspace",
        project_id="demo",
    )

    session_id = str(result["session_id"])

    await asyncio.sleep(0)

    assert session_id in stack.runtime._sessions
    assert calls == [
        "worker",
        "agent",
    ]


@pytest.mark.asyncio
async def test_resume_coder_session_ensures_worker_after_runtime_hydration(
    tmp_path,
    monkeypatch,
) -> None:
    from agents.coder_stack import build_coder_agent_stack
    from core.context import ExecutionContext
    from core.project import ProjectRecord
    from persistence.sqlite_conversation_store import (
        SQLiteConversationStore,
    )

    database_path = tmp_path / "agent.db"

    original_stack = await build_coder_agent_stack(
        database_path
    )

    context = ExecutionContext(
        workspace_id=str(tmp_path),
        agent_id="coder",
        project_id="demo",
    )

    session_id = await original_stack.orchestrator.start_session(
        context
    )

    # Simulate MCP/process restart with a new in-memory stack
    # backed by the same durable SQLite database.
    restarted_stack = await build_coder_agent_stack(
        database_path
    )

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    calls: list[str] = []

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        return project

    async def fake_get_coder_stack():
        return restarted_stack

    async def fake_ensure_worker(
        worker_session_id: str,
    ):
        assert worker_session_id == session_id

        runtime_session = restarted_stack.runtime._sessions.get(
            session_id
        )
        assert runtime_session is not None

        # resume_session() must enqueue START and
        # set_execution_target() must enqueue target/history
        # before the worker is allowed to consume them.
        assert runtime_session.commands.qsize() == 2

        calls.append("worker")
        return None

    async def fake_run_coder_session(
        stack_arg,
        worker_session_id: str,
        message,
        restored_context,
    ) -> None:
        calls.append("agent")

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "conversations",
        conversation_store,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_ensure_in_process_coder_worker",
        fake_ensure_worker,
    )
    monkeypatch.setattr(
        server,
        "_run_coder_session",
        fake_run_coder_session,
    )
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
    )

    result = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    await asyncio.sleep(0)

    assert result["status"] == "resumed"
    assert calls == [
        "worker",
        "agent",
    ]


@pytest.mark.asyncio
async def test_in_process_start_runs_model_to_completion(
    tmp_path,
    monkeypatch,
) -> None:
    from collections.abc import Mapping, Sequence

    from agents.coder_runtime_worker import CoderRuntimeWorker
    from agents.coder_stack import build_coder_agent_stack
    from core.context import ExecutionContext
    from core.model import ExecutionTarget, ModelMessage, ModelTurn
    from core.project import ProjectRecord
    from persistence.sqlite_conversation_store import (
        SQLiteConversationStore,
    )

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    class FakeModel:
        def __init__(self) -> None:
            self.calls = []

        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
            *,
            target: ExecutionTarget | None = None,
        ) -> ModelTurn:
            self.calls.append(
                {
                    "messages": tuple(messages),
                    "tools": tuple(tools),
                    "context": context,
                    "target": target,
                }
            )

            return ModelTurn(
                text="in-process reply",
                stop=True,
            )

    model = FakeModel()

    worker = CoderRuntimeWorker(
        runtime=stack.runtime,
        model=model,
        tools=stack.tool_registry,
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        return project

    async def fake_get_coder_stack():
        return stack

    async def fake_get_model_composition():
        return SimpleNamespace(
            coder_worker=worker,
        )

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "conversations",
        conversation_store,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {},
    )

    started = await server.start_coder_session(
        "inspect workspace",
        project_id="demo",
    )

    session_id = str(started["session_id"])

    worker_task = server._coder_worker_tasks[session_id]

    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )

    await asyncio.wait_for(
        worker_task,
        timeout=1,
    )

    assert len(model.calls) == 1

    call = model.calls[0]

    assert [
        message.role
        for message in call["messages"]
    ] == ["user"]

    assert call["messages"][0].content == "inspect workspace"

    assert call["context"].project_id == "demo"
    assert call["context"].session_id == session_id

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    assert [
        record.role.value
        for record in history
    ] == [
        "user",
        "assistant",
    ]

    assert history[0].content == "inspect workspace"
    assert history[1].content == "in-process reply"

    session = stack.orchestrator.get_session(session_id)
    assert session.state.value == "stopped"


@pytest.mark.asyncio
async def test_in_process_start_runs_model_tool_loop_to_completion(
    tmp_path,
    monkeypatch,
) -> None:
    from collections.abc import Mapping, Sequence

    from agents.coder_runtime_worker import CoderRuntimeWorker
    from agents.coder_stack import build_coder_agent_stack
    from core.context import ExecutionContext
    from core.model import (
        ExecutionTarget,
        ModelMessage,
        ModelToolCall,
        ModelTurn,
    )
    from core.project import ProjectRecord
    from persistence.sqlite_conversation_store import (
        SQLiteConversationStore,
    )

    (tmp_path / "app.py").write_text(
        'print("hello from app")\n',
        encoding="utf-8",
    )

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    class ToolModel:
        def __init__(self) -> None:
            self.calls: list[tuple[ModelMessage, ...]] = []

        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
            *,
            target: ExecutionTarget | None = None,
        ) -> ModelTurn:
            self.calls.append(tuple(messages))

            if len(self.calls) == 1:
                assert any(
                    tool["name"] == "read_file"
                    for tool in tools
                )

                return ModelTurn(
                    tool_calls=(
                        ModelToolCall(
                            tool_call_id="call-read",
                            name="read_file",
                            arguments={
                                "path": "app.py",
                            },
                        ),
                    ),
                )

            tool_message = messages[-1]

            assert tool_message.role == "tool"
            assert tool_message.tool_call_id == "call-read"
            assert tool_message.name == "read_file"
            assert "hello from app" in tool_message.content

            return ModelTurn(
                text="I inspected app.py successfully.",
                stop=True,
            )

    model = ToolModel()

    worker = CoderRuntimeWorker(
        runtime=stack.runtime,
        model=model,
        tools=stack.tool_registry,
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        return project

    async def fake_get_coder_stack():
        return stack

    async def fake_get_model_composition():
        return SimpleNamespace(
            coder_worker=worker,
        )

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "conversations",
        conversation_store,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {},
    )

    started = await server.start_coder_session(
        "Inspect app.py",
        project_id="demo",
    )

    session_id = str(started["session_id"])

    worker_task = server._coder_worker_tasks[session_id]

    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )

    await asyncio.wait_for(
        worker_task,
        timeout=1,
    )

    assert len(model.calls) == 2

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    assert [
        record.role.value
        for record in history
    ] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    tool_use = history[1]
    tool_result = history[2]
    final_reply = history[3]

    assert tool_use.metadata["event_type"] == "tool_use"
    assert tool_use.metadata["tool_call_id"] == "call-read"
    assert tool_use.metadata["name"] == "read_file"

    assert tool_result.metadata["event_type"] == "tool_result"
    assert tool_result.metadata["tool_call_id"] == "call-read"
    assert tool_result.metadata["name"] == "read_file"
    assert "hello from app" in tool_result.content

    assert final_reply.content == (
        "I inspected app.py successfully."
    )

    session = stack.orchestrator.get_session(session_id)
    assert session.state.value == "stopped"


@pytest.mark.asyncio
async def test_in_process_destructive_tool_waits_for_approval_then_continues(
    tmp_path,
    monkeypatch,
) -> None:
    from collections.abc import Mapping, Sequence

    from agents.coder_runtime_worker import CoderRuntimeWorker
    from agents.coder_stack import build_coder_agent_stack
    from core.context import ExecutionContext
    from core.model import (
        ExecutionTarget,
        ModelMessage,
        ModelToolCall,
        ModelTurn,
    )
    from core.project import ProjectRecord
    from core.session import SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import (
        SQLiteConversationStore,
    )

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    tool_calls: list[dict[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        tool_calls.append(dict(arguments))
        return {
            "status": "ok",
            "deleted": True,
        }

    stack.tool_registry.register(
        ToolDefinition(
            name="delete_value",
            description="Delete a test value.",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {
                        "type": "integer",
                    },
                },
                "required": ["value"],
            },
            handler=destructive_action,
            permissions=frozenset(
                {
                    ToolPermission.DESTRUCTIVE,
                }
            ),
        )
    )

    class ApprovalModel:
        def __init__(self) -> None:
            self.calls: list[tuple[ModelMessage, ...]] = []

        async def generate(
            self,
            messages: Sequence[ModelMessage],
            tools: Sequence[Mapping[str, object]],
            context: ExecutionContext,
            *,
            target: ExecutionTarget | None = None,
        ) -> ModelTurn:
            self.calls.append(tuple(messages))

            if len(self.calls) == 1:
                assert any(
                    tool["name"] == "delete_value"
                    for tool in tools
                )

                return ModelTurn(
                    tool_calls=(
                        ModelToolCall(
                            tool_call_id="call-delete",
                            name="delete_value",
                            arguments={
                                "value": 42,
                            },
                        ),
                    ),
                )

            tool_message = messages[-1]

            assert tool_message.role == "tool"
            assert tool_message.tool_call_id == "call-delete"
            assert tool_message.name == "delete_value"
            assert '"status": "ok"' in tool_message.content
            assert '"deleted": true' in tool_message.content

            return ModelTurn(
                text="Deletion completed after approval.",
                stop=True,
            )

    model = ApprovalModel()

    worker = CoderRuntimeWorker(
        runtime=stack.runtime,
        model=model,
        tools=stack.tool_registry,
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ):
        return project

    async def fake_get_coder_stack():
        return stack

    async def fake_get_model_composition():
        return SimpleNamespace(
            coder_worker=worker,
        )

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            coder_runtime_mode=CoderRuntimeMode.IN_PROCESS,
        ),
    )
    monkeypatch.setattr(
        server,
        "conversations",
        conversation_store,
    )
    monkeypatch.setattr(
        server,
        "_initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        server,
        "_resolve_project",
        fake_resolve_project,
    )
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_get_model_composition",
        fake_get_model_composition,
    )
    monkeypatch.setattr(
        server,
        "_coder_tasks",
        {},
    )
    monkeypatch.setattr(
        server,
        "_coder_worker_tasks",
        {},
    )

    started = await server.start_coder_session(
        "Delete value 42",
        project_id="demo",
    )

    session_id = str(started["session_id"])

    # Wait until CoderAgent reaches the approval boundary.
    for _ in range(100):
        durable = await stack.session_store.get(session_id)

        if (
            durable is not None
            and durable.state is SessionState.WAITING_APPROVAL
        ):
            break

        await asyncio.sleep(0.01)
    else:
        pytest.fail(
            "session never entered WAITING_APPROVAL"
        )

    # Destructive operation must NOT have executed yet.
    assert tool_calls == []

    # Model must also be blocked waiting for TOOL_RESULT.
    assert len(model.calls) == 1

    worker_task = server._coder_worker_tasks[session_id]
    assert not worker_task.done()

    resolved = await server.resolve_coder_approval(
        session_id=session_id,
        tool_call_id="call-delete",
        approved=True,
    )

    assert resolved["status"] == "approved"
    assert resolved["result"] == {
        "status": "ok",
        "deleted": True,
    }

    assert tool_calls == [
        {
            "value": 42,
        }
    ]

    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )
    await asyncio.wait_for(
        worker_task,
        timeout=1,
    )

    assert len(model.calls) == 2

    session = stack.orchestrator.get_session(session_id)
    assert session.state is SessionState.STOPPED

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    matching_tool_use = [
        record
        for record in history
        if (
            record.metadata.get("event_type")
            == "tool_use"
            and record.metadata.get("tool_call_id")
            == "call-delete"
        )
    ]

    matching_tool_result = [
        record
        for record in history
        if (
            record.metadata.get("event_type")
            == "tool_result"
            and record.metadata.get("tool_call_id")
            == "call-delete"
        )
    ]

    assert len(matching_tool_use) == 1
    assert len(matching_tool_result) == 1

    assert history[-1].role.value == "assistant"
    assert history[-1].content == (
        "Deletion completed after approval."
    )
