from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from core.protocols import ModelBackendProvider
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
async def test_shared_model_provider_returns_same_backend() -> None:
    backend = FakeModelBackend()
    builds = 0

    async def build():
        nonlocal builds
        builds += 1
        return backend

    provider: ModelBackendProvider = SharedModelBackendProvider(
        build,
    )

    first = await provider.get()
    second = await provider.get()

    assert first is backend
    assert second is backend
    assert first is second
    assert builds == 1


@pytest.mark.asyncio
async def test_shared_model_provider_builds_once_under_concurrency() -> None:
    backend = FakeModelBackend()
    builds = 0

    async def build():
        nonlocal builds
        builds += 1
        await asyncio.sleep(0)
        return backend

    provider = SharedModelBackendProvider(build)

    results = await asyncio.gather(
        provider.get(),
        provider.get(),
        provider.get(),
        provider.get(),
    )

    assert all(result is backend for result in results)
    assert builds == 1


@pytest.mark.asyncio
async def test_shared_model_provider_retries_after_build_failure() -> None:
    backend = FakeModelBackend()
    builds = 0

    async def build():
        nonlocal builds
        builds += 1

        if builds == 1:
            raise RuntimeError("backend unavailable")

        return backend

    provider = SharedModelBackendProvider(build)

    with pytest.raises(
        RuntimeError,
        match="backend unavailable",
    ):
        await provider.get()

    restored = await provider.get()

    assert restored is backend
    assert builds == 2
