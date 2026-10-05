from __future__ import annotations

import json

import pytest

from integrations.stdlib_json_transport import StdlibJsonTransport


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


@pytest.mark.asyncio
async def test_stdlib_json_transport_posts_json() -> None:
    calls = []

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return FakeResponse(
            b'{"choices":[{"message":{"content":"ok"}}]}'
        )

    transport = StdlibJsonTransport(
        timeout_seconds=12.5,
        opener=opener,
    )

    response = await transport.post_json(
        "https://example.test/v1/chat/completions",
        headers={
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
        },
        payload={
            "model": "test-model",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        },
    )

    assert response == {
        "choices": [
            {
                "message": {
                    "content": "ok",
                }
            }
        ]
    }

    assert len(calls) == 1

    request, timeout = calls[0]

    assert timeout == 12.5
    assert request.full_url == (
        "https://example.test/v1/chat/completions"
    )
    assert request.get_method() == "POST"

    headers = {
        key.lower(): value
        for key, value in request.header_items()
    }

    assert headers["authorization"] == "Bearer secret"
    assert headers["content-type"] == "application/json"

    assert json.loads(
        request.data.decode("utf-8")
    ) == {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": "Hello",
            }
        ],
    }


@pytest.mark.asyncio
async def test_stdlib_json_transport_uses_utf8_json() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["body"] = request.data
        return FakeResponse(b'{"ok":true}')

    transport = StdlibJsonTransport(
        opener=opener,
    )

    await transport.post_json(
        "https://example.test/api",
        headers={},
        payload={
            "message": "Xin chào",
        },
    )

    assert json.loads(
        captured["body"].decode("utf-8")
    ) == {
        "message": "Xin chào",
    }


@pytest.mark.asyncio
async def test_stdlib_json_transport_rejects_invalid_json_response() -> None:
    def opener(request, *, timeout):
        return FakeResponse(b"not-json")

    transport = StdlibJsonTransport(
        opener=opener,
    )

    with pytest.raises(
        ValueError,
        match="valid JSON",
    ):
        await transport.post_json(
            "https://example.test/api",
            headers={},
            payload={},
        )


@pytest.mark.asyncio
async def test_stdlib_json_transport_requires_object_response() -> None:
    def opener(request, *, timeout):
        return FakeResponse(b'["not","object"]')

    transport = StdlibJsonTransport(
        opener=opener,
    )

    with pytest.raises(
        ValueError,
        match="JSON object",
    ):
        await transport.post_json(
            "https://example.test/api",
            headers={},
            payload={},
        )


def test_stdlib_json_transport_validates_timeout() -> None:
    with pytest.raises(
        ValueError,
        match="timeout",
    ):
        StdlibJsonTransport(
            timeout_seconds=0,
        )
