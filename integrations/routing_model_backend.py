from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.context import ExecutionContext
from core.model import ExecutionTarget, ModelMessage, ModelTurn
from core.protocols import ModelBackend


class RoutingModelBackend:
    def __init__(
        self,
        *,
        default: ModelBackend,
        runtime_backends: Mapping[str, ModelBackend] | None = None,
        model_backends: Mapping[str, ModelBackend] | None = None,
    ) -> None:
        self._default = default
        self._runtime_backends = dict(runtime_backends or {})
        self._model_backends = dict(model_backends or {})

        duplicates = (
            set(self._runtime_backends)
            & set(self._model_backends)
        )

        if duplicates:
            names = ", ".join(sorted(duplicates))
            raise ValueError(
                f"duplicate backend route keys: {names}"
            )

        for key in (
            *self._runtime_backends.keys(),
            *self._model_backends.keys(),
        ):
            if not key.strip():
                raise ValueError(
                    "backend route key must not be empty"
                )

    async def generate(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, object]],
        context: ExecutionContext,
        *,
        target: ExecutionTarget | None = None,
    ) -> ModelTurn:
        backend = self._resolve(target)

        return await backend.generate(
            messages,
            tools,
            context,
        )

    def _resolve(
        self,
        target: ExecutionTarget | None,
    ) -> ModelBackend:
        if target is None:
            return self._default

        if target.runtime_id is not None:
            backend = self._runtime_backends.get(
                target.runtime_id
            )

            if backend is not None:
                return backend

        if target.model_id is not None:
            backend = self._model_backends.get(
                target.model_id
            )

            if backend is not None:
                return backend

        if (
            target.runtime_id is None
            and target.model_id is None
        ):
            return self._default

        parts: list[str] = []

        if target.runtime_id is not None:
            parts.append(
                f"runtime_id={target.runtime_id}"
            )

        if target.model_id is not None:
            parts.append(
                f"model_id={target.model_id}"
            )

        raise KeyError(
            "unknown model target: "
            + ", ".join(parts)
        )
