from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import SimpleNamespace

import pytest

from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from my_agent_mcp import server


class FakeModelBackend:
    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        return ModelTurn(
            text="ok",
            stop=True,
        )


class FakeProvider:
    def __init__(self, backend) -> None:
        self.backend = backend
        self.calls = 0

    async def get(self):
        self.calls += 1
        return self.backend


@pytest.mark.asyncio
async def test_server_model_composition_is_lazy_and_cached(
    monkeypatch,
) -> None:
    backend = FakeModelBackend()
    provider = FakeProvider(backend)

    runtime = object()
    tools = object()

    stack = SimpleNamespace(
        runtime=runtime,
        tool_registry=tools,
    )

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_model_provider",
        provider,
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_model_composition",
        None,
        raising=False,
    )

    # Merely configuring the server must not build the backend.
    assert provider.calls == 0

    first = await server._get_model_composition()
    second = await server._get_model_composition()

    assert first is second
    assert provider.calls == 1

    assert first.model is backend
    assert first.coder_worker._model is backend
    assert first.planner._model is backend

    assert first.coder_worker._runtime is runtime
    assert first.coder_worker._tools is tools


@pytest.mark.asyncio
async def test_server_model_composition_does_not_start_coder_worker(
    monkeypatch,
) -> None:
    backend = FakeModelBackend()
    provider = FakeProvider(backend)

    stack = SimpleNamespace(
        runtime=object(),
        tool_registry=object(),
    )

    async def fake_get_coder_stack():
        return stack

    monkeypatch.setattr(
        server,
        "_get_coder_stack",
        fake_get_coder_stack,
    )
    monkeypatch.setattr(
        server,
        "_model_provider",
        provider,
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_model_composition",
        None,
        raising=False,
    )

    composition = await server._get_model_composition()

    # Composition only wires the worker.
    # Running a coder session remains a separate explicit lifecycle.
    assert composition.coder_worker is not None
    assert provider.calls == 1
