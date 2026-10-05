from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from integrations.model_backend_registry import ModelBackendRegistry
from integrations.routing_model_backend import RoutingModelBackend


class FakeBackend:
    def __init__(self, name: str) -> None:
        self.name = name

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        return ModelTurn(
            text=self.name,
            stop=True,
        )


def make_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        agent_id="test",
        project_id="project-1",
    )


def test_registry_requires_default_backend() -> None:
    registry = ModelBackendRegistry()

    with pytest.raises(
        ValueError,
        match="default",
    ):
        registry.build_router()


def test_registry_registers_default_backend() -> None:
    backend = FakeBackend("default")

    registry = ModelBackendRegistry()
    registry.set_default(backend)

    router = registry.build_router()

    assert isinstance(router, RoutingModelBackend)


def test_registry_rejects_second_default_backend() -> None:
    registry = ModelBackendRegistry()
    registry.set_default(FakeBackend("first"))

    with pytest.raises(
        ValueError,
        match="default",
    ):
        registry.set_default(FakeBackend("second"))


def test_registry_registers_runtime_route() -> None:
    registry = ModelBackendRegistry()

    backend = FakeBackend("runtime")

    registry.set_default(FakeBackend("default"))
    registry.register_runtime(
        "codex",
        backend,
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register_runtime(
            "codex",
            FakeBackend("other"),
        )


def test_registry_registers_model_route() -> None:
    registry = ModelBackendRegistry()

    backend = FakeBackend("model")

    registry.set_default(FakeBackend("default"))
    registry.register_model(
        "planning-model",
        backend,
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register_model(
            "planning-model",
            FakeBackend("other"),
        )


def test_registry_rejects_cross_namespace_duplicate_key() -> None:
    registry = ModelBackendRegistry()

    registry.set_default(FakeBackend("default"))
    registry.register_runtime(
        "shared",
        FakeBackend("runtime"),
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register_model(
            "shared",
            FakeBackend("model"),
        )


@pytest.mark.asyncio
async def test_registry_builds_router_with_all_routes() -> None:
    default = FakeBackend("default")
    runtime = FakeBackend("runtime")
    model = FakeBackend("model")

    registry = ModelBackendRegistry()

    registry.set_default(default)
    registry.register_runtime(
        "codex",
        runtime,
    )
    registry.register_model(
        "planning-model",
        model,
    )

    router = registry.build_router()

    default_turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
    )

    runtime_turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
        target=ExecutionTarget(
            runtime_id="codex",
        ),
    )

    model_turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
        target=ExecutionTarget(
            model_id="planning-model",
        ),
    )

    assert default_turn.text == "default"
    assert runtime_turn.text == "runtime"
    assert model_turn.text == "model"


def test_registry_rejects_empty_route_key() -> None:
    registry = ModelBackendRegistry()
    registry.set_default(FakeBackend("default"))

    with pytest.raises(
        ValueError,
        match="empty",
    ):
        registry.register_runtime(
            "",
            FakeBackend("runtime"),
        )

    with pytest.raises(
        ValueError,
        match="empty",
    ):
        registry.register_model(
            "   ",
            FakeBackend("model"),
        )
