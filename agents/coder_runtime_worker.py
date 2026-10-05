from __future__ import annotations

import json
from collections.abc import Mapping

from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.model import ExecutionTarget, ModelMessage, ModelToolCall, ModelTurn
from core.protocols import ModelBackend
from core.tool_registry import ToolRegistry
from integrations.runtime_bridge import (
    RuntimeBridge,
    RuntimeCommand,
    RuntimeCommandType,
)


class CoderRuntimeWorker:
    def __init__(
        self,
        *,
        runtime: RuntimeBridge,
        model: ModelBackend,
        tools: ToolRegistry,
    ) -> None:
        self._runtime = runtime
        self._model = model
        self._tools = tools

    async def run_session(self, session_id: str) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        context: ExecutionContext | None = None

        try:
            start = await self._runtime.next_command(session_id)
            if start.type is not RuntimeCommandType.START:
                raise ValueError("runtime session must begin with START")

            context = self._context_from_start(
                session_id,
                start,
            )
            messages: list[ModelMessage] = []
            pending_tools: dict[str, ModelToolCall] = {}
            target = ExecutionTarget()

            while True:
                command = await self._runtime.next_command(session_id)

                if command.type is RuntimeCommandType.SET_EXECUTION_TARGET:
                    runtime_id = command.payload.get("runtime_id")
                    model_id = command.payload.get("model_id")

                    if runtime_id is not None and not isinstance(runtime_id, str):
                        raise TypeError("runtime_id must be a string or None")
                    if model_id is not None and not isinstance(model_id, str):
                        raise TypeError("model_id must be a string or None")

                    target = ExecutionTarget(
                        runtime_id=runtime_id,
                        model_id=model_id,
                    )

                    history = command.payload.get("history")
                    if history is not None:
                        if not isinstance(history, list):
                            raise TypeError("history must be a list")

                        messages, pending_tools = self._hydrate_history(
                            history
                        )

                    continue

                if command.type is RuntimeCommandType.CANCEL:
                    await self._runtime.publish_event(
                        AgentEvent(
                            type=EventType.STOP,
                            session_id=session_id,
                            agent_id=context.agent_id,
                            task_id=context.task_id,
                            payload={"reason": "cancelled"},
                        )
                    )
                    return

                if command.type is RuntimeCommandType.MESSAGE:
                    message = command.payload.get("message")
                    if not isinstance(message, str) or not message.strip():
                        raise ValueError(
                            "message command requires non-empty message"
                        )

                    messages.append(
                        ModelMessage(
                            role="user",
                            content=message,
                        )
                    )

                    should_stop = await self._generate(
                        session_id=session_id,
                        context=context,
                        messages=messages,
                        pending_tools=pending_tools,
                        target=target,
                    )
                    if should_stop:
                        return
                    continue

                if command.type is RuntimeCommandType.TOOL_RESULT:
                    tool_call_id = command.payload.get("tool_call_id")
                    result = command.payload.get("result")

                    if (
                        not isinstance(tool_call_id, str)
                        or not tool_call_id.strip()
                    ):
                        raise ValueError(
                            "tool_result command requires tool_call_id"
                        )
                    if not isinstance(result, Mapping):
                        raise TypeError(
                            "tool_result result must be a mapping"
                        )

                    try:
                        tool_call = pending_tools.pop(tool_call_id)
                    except KeyError as exc:
                        raise KeyError(
                            f"unknown pending tool call: {tool_call_id}"
                        ) from exc

                    messages.append(
                        ModelMessage(
                            role="tool",
                            content=json.dumps(
                                dict(result),
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            tool_call_id=tool_call_id,
                            name=tool_call.name,
                        )
                    )

                    if pending_tools:
                        continue

                    should_stop = await self._generate(
                        session_id=session_id,
                        context=context,
                        messages=messages,
                        pending_tools=pending_tools,
                        target=target,
                    )
                    if should_stop:
                        return
                    continue

                if command.type is RuntimeCommandType.START:
                    raise ValueError("duplicate START command")

        except Exception as exc:
            await self._runtime.publish_event(
                AgentEvent(
                    type=EventType.STOP,
                    session_id=session_id,
                    agent_id=context.agent_id if context is not None else None,
                    task_id=context.task_id if context is not None else None,
                    payload={
                        "reason": "runtime_error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            )
            raise

    async def _generate(
        self,
        *,
        session_id: str,
        context: ExecutionContext,
        messages: list[ModelMessage],
        pending_tools: dict[str, ModelToolCall],
        target: ExecutionTarget,
    ) -> bool:
        if target.runtime_id is None and target.model_id is None:
            turn = await self._model.generate(
                tuple(messages),
                self._tool_schemas(),
                context,
            )
        else:
            turn = await self._model.generate(
                tuple(messages),
                self._tool_schemas(),
                context,
                target=target,
            )

        if not isinstance(turn, ModelTurn):
            raise TypeError("model.generate must return ModelTurn")

        if turn.text is not None or turn.tool_calls:
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=turn.text or "",
                    tool_calls=turn.tool_calls,
                )
            )

        if turn.text:
            await self._runtime.publish_event(
                AgentEvent(
                    type=EventType.TEXT,
                    session_id=session_id,
                    agent_id=context.agent_id,
                    task_id=context.task_id,
                    payload={"text": turn.text},
                )
            )

        for tool_call in turn.tool_calls:
            if tool_call.tool_call_id in pending_tools:
                raise ValueError(
                    f"duplicate tool_call_id: {tool_call.tool_call_id}"
                )

            pending_tools[tool_call.tool_call_id] = tool_call

            await self._runtime.publish_event(
                AgentEvent(
                    type=EventType.TOOL_USE,
                    session_id=session_id,
                    agent_id=context.agent_id,
                    task_id=context.task_id,
                    payload={
                        "tool_call_id": tool_call.tool_call_id,
                        "name": tool_call.name,
                        "arguments": dict(tool_call.arguments),
                    },
                )
            )

        if turn.stop and not pending_tools:
            await self._runtime.publish_event(
                AgentEvent(
                    type=EventType.STOP,
                    session_id=session_id,
                    agent_id=context.agent_id,
                    task_id=context.task_id,
                    payload={"reason": "complete"},
                )
            )
            return True

        return False

    @staticmethod
    def _hydrate_history(
        history: list[object],
    ) -> tuple[list[ModelMessage], dict[str, ModelToolCall]]:
        messages: list[ModelMessage] = []
        pending_tools: dict[str, ModelToolCall] = {}

        for item in history:
            if not isinstance(item, Mapping):
                raise TypeError("history entries must be mappings")

            role = item.get("role")
            content = item.get("content")
            metadata = item.get("metadata", {})

            if not isinstance(role, str) or not role.strip():
                raise ValueError("history entry requires a non-empty role")
            if not isinstance(content, str):
                raise TypeError("history entry content must be a string")
            if not isinstance(metadata, Mapping):
                raise TypeError("history entry metadata must be a mapping")

            event_type = metadata.get("event_type")

            if role == "assistant" and event_type == "tool_use":
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "tool_use history content must be valid JSON"
                    ) from exc

                if not isinstance(payload, Mapping):
                    raise TypeError(
                        "tool_use history content must decode to a mapping"
                    )

                tool_call_id = payload.get("tool_call_id")
                name = payload.get("name")
                arguments = payload.get("arguments", {})

                if (
                    not isinstance(tool_call_id, str)
                    or not tool_call_id.strip()
                ):
                    raise ValueError(
                        "tool_use history requires tool_call_id"
                    )
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("tool_use history requires name")
                if not isinstance(arguments, Mapping):
                    raise TypeError(
                        "tool_use history arguments must be a mapping"
                    )

                tool_call = ModelToolCall(
                    tool_call_id=tool_call_id,
                    name=name,
                    arguments=dict(arguments),
                )
                pending_tools[tool_call_id] = tool_call
                messages.append(
                    ModelMessage(
                        role="assistant",
                        content="",
                        tool_calls=(tool_call,),
                    )
                )
                continue

            if role == "tool":
                tool_call_id = metadata.get("tool_call_id")
                name = metadata.get("name")

                if (
                    not isinstance(tool_call_id, str)
                    or not tool_call_id.strip()
                ):
                    raise ValueError(
                        "tool history requires tool_call_id metadata"
                    )

                if name is not None and not isinstance(name, str):
                    raise TypeError(
                        "tool history name metadata must be a string or None"
                    )

                tool_call = pending_tools.pop(tool_call_id, None)
                resolved_name = (
                    tool_call.name
                    if tool_call is not None
                    else name
                )

                messages.append(
                    ModelMessage(
                        role="tool",
                        content=content,
                        tool_call_id=tool_call_id,
                        name=resolved_name,
                    )
                )
                continue

            messages.append(
                ModelMessage(
                    role=role,
                    content=content,
                )
            )

        return messages, pending_tools

    def _tool_schemas(self) -> tuple[Mapping[str, object], ...]:
        return tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": dict(tool.input_schema),
            }
            for tool in self._tools.all()
        )

    @staticmethod
    def _context_from_start(
        session_id: str,
        command: RuntimeCommand,
    ) -> ExecutionContext:
        workspace_id = command.payload.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("START command requires workspace_id")

        def optional_string(name: str) -> str | None:
            value = command.payload.get(name)
            return value if isinstance(value, str) else None

        return ExecutionContext(
            workspace_id=workspace_id,
            user_id=optional_string("user_id"),
            agent_id=optional_string("agent_id"),
            session_id=session_id,
            task_id=optional_string("task_id"),
            project_id=optional_string("project_id"),
        )
