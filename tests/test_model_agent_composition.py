from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from agents.model_agent_composition import (
    ModelAgentComposition,
    build_model_agent_composition,
)
from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from integrations.shared_model_backend_provider import (
    SharedModelBackendProvider,
)


class FakeModelBackend:
    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        return ModelTurn(text="ok", stop=True)


@pytest.mark.asyncio
async def test_model_agent_composition_shares_backend_between_consumers() -> None:
    backend = FakeModelBackend()
    builds = 0

    async def build_backend():
        nonlocal builds
        builds += 1
        return backend

    provider = SharedModelBackendProvider(build_backend)

    runtime = object()
    tools = object()

    composition = await build_model_agent_composition(
        provider=provider,
        runtime=runtime,
        tools=tools,
    )

    assert isinstance(composition, ModelAgentComposition)

    assert composition.model is backend

    # Both consumers must use the exact backend owned by the provider.
    assert composition.coder_worker._model is backend
    assert composition.planner._model is backend

    assert composition.coder_worker._runtime is runtime
    assert composition.coder_worker._tools is tools

    assert builds == 1


@pytest.mark.asyncio
async def test_multiple_compositions_reuse_provider_backend() -> None:
    backend = FakeModelBackend()
    builds = 0

    async def build_backend():
        nonlocal builds
        builds += 1
        return backend

    provider = SharedModelBackendProvider(build_backend)

    first = await build_model_agent_composition(
        provider=provider,
        runtime=object(),
        tools=object(),
    )

    second = await build_model_agent_composition(
        provider=provider,
        runtime=object(),
        tools=object(),
    )

    assert first.model is backend
    assert second.model is backend

    assert first.coder_worker is not second.coder_worker
    assert first.planner is not second.planner

    # Backend lifecycle belongs to the provider, not each consumer.
    assert builds == 1


@pytest.mark.asyncio
async def test_model_agent_composition_passes_planner_target() -> None:
    backend = FakeModelBackend()

    async def build_backend():
        return backend

    provider = SharedModelBackendProvider(build_backend)

    target = ExecutionTarget(
        runtime_id="planning-runtime",
        model_id="planning-model",
    )

    composition = await build_model_agent_composition(
        provider=provider,
        runtime=object(),
        tools=object(),
        planner_target=target,
    )

    assert composition.planner._target == target
