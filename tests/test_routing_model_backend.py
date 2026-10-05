from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from integrations.routing_model_backend import RoutingModelBackend


class FakeBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[
            tuple[
                tuple[ModelMessage, ...],
                tuple[Mapping[str, object], ...],
                ExecutionContext,
                ExecutionTarget | None,
            ]
        ] = []

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        self.calls.append(
            (
                tuple(messages),
                tuple(tools),
                context,
                target,
            )
        )
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


@pytest.mark.asyncio
async def test_routing_model_backend_uses_default_backend() -> None:
    default = FakeBackend("default")

    router = RoutingModelBackend(
        default=default,
    )

    turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
    )

    assert turn.text == "default"
    assert len(default.calls) == 1

    # The selected backend receives the unresolved call.
    assert default.calls[0][3] is None


@pytest.mark.asyncio
async def test_routing_model_backend_routes_by_runtime_id() -> None:
    default = FakeBackend("default")
    runtime_backend = FakeBackend("runtime")

    router = RoutingModelBackend(
        default=default,
        runtime_backends={
            "codex": runtime_backend,
        },
    )

    target = ExecutionTarget(
        runtime_id="codex",
    )

    turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
        target=target,
    )

    assert turn.text == "runtime"
    assert len(runtime_backend.calls) == 1
    assert default.calls == []

    # Routing metadata is consumed by the router.
    assert runtime_backend.calls[0][3] is None


@pytest.mark.asyncio
async def test_routing_model_backend_routes_by_model_id() -> None:
    default = FakeBackend("default")
    model_backend = FakeBackend("model")

    router = RoutingModelBackend(
        default=default,
        model_backends={
            "planning-model": model_backend,
        },
    )

    target = ExecutionTarget(
        model_id="planning-model",
    )

    turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
        target=target,
    )

    assert turn.text == "model"
    assert len(model_backend.calls) == 1
    assert default.calls == []


@pytest.mark.asyncio
async def test_runtime_route_has_priority_over_model_route() -> None:
    default = FakeBackend("default")
    runtime_backend = FakeBackend("runtime")
    model_backend = FakeBackend("model")

    router = RoutingModelBackend(
        default=default,
        runtime_backends={
            "codex": runtime_backend,
        },
        model_backends={
            "planning-model": model_backend,
        },
    )

    turn = await router.generate(
        (ModelMessage(role="user", content="hello"),),
        (),
        make_context(),
        target=ExecutionTarget(
            runtime_id="codex",
            model_id="planning-model",
        ),
    )

    assert turn.text == "runtime"

    assert len(runtime_backend.calls) == 1
    assert model_backend.calls == []
    assert default.calls == []


@pytest.mark.asyncio
async def test_unknown_explicit_target_is_rejected() -> None:
    router = RoutingModelBackend(
        default=FakeBackend("default"),
    )

    with pytest.raises(
        KeyError,
        match="unknown model target",
    ):
        await router.generate(
            (ModelMessage(role="user", content="hello"),),
            (),
            make_context(),
            target=ExecutionTarget(
                model_id="missing-model",
            ),
        )


@pytest.mark.asyncio
async def test_router_rejects_duplicate_backend_keys() -> None:
    backend = FakeBackend("shared")

    with pytest.raises(
        ValueError,
        match="duplicate",
    ):
        RoutingModelBackend(
            default=FakeBackend("default"),
            runtime_backends={
                "shared": backend,
            },
            model_backends={
                "shared": backend,
            },
        )
