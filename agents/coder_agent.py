from __future__ import annotations

import json

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.orchestrator import Orchestrator


class CoderAgent:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator
        self._pending_approvals: dict[
            tuple[str, str],
            tuple[str, Mapping[str, object], ExecutionContext],
        ] = {}

    async def run(
        self,
        message: str,
        context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        session_id = await self._orchestrator.start_session(context)

        async for event in self.run_session(
            session_id,
            message,
            context,
        ):
            yield event

    async def run_session(
        self,
        session_id: str,
        message: str,
        context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        session_context = await self._resume_context(
            session_id,
            context,
        )

        await self._orchestrator.send(
            session_id,
            message,
            session_context,
        )

        async for event in self._stream_session_events(
            session_id,
            session_context,
        ):
            yield event

    async def attach_session(
        self,
        session_id: str,
        context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        session_context = await self._resume_context(
            session_id,
            context,
        )

        async for event in self._stream_session_events(
            session_id,
            session_context,
        ):
            yield event

    async def _resume_context(
        self,
        session_id: str,
        context: ExecutionContext,
    ) -> ExecutionContext:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        session_context = replace(context, session_id=session_id)

        await self._orchestrator.resume_session(
            session_id,
            session_context,
        )

        return session_context

    async def _stream_session_events(
        self,
        session_id: str,
        session_context: ExecutionContext,
    ) -> AsyncIterator[AgentEvent]:
        async for event in self._orchestrator.stream_events(session_id):
            if event.type is not EventType.TOOL_USE:
                yield event
                continue

            yield event

            tool_call_id = event.payload.get("tool_call_id")
            tool_name = event.payload.get("name")
            arguments = event.payload.get("arguments", {})

            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                raise ValueError("tool_use event requires tool_call_id")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("tool_use event requires tool name")
            if not isinstance(arguments, Mapping):
                raise TypeError("tool_use arguments must be a mapping")

            try:
                result = await self._orchestrator.execute_tool(
                    tool_name,
                    arguments,
                    session_context,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }

            if result.get("status") == "approval_required":
                self._pending_approvals[(session_id, tool_call_id)] = (
                    tool_name,
                    dict(arguments),
                    session_context,
                )

            event_type = (
                EventType.TOOL_CONFIRM
                if result.get("status") == "approval_required"
                else EventType.TOOL_RESULT
            )

            yield AgentEvent(
                type=event_type,
                session_id=session_id,
                agent_id=session_context.agent_id,
                task_id=session_context.task_id,
                payload={
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "result": result,
                },
            )

            if result.get("status") != "approval_required":
                await self._orchestrator.submit_tool_result(
                    session_id,
                    tool_call_id,
                    result,
                    session_context,
                )


    def restore_pending_approvals(
        self,
        session_id: str,
        history: tuple[Mapping[str, object], ...],
        context: ExecutionContext,
    ) -> tuple[str, ...]:
        """Rebuild unresolved approval state from durable tool history."""
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        session_context = replace(context, session_id=session_id)
        pending: dict[
            str,
            tuple[str, Mapping[str, object], ExecutionContext],
        ] = {}

        for item in history:
            metadata = item.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue

            event_type = metadata.get("event_type")
            tool_call_id = metadata.get("tool_call_id")

            if (
                not isinstance(tool_call_id, str)
                or not tool_call_id.strip()
            ):
                continue

            if event_type == EventType.TOOL_RESULT.value:
                pending.pop(tool_call_id, None)
                continue

            if event_type != EventType.TOOL_USE.value:
                continue

            name = metadata.get("name")
            arguments: object = {}

            content = item.get("content")
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, Mapping):
                    if not isinstance(name, str) or not name.strip():
                        name = payload.get("name")
                    arguments = payload.get("arguments", {})

            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(arguments, Mapping):
                continue

            pending[tool_call_id] = (
                name,
                dict(arguments),
                session_context,
            )

        for key in tuple(self._pending_approvals):
            if key[0] == session_id:
                self._pending_approvals.pop(key)

        for tool_call_id, value in pending.items():
            self._pending_approvals[(session_id, tool_call_id)] = value

        return tuple(pending)

    async def resolve_tool_approval(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        approved: bool,
        submit_result: bool = True,
    ) -> AgentEvent:
        key = (session_id, tool_call_id)
        try:
            tool_name, arguments, context = self._pending_approvals.pop(key)
        except KeyError as exc:
            raise KeyError(
                f"no pending approval for tool call: {tool_call_id}"
            ) from exc

        if approved:
            try:
                result = await self._orchestrator.execute_tool(
                    tool_name,
                    arguments,
                    context,
                    approved=True,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
        else:
            result = {
                "status": "rejected",
                "tool": tool_name,
            }

        if submit_result:
            await self._orchestrator.submit_tool_result(
                session_id,
                tool_call_id,
                result,
                context,
            )

        return AgentEvent(
            type=EventType.TOOL_RESULT,
            session_id=session_id,
            agent_id=context.agent_id,
            task_id=context.task_id,
            payload={
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "result": result,
            },
        )
