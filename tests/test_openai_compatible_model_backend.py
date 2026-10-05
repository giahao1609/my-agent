from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.context import ExecutionContext
from core.model import ModelMessage, ModelToolCall
from integrations.openai_compatible_model_backend import (
    OpenAICompatibleModelBackend,
)


class FakeJsonTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[
            tuple[
                str,
                Mapping[str, str],
                Mapping[str, object],
            ]
        ] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        self.calls.append(
            (
                url,
                dict(headers),
                dict(payload),
            )
        )
        return self.response


def make_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        agent_id="test",
        task_id="task-1",
        project_id="project-1",
    )


@pytest.mark.asyncio
async def test_openai_backend_generates_text_turn() -> None:
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "done",
                    },
                }
            ]
        }
    )

    backend = OpenAICompatibleModelBackend(
        model="test-model",
        base_url="https://example.test/v1",
        api_key="secret",
        transport=transport,
    )

    turn = await backend.generate(
        (
            ModelMessage(
                role="system",
                content="System prompt",
            ),
            ModelMessage(
                role="user",
                content="Hello",
            ),
        ),
        (),
        make_context(),
    )

    assert turn.text == "done"
    assert turn.tool_calls == ()
    assert turn.stop is True

    assert len(transport.calls) == 1

    url, headers, payload = transport.calls[0]

    assert url == "https://example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"

    assert payload["model"] == "test-model"
    assert payload["messages"] == [
        {
            "role": "system",
            "content": "System prompt",
        },
        {
            "role": "user",
            "content": "Hello",
        },
    ]

    assert "tools" not in payload


@pytest.mark.asyncio
async def test_openai_backend_passes_tool_schemas() -> None:
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                    },
                }
            ]
        }
    )

    backend = OpenAICompatibleModelBackend(
        model="test-model",
        base_url="https://example.test/v1/",
        api_key=None,
        transport=transport,
    )

    tools = (
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    )

    await backend.generate(
        (
            ModelMessage(
                role="user",
                content="Inspect code",
            ),
        ),
        tools,
        make_context(),
    )

    _, headers, payload = transport.calls[0]

    assert "Authorization" not in headers
    assert payload["tools"] == list(tools)


@pytest.mark.asyncio
async def test_openai_backend_parses_tool_calls() -> None:
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": (
                                        '{"path":"core/task.py"}'
                                    ),
                                },
                            }
                        ],
                    },
                }
            ]
        }
    )

    backend = OpenAICompatibleModelBackend(
        model="test-model",
        base_url="https://example.test/v1",
        api_key="secret",
        transport=transport,
    )

    turn = await backend.generate(
        (
            ModelMessage(
                role="user",
                content="Read task code",
            ),
        ),
        (),
        make_context(),
    )

    assert turn.text is None
    assert turn.stop is False
    assert turn.tool_calls == (
        ModelToolCall(
            tool_call_id="call-1",
            name="read_file",
            arguments={
                "path": "core/task.py",
            },
        ),
    )


@pytest.mark.asyncio
async def test_openai_backend_serializes_tool_history() -> None:
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "finished",
                    },
                }
            ]
        }
    )

    backend = OpenAICompatibleModelBackend(
        model="test-model",
        base_url="https://example.test/v1",
        api_key=None,
        transport=transport,
    )

    await backend.generate(
        (
            ModelMessage(
                role="assistant",
                content="",
                tool_calls=(
                    ModelToolCall(
                        tool_call_id="call-1",
                        name="read_file",
                        arguments={
                            "path": "core/task.py",
                        },
                    ),
                ),
            ),
            ModelMessage(
                role="tool",
                content='{"content":"source"}',
                tool_call_id="call-1",
                name="read_file",
            ),
        ),
        (),
        make_context(),
    )

    payload = transport.calls[0][2]

    assert payload["messages"][0] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path":"core/task.py"}',
                },
            }
        ],
    }

    assert payload["messages"][1] == {
        "role": "tool",
        "content": '{"content":"source"}',
        "tool_call_id": "call-1",
        "name": "read_file",
    }


@pytest.mark.asyncio
async def test_openai_backend_rejects_invalid_tool_arguments_json() -> None:
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{bad-json",
                                },
                            }
                        ],
                    },
                }
            ]
        }
    )

    backend = OpenAICompatibleModelBackend(
        model="test-model",
        base_url="https://example.test/v1",
        api_key=None,
        transport=transport,
    )

    with pytest.raises(
        ValueError,
        match="arguments",
    ):
        await backend.generate(
            (
                ModelMessage(
                    role="user",
                    content="Read file",
                ),
            ),
            (),
            make_context(),
        )


def test_openai_backend_validates_configuration() -> None:
    transport = FakeJsonTransport({})

    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleModelBackend(
            model="",
            base_url="https://example.test/v1",
            api_key=None,
            transport=transport,
        )

    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleModelBackend(
            model="test-model",
            base_url="",
            api_key=None,
            transport=transport,
        )
