from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from core.context import ExecutionContext
from core.model import (
    ExecutionTarget,
    ModelMessage,
    ModelToolCall,
    ModelTurn,
)


class JsonTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        ...


class OpenAICompatibleModelBackend:
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None,
        transport: JsonTransport,
    ) -> None:
        resolved_model = model.strip()
        resolved_base_url = base_url.strip()

        if not resolved_model:
            raise ValueError("model must not be empty")

        if not resolved_base_url:
            raise ValueError("base_url must not be empty")

        self._model = resolved_model
        self._base_url = resolved_base_url.rstrip("/")
        self._api_key = (
            api_key.strip()
            if api_key is not None and api_key.strip()
            else None
        )
        self._transport = transport

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        # RoutingModelBackend owns ExecutionTarget resolution.
        # This leaf backend intentionally ignores target.
        del context, target

        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                self._serialize_message(message)
                for message in messages
            ],
        }

        if tools:
            payload["tools"] = list(tools)

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }

        if self._api_key is not None:
            headers["Authorization"] = (
                f"Bearer {self._api_key}"
            )

        response = await self._transport.post_json(
            f"{self._base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )

        return self._parse_response(response)

    @staticmethod
    def _serialize_message(
        message: ModelMessage,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": message.role,
            "content": message.content,
        }

        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id

        if message.name is not None:
            payload["name"] = message.name

        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tool_call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(
                            dict(tool_call.arguments),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                }
                for tool_call in message.tool_calls
            ]

        return payload

    @classmethod
    def _parse_response(
        cls,
        response: Mapping[str, object],
    ) -> ModelTurn:
        raw_choices = response.get("choices")

        if not isinstance(raw_choices, list) or not raw_choices:
            raise ValueError(
                "model response must contain choices"
            )

        choice = raw_choices[0]

        if not isinstance(choice, Mapping):
            raise ValueError(
                "model response choice must be an object"
            )

        raw_message = choice.get("message")

        if not isinstance(raw_message, Mapping):
            raise ValueError(
                "model response choice must contain message"
            )

        raw_content = raw_message.get("content")

        if raw_content is not None and not isinstance(
            raw_content,
            str,
        ):
            raise ValueError(
                "model response content must be a string or null"
            )

        raw_tool_calls = raw_message.get(
            "tool_calls",
            [],
        )

        if not isinstance(raw_tool_calls, list):
            raise ValueError(
                "model response tool_calls must be a list"
            )

        tool_calls = tuple(
            cls._parse_tool_call(raw_tool_call)
            for raw_tool_call in raw_tool_calls
        )

        finish_reason = choice.get("finish_reason")

        if finish_reason is not None and not isinstance(
            finish_reason,
            str,
        ):
            raise ValueError(
                "model response finish_reason must be a string or null"
            )

        return ModelTurn(
            text=raw_content,
            tool_calls=tool_calls,
            stop=finish_reason == "stop",
        )

    @staticmethod
    def _parse_tool_call(
        raw_tool_call: object,
    ) -> ModelToolCall:
        if not isinstance(raw_tool_call, Mapping):
            raise ValueError(
                "model tool call must be an object"
            )

        tool_call_id = raw_tool_call.get("id")

        if not isinstance(tool_call_id, str):
            raise ValueError(
                "model tool call id must be a string"
            )

        raw_function = raw_tool_call.get("function")

        if not isinstance(raw_function, Mapping):
            raise ValueError(
                "model tool call function must be an object"
            )

        name = raw_function.get("name")

        if not isinstance(name, str):
            raise ValueError(
                "model tool call name must be a string"
            )

        raw_arguments = raw_function.get(
            "arguments",
            "{}",
        )

        if not isinstance(raw_arguments, str):
            raise ValueError(
                "model tool call arguments must be JSON text"
            )

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "model tool call arguments must be valid JSON"
            ) from exc

        if not isinstance(arguments, Mapping):
            raise ValueError(
                "model tool call arguments must decode to an object"
            )

        return ModelToolCall(
            tool_call_id=tool_call_id,
            name=name,
            arguments=dict(arguments),
        )
