from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.status import Availability, CapabilityStatus


class RuntimeCommandType(StrEnum):
    START = "start"
    MESSAGE = "message"
    TOOL_RESULT = "tool_result"
    SET_EXECUTION_TARGET = "set_execution_target"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class RuntimeCommand:
    type: RuntimeCommandType
    session_id: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _RuntimeSession:
    context: ExecutionContext
    commands: asyncio.Queue[RuntimeCommand] = field(
        default_factory=asyncio.Queue
    )
    events: asyncio.Queue[AgentEvent] = field(
        default_factory=asyncio.Queue
    )


class RuntimeBridge:
    def __init__(self) -> None:
        self._sessions: dict[str, _RuntimeSession] = {}

    async def start(self, context: ExecutionContext) -> str:
        session_id = f"runtime-{uuid4().hex}"
        session = _RuntimeSession(context=context)
        self._sessions[session_id] = session
        await session.commands.put(
            RuntimeCommand(
                type=RuntimeCommandType.START,
                session_id=session_id,
                payload={
                    "workspace_id": context.workspace_id,
                    "user_id": context.user_id,
                    "agent_id": context.agent_id,
                    "task_id": context.task_id,
                    "project_id": context.project_id,
                },
            )
        )
        return session_id

    async def resume(
        self,
        session_id: str,
        context: ExecutionContext,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        session = self._sessions.get(session_id)
        if session is not None:
            session.context = context
            return

        session = _RuntimeSession(context=context)
        self._sessions[session_id] = session

        await session.commands.put(
            RuntimeCommand(
                type=RuntimeCommandType.START,
                session_id=session_id,
                payload={
                    "workspace_id": context.workspace_id,
                    "user_id": context.user_id,
                    "agent_id": context.agent_id,
                    "task_id": context.task_id,
                    "project_id": context.project_id,
                },
            )
        )

    async def send(
        self,
        session_id: str,
        message: str,
        context: ExecutionContext,
        *,
        retrieval_context: Mapping[str, object] | None = None,
    ) -> None:
        session = self._require_session(session_id)
        session.context = context
        payload: dict[str, object] = {"message": message}
        if retrieval_context is not None:
            payload["retrieval_context"] = dict(retrieval_context)
        await session.commands.put(
            RuntimeCommand(
                type=RuntimeCommandType.MESSAGE,
                session_id=session_id,
                payload=payload,
            )
        )

    async def set_execution_target(
        self,
        session_id: str,
        *,
        runtime_id: str | None,
        model_id: str | None,
        history: tuple[Mapping[str, object], ...] | None = None,
    ) -> None:
        session = self._require_session(session_id)

        if runtime_id is not None and not runtime_id.strip():
            raise ValueError("runtime_id must not be empty")
        if model_id is not None and not model_id.strip():
            raise ValueError("model_id must not be empty")

        payload: dict[str, object] = {
            "runtime_id": runtime_id,
            "model_id": model_id,
        }
        if history is not None:
            payload["history"] = [dict(item) for item in history]

        await session.commands.put(
            RuntimeCommand(
                type=RuntimeCommandType.SET_EXECUTION_TARGET,
                session_id=session_id,
                payload=payload,
            )
        )

    async def cancel(self, session_id: str) -> None:
        session = self._require_session(session_id)
        await session.commands.put(
            RuntimeCommand(
                type=RuntimeCommandType.CANCEL,
                session_id=session_id,
            )
        )

    async def submit_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        result: Mapping[str, object],
        context: ExecutionContext,
    ) -> None:
        session = self._require_session(session_id)
        session.context = context
        await session.commands.put(
            RuntimeCommand(
                type=RuntimeCommandType.TOOL_RESULT,
                session_id=session_id,
                payload={
                    "tool_call_id": tool_call_id,
                    "result": result,
                },
            )
        )

    async def next_command(self, session_id: str) -> RuntimeCommand:
        session = self._require_session(session_id)
        return await session.commands.get()

    async def publish_event(self, event: AgentEvent) -> None:
        session = self._require_session(event.session_id)
        await session.events.put(event)

    async def _stream_events(
        self,
        session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        session = self._require_session(session_id)

        while True:
            event = await session.events.get()
            yield event

            if event.type is EventType.STOP:
                break

    def stream_events(
        self,
        session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        return self._stream_events(session_id)

    async def capabilities(self) -> tuple[CapabilityStatus, ...]:
        return (
            CapabilityStatus(
                name="runtime_bridge",
                state=Availability.READY,
            ),
        )

    def _require_session(self, session_id: str) -> _RuntimeSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown runtime session: {session_id}") from exc
