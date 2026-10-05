from __future__ import annotations

import asyncio

import pytest

from agents.coder_stack import build_coder_agent_stack
from core.project import ProjectRecord
from core.session import SessionState
from integrations.runtime_bridge import RuntimeCommandType
from my_agent_mcp import server


@pytest.mark.asyncio
async def test_mcp_coder_session_executes_runtime_tool_call(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    source = tmp_path / "hello.txt"
    source.write_text("hello coder", encoding="utf-8")

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
        raising=False,
    )

    started = await server.start_coder_session(
        "read hello.txt",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    start_command = await asyncio.wait_for(
        stack.runtime.next_command(session_id),
        timeout=1,
    )
    message_command = await asyncio.wait_for(
        stack.runtime.next_command(session_id),
        timeout=1,
    )

    assert start_command.type is RuntimeCommandType.START
    assert message_command.type is RuntimeCommandType.MESSAGE
    assert message_command.payload["message"] == "read hello.txt"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-1",
            "name": "read_file",
            "arguments": {"path": "hello.txt"},
        },
    )

    result_command = await asyncio.wait_for(
        stack.runtime.next_command(session_id),
        timeout=1,
    )

    assert result_command.type is RuntimeCommandType.TOOL_RESULT
    assert result_command.payload["tool_call_id"] == "call-1"
    result = result_command.payload["result"]
    assert result["status"] == "ok"
    assert result["content"] == "hello coder"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "complete"},
    )

    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_next_coder_command_exposes_runtime_commands(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "inspect workspace",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    start_command = await server.next_coder_command(session_id)
    message_command = await server.next_coder_command(session_id)

    assert start_command["type"] == "start"
    assert start_command["session_id"] == session_id
    assert message_command["type"] == "message"
    assert message_command["payload"]["message"] == "inspect workspace"


@pytest.mark.asyncio
async def test_next_coder_command_returns_idle_instead_of_hanging(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "inspect workspace",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id, timeout_seconds=0.1)
    await server.next_coder_command(session_id, timeout_seconds=0.1)

    idle = await server.next_coder_command(
        session_id,
        timeout_seconds=0.05,
    )

    assert idle == {
        "status": "idle",
        "session_id": session_id,
        "payload": {},
    }

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_start_coder_session_tracks_basic_orchestrator_state(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "inspect workspace",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    session = stack.orchestrator.get_session(session_id)

    assert session.session_id == session_id
    assert session.project_id == "demo"
    assert session.state.value == "running"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_switches_execution_target_and_exposes_history(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "continue current task",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    switched = await server.set_coder_execution_target(
        session_id=session_id,
        runtime_id="codex",
        model_id="codex-model",
    )

    command = await server.next_coder_command(session_id)
    session = stack.orchestrator.get_session(session_id)

    assert switched == {
        "status": "switched",
        "session_id": session_id,
        "runtime_id": "codex",
        "model_id": "codex-model",
    }
    assert command["type"] == RuntimeCommandType.SET_EXECUTION_TARGET.value
    assert command["payload"]["runtime_id"] == "codex"
    assert command["payload"]["model_id"] == "codex-model"
    assert command["payload"]["history"]
    assert command["payload"]["history"][0]["role"] == "user"
    assert session.runtime_id == "codex"
    assert session.model_id == "codex-model"
    assert session.state.value == "running"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_start_coder_session_is_tracked_by_orchestrator(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "inspect workspace",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    session = stack.orchestrator.get_session(session_id)

    assert session.session_id == session_id
    assert session.project_id == "demo"
    assert session.state.value == "running"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_switches_coder_execution_target_in_same_session(
    tmp_path,
    monkeypatch,
):
    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "continue current task",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    switched = await server.set_coder_execution_target(
        session_id=session_id,
        runtime_id="codex",
        model_id="codex-model",
    )

    command = await server.next_coder_command(session_id)
    session = stack.orchestrator.get_session(session_id)

    assert switched == {
        "status": "switched",
        "session_id": session_id,
        "runtime_id": "codex",
        "model_id": "codex-model",
    }
    assert command["type"] == RuntimeCommandType.SET_EXECUTION_TARGET.value
    assert command["payload"]["runtime_id"] == "codex"
    assert command["payload"]["model_id"] == "codex-model"
    assert command["payload"]["history"]
    assert command["payload"]["history"][0]["role"] == "user"
    assert command["payload"]["history"][0]["content"] == (
        "continue current task"
    )
    assert session.runtime_id == "codex"
    assert session.model_id == "codex-model"
    assert session.state.value == "running"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_coder_session_persists_initial_user_message(
    tmp_path,
    monkeypatch,
):
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    conversation_store = SQLiteConversationStore(tmp_path / "agent.db")
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "Fix the failing test",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    conversation = await conversation_store.get(
        "demo",
        session_id,
    )
    history = await conversation_store.history(
        "demo",
        session_id,
    )

    assert conversation is not None
    assert conversation.conversation_id == session_id
    assert len(history) == 1
    assert history[0].role.value == "user"
    assert history[0].content == "Fix the failing test"
    assert history[0].metadata["session_id"] == session_id
    assert history[0].metadata["source"] == "my-agent"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_coder_session_persists_tool_and_assistant_history(
    tmp_path,
    monkeypatch,
):
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    conversation_store = SQLiteConversationStore(tmp_path / "agent.db")
    await conversation_store.initialize()

    source = tmp_path / "hello.txt"
    source.write_text("hello coder", encoding="utf-8")

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "Read hello.txt",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await stack.runtime.next_command(session_id)
    await stack.runtime.next_command(session_id)

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-1",
            "name": "read_file",
            "arguments": {"path": "hello.txt"},
        },
    )

    result_command = await asyncio.wait_for(
        stack.runtime.next_command(session_id),
        timeout=1,
    )
    assert result_command.type is RuntimeCommandType.TOOL_RESULT

    await server.publish_coder_event(
        session_id=session_id,
        event_type="text",
        payload={"text": "The file contains hello coder."},
    )
    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "complete"},
    )

    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    assert [message.role.value for message in history] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    assert history[1].metadata["event_type"] == "tool_use"
    assert history[1].metadata["tool_call_id"] == "call-1"
    assert history[1].metadata["name"] == "read_file"

    assert history[2].metadata["event_type"] == "tool_result"
    assert history[2].metadata["tool_call_id"] == "call-1"
    assert "hello coder" in history[2].content

    assert history[3].content == "The file contains hello coder."
    assert history[3].metadata["event_type"] == "text"


@pytest.mark.asyncio
async def test_mcp_returns_durable_coder_history_for_session(
    tmp_path,
    monkeypatch,
):
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    conversation_store = SQLiteConversationStore(tmp_path / "agent.db")
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "Inspect the project",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.publish_coder_event(
        session_id=session_id,
        event_type="text",
        payload={"text": "I found the relevant module."},
    )
    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "complete"},
    )

    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )

    result = await server.get_coder_history(
        session_id=session_id,
        project_id="demo",
    )

    assert result["status"] == "ok"
    assert result["session_id"] == session_id
    assert result["project_id"] == "demo"

    history = result["history"]
    assert [item["role"] for item in history] == [
        "user",
        "assistant",
    ]
    assert history[0]["content"] == "Inspect the project"
    assert history[1]["content"] == "I found the relevant module."
    assert history[1]["metadata"]["event_type"] == "text"


@pytest.mark.asyncio
async def test_mcp_switch_command_includes_durable_history(
    tmp_path,
    monkeypatch,
):
    from core.message import MessageRecord, MessageRole
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    stack = await build_coder_agent_stack(tmp_path / "agent.db")
    conversation_store = SQLiteConversationStore(tmp_path / "agent.db")
    await conversation_store.initialize()

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "Fix the bug",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await conversation_store.add_message(
        MessageRecord(
            message_id="assistant-1",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content="I found the failing module.",
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "text",
            },
        )
    )

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    await server.set_coder_execution_target(
        session_id=session_id,
        runtime_id="codex",
        model_id="codex-model",
    )

    command = await server.next_coder_command(session_id)

    assert command["type"] == RuntimeCommandType.SET_EXECUTION_TARGET.value
    assert command["payload"]["runtime_id"] == "codex"
    assert command["payload"]["model_id"] == "codex-model"
    assert [
        item["role"]
        for item in command["payload"]["history"]
    ] == [
        "user",
        "assistant",
    ]
    assert command["payload"]["history"][0]["content"] == "Fix the bug"
    assert command["payload"]["history"][1]["content"] == (
        "I found the failing module."
    )

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_resumes_durable_coder_session_after_restart(
    tmp_path,
    monkeypatch,
):
    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"

    # Simulate durable state left by the previous MCP process.
    previous_stack = await build_coder_agent_stack(database_path)
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "session-restart"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    await previous_stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.RUNNING,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Continue durable task",
        )
    )
    await conversation_store.add_message(
        MessageRecord(
            message_id="user-1",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.USER,
            content="Original durable task",
            metadata={
                "session_id": session_id,
                "source": "my-agent",
            },
        )
    )
    await conversation_store.add_message(
        MessageRecord(
            message_id="assistant-1",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content="Work before restart",
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "text",
            },
        )
    )

    # Fresh stack = fresh MCP process with empty in-memory runtime queues.
    restarted_stack = await build_coder_agent_stack(database_path)

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return restarted_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    resumed = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert resumed == {
        "status": "resumed",
        "session_id": session_id,
        "project_id": "demo",
        "runtime_id": "codex",
        "model_id": "codex-model",
    }

    start_command = await server.next_coder_command(session_id)
    target_command = await server.next_coder_command(session_id)

    assert start_command["type"] == RuntimeCommandType.START.value
    assert start_command["session_id"] == session_id

    assert (
        target_command["type"]
        == RuntimeCommandType.SET_EXECUTION_TARGET.value
    )
    assert target_command["payload"]["runtime_id"] == "codex"
    assert target_command["payload"]["model_id"] == "codex-model"
    assert [
        item["role"]
        for item in target_command["payload"]["history"]
    ] == [
        "user",
        "assistant",
    ]
    assert target_command["payload"]["history"][0]["content"] == (
        "Original durable task"
    )
    assert target_command["payload"]["history"][1]["content"] == (
        "Work before restart"
    )

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.runtime_id == "codex"
    assert restored.model_id == "codex-model"
    assert restored.state is SessionState.RUNNING

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_finds_latest_resumable_coder_session(
    tmp_path,
    monkeypatch,
):
    from core.context import ExecutionContext
    from core.session import SessionRecord, SessionState

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    await stack.session_store.save(
        SessionRecord(
            session_id="older-session",
            context=ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="older-session",
                project_id="demo",
            ),
            runtime_id="legacy-runtime",
            model_id="legacy-model",
            state=SessionState.RUNNING,
        )
    )

    import asyncio as _asyncio
    await _asyncio.sleep(0.01)

    await stack.session_store.save(
        SessionRecord(
            session_id="latest-session",
            context=ExecutionContext(
                workspace_id=str(tmp_path),
                session_id="latest-session",
                project_id="demo",
            ),
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.WAITING_APPROVAL,
        )
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    result = await server.get_latest_resumable_coder_session(
        project_id="demo",
    )

    assert result == {
        "status": "ok",
        "project_id": "demo",
        "session_id": "latest-session",
        "runtime_id": "codex",
        "model_id": "codex-model",
        "state": "waiting_approval",
    }


@pytest.mark.asyncio
async def test_mcp_handoff_discovers_and_resumes_latest_session(
    tmp_path,
    monkeypatch,
):
    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "handoff-session"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    await previous_stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            runtime_id="legacy-runtime",
            model_id="legacy-model",
            state=SessionState.RUNNING,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Handoff task",
        )
    )
    await conversation_store.add_message(
        MessageRecord(
            message_id="handoff-user-1",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.USER,
            content="Continue this task after handoff",
            metadata={
                "session_id": session_id,
                "source": "my-agent",
            },
        )
    )

    restarted_stack = await build_coder_agent_stack(database_path)

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return restarted_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    result = await server.handoff_coder_session(
        project_id="demo",
    )

    assert result == {
        "status": "resumed",
        "session_id": session_id,
        "project_id": "demo",
        "runtime_id": "legacy-runtime",
        "model_id": "legacy-model",
    }

    start_command = await server.next_coder_command(session_id)
    target_command = await server.next_coder_command(session_id)

    assert start_command["type"] == RuntimeCommandType.START.value
    assert (
        target_command["type"]
        == RuntimeCommandType.SET_EXECUTION_TARGET.value
    )
    assert target_command["payload"]["history"][0]["content"] == (
        "Continue this task after handoff"
    )

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_pending_approval_survives_restart(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.session import SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {
            "status": "ok",
            "deleted": True,
        }

    destructive_tool = ToolDefinition(
        name="delete_test_value",
        description="Delete a test value.",
        input_schema={"type": "object"},
        handler=destructive_action,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )
    previous_stack.tool_registry.register(destructive_tool)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    active_stack = previous_stack

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return active_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "delete the test value",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    # Drain the initial runtime commands.
    start_command = await server.next_coder_command(session_id)
    message_command = await server.next_coder_command(session_id)

    assert start_command["type"] == RuntimeCommandType.START.value
    assert message_command["type"] == RuntimeCommandType.MESSAGE.value

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-restart-approval",
            "name": "delete_test_value",
            "arguments": {"value": 42},
        },
    )

    # Wait until the approval state has been durably persisted.
    for _ in range(100):
        durable = await previous_stack.session_store.get(session_id)
        if (
            durable is not None
            and durable.state is SessionState.WAITING_APPROVAL
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("session never entered WAITING_APPROVAL")

    assert calls == []

    history = await conversation_store.history(
        "demo",
        session_id,
    )
    tool_use_records = [
        record
        for record in history
        if record.metadata.get("event_type") == "tool_use"
    ]

    assert len(tool_use_records) == 1
    assert (
        tool_use_records[0].metadata["tool_call_id"]
        == "call-restart-approval"
    )

    # Simulate the previous MCP process disappearing without changing
    # the durable session state.
    previous_task = server._coder_tasks.pop(session_id)
    previous_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await previous_task

    # Fresh process / fresh CoderAgent: in-memory pending approvals are gone.
    restarted_stack = await build_coder_agent_stack(database_path)
    restarted_stack.tool_registry.register(destructive_tool)
    active_stack = restarted_stack

    resumed = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert resumed["status"] == "resumed"
    assert resumed["session_id"] == session_id

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.state is SessionState.WAITING_APPROVAL

    resumed_start = await server.next_coder_command(session_id)
    target_command = await server.next_coder_command(session_id)

    assert resumed_start["type"] == RuntimeCommandType.START.value
    assert (
        target_command["type"]
        == RuntimeCommandType.SET_EXECUTION_TARGET.value
    )

    try:
        resolved = await server.resolve_coder_approval(
            session_id=session_id,
            tool_call_id="call-restart-approval",
            approved=True,
        )

        assert resolved["status"] == "approved"
        assert resolved["result"] == {
            "status": "ok",
            "deleted": True,
        }
        assert calls == [{"value": 42}]

        result_command = await server.next_coder_command(session_id)

        assert (
            result_command["type"]
            == RuntimeCommandType.TOOL_RESULT.value
        )
        assert (
            result_command["payload"]["tool_call_id"]
            == "call-restart-approval"
        )

        restored = restarted_stack.orchestrator.get_session(session_id)
        assert restored.state is SessionState.RUNNING

        durable_history = await conversation_store.history(
            "demo",
            session_id,
        )
        matching_results = [
            record
            for record in durable_history
            if (
                record.metadata.get("event_type") == "tool_result"
                and record.metadata.get("tool_call_id")
                == "call-restart-approval"
            )
        ]
        assert len(matching_results) == 1

        # Restart once more after the tool result has been completed.
        completed_task = server._coder_tasks.pop(session_id)
        completed_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await completed_task

        second_restarted_stack = await build_coder_agent_stack(database_path)
        second_restarted_stack.tool_registry.register(destructive_tool)
        active_stack = second_restarted_stack

        resumed_again = await server.resume_coder_session(
            session_id=session_id,
            project_id="demo",
        )

        assert resumed_again["status"] == "resumed"

        restored_again = second_restarted_stack.orchestrator.get_session(
            session_id
        )
        assert restored_again.state is SessionState.RUNNING

        second_start = await server.next_coder_command(session_id)
        second_target = await server.next_coder_command(session_id)

        assert second_start["type"] == RuntimeCommandType.START.value
        assert (
            second_target["type"]
            == RuntimeCommandType.SET_EXECUTION_TARGET.value
        )

        matching_history = [
            item
            for item in second_target["payload"]["history"]
            if item["metadata"].get("tool_call_id")
            == "call-restart-approval"
        ]
        assert [
            item["metadata"].get("event_type")
            for item in matching_history
        ] == [
            "tool_use",
            "tool_result",
        ]

        with pytest.raises(
            KeyError,
            match="no pending approval for tool call",
        ):
            await server.resolve_coder_approval(
                session_id=session_id,
                tool_call_id="call-restart-approval",
                approved=True,
            )

        assert calls == [{"value": 42}]
    finally:
        await server.publish_coder_event(
            session_id=session_id,
            event_type="stop",
            payload={"reason": "test_complete"},
        )
        await asyncio.wait_for(
            server._coder_tasks[session_id],
            timeout=1,
        )


@pytest.mark.asyncio
async def test_mcp_pending_approval_rejection_survives_restart(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.session import SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {"status": "ok"}

    destructive_tool = ToolDefinition(
        name="delete_rejected_value",
        description="Delete a rejected test value.",
        input_schema={"type": "object"},
        handler=destructive_action,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )
    previous_stack.tool_registry.register(destructive_tool)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    active_stack = previous_stack

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return active_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "delete the rejected value",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-restart-rejected",
            "name": "delete_rejected_value",
            "arguments": {"value": 99},
        },
    )

    for _ in range(100):
        durable = await previous_stack.session_store.get(session_id)
        if (
            durable is not None
            and durable.state is SessionState.WAITING_APPROVAL
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("session never entered WAITING_APPROVAL")

    assert calls == []

    previous_task = server._coder_tasks.pop(session_id)
    previous_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await previous_task

    restarted_stack = await build_coder_agent_stack(database_path)
    restarted_stack.tool_registry.register(destructive_tool)
    active_stack = restarted_stack

    resumed = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert resumed["status"] == "resumed"

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    resolved = await server.resolve_coder_approval(
        session_id=session_id,
        tool_call_id="call-restart-rejected",
        approved=False,
    )

    assert resolved["status"] == "rejected"
    assert resolved["result"] == {
        "status": "rejected",
        "tool": "delete_rejected_value",
    }
    assert calls == []

    result_command = await server.next_coder_command(session_id)

    assert result_command["type"] == RuntimeCommandType.TOOL_RESULT.value
    assert (
        result_command["payload"]["tool_call_id"]
        == "call-restart-rejected"
    )
    assert result_command["payload"]["result"] == {
        "status": "rejected",
        "tool": "delete_rejected_value",
    }

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.state is SessionState.RUNNING

    history = await conversation_store.history(
        "demo",
        session_id,
    )
    matching_results = [
        record
        for record in history
        if (
            record.metadata.get("event_type") == "tool_result"
            and record.metadata.get("tool_call_id")
            == "call-restart-rejected"
        )
    ]

    assert len(matching_results) == 1

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_handoff_restores_pending_approval(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.session import SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {
            "status": "ok",
            "deleted": True,
        }

    destructive_tool = ToolDefinition(
        name="handoff_delete_value",
        description="Delete a test value after handoff.",
        input_schema={"type": "object"},
        handler=destructive_action,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )
    previous_stack.tool_registry.register(destructive_tool)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    active_stack = previous_stack

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return active_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "delete value after handoff",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-handoff-approval",
            "name": "handoff_delete_value",
            "arguments": {"value": 123},
        },
    )

    for _ in range(100):
        durable = await previous_stack.session_store.get(session_id)
        if (
            durable is not None
            and durable.state is SessionState.WAITING_APPROVAL
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("session never entered WAITING_APPROVAL")

    assert calls == []

    previous_task = server._coder_tasks.pop(session_id)
    previous_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await previous_task

    restarted_stack = await build_coder_agent_stack(database_path)
    restarted_stack.tool_registry.register(destructive_tool)
    active_stack = restarted_stack

    result = await server.handoff_coder_session(
        project_id="demo",
    )

    assert result["status"] == "resumed"
    assert result["session_id"] == session_id

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.state is SessionState.WAITING_APPROVAL

    await server.next_coder_command(session_id)
    target_command = await server.next_coder_command(session_id)

    assert (
        target_command["type"]
        == RuntimeCommandType.SET_EXECUTION_TARGET.value
    )

    resolved = await server.resolve_coder_approval(
        session_id=session_id,
        tool_call_id="call-handoff-approval",
        approved=True,
    )

    assert resolved["status"] == "approved"
    assert resolved["result"] == {
        "status": "ok",
        "deleted": True,
    }
    assert calls == [{"value": 123}]

    result_command = await server.next_coder_command(session_id)

    assert result_command["type"] == RuntimeCommandType.TOOL_RESULT.value
    assert (
        result_command["payload"]["tool_call_id"]
        == "call-handoff-approval"
    )

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.state is SessionState.RUNNING

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_restart_restores_only_unresolved_tool_call(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "multi-tool-restart"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    await previous_stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.WAITING_APPROVAL,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Multiple durable tool calls",
        )
    )

    # Completed call.
    await conversation_store.add_message(
        MessageRecord(
            message_id="completed-use",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content=(
                '{"arguments": {"value": 1}, '
                '"name": "destructive_value", '
                '"tool_call_id": "call-completed"}'
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_use",
                "tool_call_id": "call-completed",
                "name": "destructive_value",
            },
        )
    )
    await conversation_store.add_message(
        MessageRecord(
            message_id="completed-result",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.TOOL,
            content='{"status": "ok"}',
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_result",
                "tool_call_id": "call-completed",
                "name": "destructive_value",
            },
        )
    )

    # Unresolved call.
    await conversation_store.add_message(
        MessageRecord(
            message_id="pending-use",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content=(
                '{"arguments": {"value": 2}, '
                '"name": "destructive_value", '
                '"tool_call_id": "call-pending"}'
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_use",
                "tool_call_id": "call-pending",
                "name": "destructive_value",
            },
        )
    )

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {"status": "ok", "value": arguments.get("value")}

    destructive_tool = ToolDefinition(
        name="destructive_value",
        description="Test destructive value operation.",
        input_schema={"type": "object"},
        handler=destructive_action,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )

    restarted_stack = await build_coder_agent_stack(database_path)
    restarted_stack.tool_registry.register(destructive_tool)

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return restarted_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    resumed = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert resumed["status"] == "resumed"

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    # Completed call must NOT be reconstructed.
    with pytest.raises(
        KeyError,
        match="no pending approval for tool call",
    ):
        await server.resolve_coder_approval(
            session_id=session_id,
            tool_call_id="call-completed",
            approved=True,
        )

    assert calls == []

    # Only the unresolved call should survive restart.
    resolved = await server.resolve_coder_approval(
        session_id=session_id,
        tool_call_id="call-pending",
        approved=True,
    )

    assert resolved["status"] == "approved"
    assert resolved["result"] == {
        "status": "ok",
        "value": 2,
    }
    assert calls == [{"value": 2}]

    result_command = await server.next_coder_command(session_id)
    assert result_command["type"] == RuntimeCommandType.TOOL_RESULT.value
    assert result_command["payload"]["tool_call_id"] == "call-pending"

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.state is SessionState.RUNNING

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_restart_does_not_reexecute_unresolved_nonapproval_tool(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.session import SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    calls: list[Mapping[str, object]] = []
    tool_started = asyncio.Event()
    blocker = asyncio.Event()

    async def interrupted_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        # Simulate a side effect that happened before the process died.
        calls.append(dict(arguments))
        tool_started.set()

        # Never finishes in the original process.
        await blocker.wait()

        return {
            "status": "ok",
            "executed": True,
        }

    interrupted_tool = ToolDefinition(
        name="interrupted_execute",
        description="Simulate an interrupted non-approval tool.",
        input_schema={"type": "object"},
        handler=interrupted_action,
        permissions=frozenset({ToolPermission.EXECUTE}),
    )
    previous_stack.tool_registry.register(interrupted_tool)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    active_stack = previous_stack

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return active_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "run the interrupted action",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-interrupted",
            "name": "interrupted_execute",
            "arguments": {"value": 7},
        },
    )

    # The handler has started and its simulated side effect already happened,
    # but no TOOL_RESULT can exist yet because the handler is blocked.
    await asyncio.wait_for(tool_started.wait(), timeout=1)

    assert calls == [{"value": 7}]

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    matching_use = [
        record
        for record in history
        if (
            record.metadata.get("event_type") == "tool_use"
            and record.metadata.get("tool_call_id")
            == "call-interrupted"
        )
    ]
    matching_result = [
        record
        for record in history
        if (
            record.metadata.get("event_type") == "tool_result"
            and record.metadata.get("tool_call_id")
            == "call-interrupted"
        )
    ]

    assert len(matching_use) == 1
    assert matching_result == []

    durable = await previous_stack.session_store.get(session_id)
    assert durable is not None
    assert durable.state is SessionState.RUNNING

    # Simulate process death while the tool execution is in-flight.
    previous_task = server._coder_tasks.pop(session_id)
    previous_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await previous_task

    # Fresh process, same durable database.
    restarted_stack = await build_coder_agent_stack(database_path)
    restarted_stack.tool_registry.register(interrupted_tool)
    active_stack = restarted_stack

    resumed = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert resumed["status"] == "resumed"
    assert resumed["session_id"] == session_id

    restored = restarted_stack.orchestrator.get_session(session_id)
    assert restored.state is SessionState.RUNNING

    start_command = await server.next_coder_command(session_id)
    target_command = await server.next_coder_command(session_id)

    assert start_command["type"] == RuntimeCommandType.START.value
    assert (
        target_command["type"]
        == RuntimeCommandType.SET_EXECUTION_TARGET.value
    )

    matching_history = [
        item
        for item in target_command["payload"]["history"]
        if item["metadata"].get("tool_call_id") == "call-interrupted"
    ]

    assert [
        item["metadata"].get("event_type")
        for item in matching_history
    ] == ["tool_use"]

    # Critical invariant: resume must not execute the tool again.
    await asyncio.sleep(0.05)
    assert calls == [{"value": 7}]

    # MyAgent also must not fabricate a TOOL_RESULT after restart.
    idle = await server.next_coder_command(
        session_id,
        timeout_seconds=0.05,
    )
    assert idle["status"] == "idle"

    history_after_restart = await conversation_store.history(
        "demo",
        session_id,
    )
    matching_result_after_restart = [
        record
        for record in history_after_restart
        if (
            record.metadata.get("event_type") == "tool_result"
            and record.metadata.get("tool_call_id")
            == "call-interrupted"
        )
    ]

    assert matching_result_after_restart == []

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_reconciles_interrupted_nonapproval_tool_result_after_restart(
    tmp_path,
    monkeypatch,
):
    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    previous_stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "reconcile-tool-result"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    await previous_stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.RUNNING,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Interrupted tool reconciliation",
        )
    )

    await conversation_store.add_message(
        MessageRecord(
            message_id="reconcile-user",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.USER,
            content="Read README.md",
            metadata={
                "session_id": session_id,
                "source": "my-agent",
            },
        )
    )

    await conversation_store.add_message(
        MessageRecord(
            message_id="reconcile-tool-use",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content=(
                '{"arguments":{"path":"README.md"},'
                '"name":"read_file",'
                '"tool_call_id":"call-reconcile"}'
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_use",
                "tool_call_id": "call-reconcile",
                "name": "read_file",
            },
        )
    )

    restarted_stack = await build_coder_agent_stack(database_path)

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return restarted_stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    resumed = await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert resumed["status"] == "resumed"

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    reconciled = await server.reconcile_coder_tool_result(
        session_id=session_id,
        tool_call_id="call-reconcile",
        result={
            "status": "ok",
            "content": "verified restored content",
        },
        project_id="demo",
    )

    assert reconciled == {
        "status": "reconciled",
        "session_id": session_id,
        "project_id": "demo",
        "tool_call_id": "call-reconcile",
        "tool": "read_file",
        "result": {
            "status": "ok",
            "content": "verified restored content",
        },
    }

    command = await server.next_coder_command(session_id)

    assert command["type"] == RuntimeCommandType.TOOL_RESULT.value
    assert command["payload"]["tool_call_id"] == "call-reconcile"
    assert command["payload"]["result"] == {
        "status": "ok",
        "content": "verified restored content",
    }

    history = await conversation_store.history(
        "demo",
        session_id,
    )

    matching_results = [
        record
        for record in history
        if (
            record.metadata.get("event_type") == "tool_result"
            and record.metadata.get("tool_call_id")
            == "call-reconcile"
        )
    ]

    assert len(matching_results) == 1
    assert matching_results[0].metadata["name"] == "read_file"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_reconcile_rejects_tool_call_with_existing_result(
    tmp_path,
    monkeypatch,
):
    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "reconcile-completed"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    await stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            runtime_id="codex",
            model_id="codex-model",
            state=SessionState.RUNNING,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Completed tool call",
        )
    )

    await conversation_store.add_message(
        MessageRecord(
            message_id="completed-use",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content=(
                '{"arguments":{"path":"README.md"},'
                '"name":"read_file",'
                '"tool_call_id":"call-completed-reconcile"}'
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_use",
                "tool_call_id": "call-completed-reconcile",
                "name": "read_file",
            },
        )
    )

    await conversation_store.add_message(
        MessageRecord(
            message_id="completed-result",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.TOOL,
            content='{"status":"ok"}',
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_result",
                "tool_call_id": "call-completed-reconcile",
                "name": "read_file",
            },
        )
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    with pytest.raises(
        ValueError,
        match="already has a durable result",
    ):
        await server.reconcile_coder_tool_result(
            session_id=session_id,
            tool_call_id="call-completed-reconcile",
            result={"status": "ok"},
            project_id="demo",
        )

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_reconcile_cannot_bypass_waiting_approval(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "reconcile-waiting-approval"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return {"status": "ok"}

    stack.tool_registry.register(
        ToolDefinition(
            name="approval_guard_tool",
            description="Approval guard test tool.",
            input_schema={"type": "object"},
            handler=destructive_action,
            permissions=frozenset({ToolPermission.DESTRUCTIVE}),
        )
    )

    await stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            state=SessionState.WAITING_APPROVAL,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Waiting approval",
        )
    )

    await conversation_store.add_message(
        MessageRecord(
            message_id="approval-use",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content=(
                '{"arguments":{"value":1},'
                '"name":"approval_guard_tool",'
                '"tool_call_id":"call-waiting-approval"}'
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_use",
                "tool_call_id": "call-waiting-approval",
                "name": "approval_guard_tool",
            },
        )
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    with pytest.raises(
        ValueError,
        match="waiting for approval",
    ):
        await server.reconcile_coder_tool_result(
            session_id=session_id,
            tool_call_id="call-waiting-approval",
            result={"status": "ok"},
            project_id="demo",
        )

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_reconcile_cannot_bypass_destructive_tool_policy(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.conversation import ConversationRecord
    from core.message import MessageRecord, MessageRole
    from core.session import SessionRecord, SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)
    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    session_id = "reconcile-destructive-guard"
    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        user_id="user-1",
        agent_id="coder",
        session_id=session_id,
        project_id="demo",
    )

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        return {"status": "ok"}

    stack.tool_registry.register(
        ToolDefinition(
            name="destructive_guard_tool",
            description="Destructive reconciliation guard.",
            input_schema={"type": "object"},
            handler=destructive_action,
            permissions=frozenset({ToolPermission.DESTRUCTIVE}),
        )
    )

    # Deliberately inconsistent durable state to verify defense-in-depth:
    # even RUNNING must not allow direct destructive reconciliation.
    await stack.session_store.save(
        SessionRecord(
            session_id=session_id,
            context=context,
            state=SessionState.RUNNING,
        )
    )

    await conversation_store.create(
        ConversationRecord(
            conversation_id=session_id,
            project_id="demo",
            title="Destructive guard",
        )
    )

    await conversation_store.add_message(
        MessageRecord(
            message_id="destructive-use",
            project_id="demo",
            conversation_id=session_id,
            role=MessageRole.ASSISTANT,
            content=(
                '{"arguments":{"value":1},'
                '"name":"destructive_guard_tool",'
                '"tool_call_id":"call-destructive-guard"}'
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": "tool_use",
                "tool_call_id": "call-destructive-guard",
                "name": "destructive_guard_tool",
            },
        )
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    await server.resume_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    with pytest.raises(
        ValueError,
        match="approval-required tool calls cannot be reconciled directly",
    ):
        await server.reconcile_coder_tool_result(
            session_id=session_id,
            tool_call_id="call-destructive-guard",
            result={"status": "ok"},
            project_id="demo",
        )

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(server._coder_tasks[session_id], timeout=1)


@pytest.mark.asyncio
async def test_mcp_execution_target_switch_preserves_pending_approval(
    tmp_path,
    monkeypatch,
):
    from collections.abc import Mapping

    from core.context import ExecutionContext
    from core.session import SessionState
    from core.tools import ToolDefinition, ToolPermission
    from persistence.sqlite_conversation_store import SQLiteConversationStore

    database_path = tmp_path / "agent.db"
    stack = await build_coder_agent_stack(database_path)

    conversation_store = SQLiteConversationStore(database_path)
    await conversation_store.initialize()

    calls: list[Mapping[str, object]] = []

    async def destructive_action(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        calls.append(dict(arguments))
        return {
            "status": "ok",
            "switched": True,
        }

    destructive_tool = ToolDefinition(
        name="switch_target_destructive",
        description="Test approval across execution-target switch.",
        input_schema={"type": "object"},
        handler=destructive_action,
        permissions=frozenset({ToolPermission.DESTRUCTIVE}),
    )
    stack.tool_registry.register(destructive_tool)

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)
    monkeypatch.setattr(server, "conversations", conversation_store)

    started = await server.start_coder_session(
        "perform destructive action",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    await server.next_coder_command(session_id)
    await server.next_coder_command(session_id)

    await server.publish_coder_event(
        session_id=session_id,
        event_type="tool_use",
        payload={
            "tool_call_id": "call-switch-approval",
            "name": "switch_target_destructive",
            "arguments": {"value": 42},
        },
    )

    for _ in range(100):
        durable = await stack.session_store.get(session_id)
        if (
            durable is not None
            and durable.state is SessionState.WAITING_APPROVAL
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("session never entered WAITING_APPROVAL")

    assert calls == []

    switched = await server.set_coder_execution_target(
        session_id=session_id,
        runtime_id="codex",
        model_id="codex-model-2",
    )

    assert switched == {
        "status": "switched",
        "session_id": session_id,
        "runtime_id": "codex",
        "model_id": "codex-model-2",
    }

    target_command = await server.next_coder_command(session_id)

    assert (
        target_command["type"]
        == RuntimeCommandType.SET_EXECUTION_TARGET.value
    )
    assert target_command["payload"]["runtime_id"] == "codex"
    assert target_command["payload"]["model_id"] == "codex-model-2"

    matching_history = [
        item
        for item in target_command["payload"]["history"]
        if item["metadata"].get("tool_call_id")
        == "call-switch-approval"
    ]

    assert [
        item["metadata"].get("event_type")
        for item in matching_history
    ] == ["tool_use"]

    # Switching execution target must not replay/execute the tool.
    assert calls == []

    session = stack.orchestrator.get_session(session_id)
    assert session.state is SessionState.WAITING_APPROVAL
    assert session.runtime_id == "codex"
    assert session.model_id == "codex-model-2"

    resolved = await server.resolve_coder_approval(
        session_id=session_id,
        tool_call_id="call-switch-approval",
        approved=True,
    )

    assert resolved["status"] == "approved"
    assert resolved["result"] == {
        "status": "ok",
        "switched": True,
    }
    assert calls == [{"value": 42}]

    result_command = await server.next_coder_command(session_id)

    assert result_command["type"] == RuntimeCommandType.TOOL_RESULT.value
    assert (
        result_command["payload"]["tool_call_id"]
        == "call-switch-approval"
    )

    session = stack.orchestrator.get_session(session_id)
    assert session.state is SessionState.RUNNING
    assert session.runtime_id == "codex"
    assert session.model_id == "codex-model-2"

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )


@pytest.mark.asyncio
async def test_mcp_cancel_coder_session_stops_durable_session(
    tmp_path,
    monkeypatch,
) -> None:
    stack = await build_coder_agent_stack(tmp_path / "agent.db")

    project = ProjectRecord(
        project_id="demo",
        name="Demo",
        workspace_path=str(tmp_path),
        active_task_id="task-1",
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return project

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "long running work",
        project_id="demo",
    )
    session_id = str(started["session_id"])

    result = await server.cancel_coder_session(
        session_id=session_id,
        project_id="demo",
    )

    assert result["status"] == "cancelled"
    assert result["session_id"] == session_id

    durable = await stack.session_store.get(session_id)

    assert durable is not None
    assert durable.state is SessionState.STOPPED


@pytest.mark.asyncio
async def test_mcp_cancel_coder_session_rejects_project_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    stack = await build_coder_agent_stack(tmp_path / "agent.db")

    owner = ProjectRecord(
        project_id="owner",
        name="Owner",
        workspace_path=str(tmp_path),
    )
    other = ProjectRecord(
        project_id="other",
        name="Other",
        workspace_path=str(tmp_path),
    )

    async def fake_initialize() -> None:
        return None

    async def fake_resolve_project(
        project_id: str | None = None,
    ) -> ProjectRecord:
        return other if project_id == "other" else owner

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(server, "_initialize", fake_initialize)
    monkeypatch.setattr(server, "_resolve_project", fake_resolve_project)
    monkeypatch.setattr(server, "_get_coder_stack", fake_get_coder_stack)

    started = await server.start_coder_session(
        "owner task",
        project_id="owner",
    )
    session_id = str(started["session_id"])

    result = await server.cancel_coder_session(
        session_id=session_id,
        project_id="other",
    )

    assert result["status"] == "project_mismatch"

    durable = await stack.session_store.get(session_id)

    assert durable is not None
    assert durable.state is SessionState.RUNNING

    await server.publish_coder_event(
        session_id=session_id,
        event_type="stop",
        payload={"reason": "test_complete"},
    )
    await asyncio.wait_for(
        server._coder_tasks[session_id],
        timeout=1,
    )
