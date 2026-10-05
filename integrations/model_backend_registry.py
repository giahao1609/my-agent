from __future__ import annotations

from core.protocols import ModelBackend

from .routing_model_backend import RoutingModelBackend


class ModelBackendRegistry:
    def __init__(self) -> None:
        self._default: ModelBackend | None = None
        self._runtime_backends: dict[str, ModelBackend] = {}
        self._model_backends: dict[str, ModelBackend] = {}

    def set_default(
        self,
        backend: ModelBackend,
    ) -> None:
        if self._default is not None:
            raise ValueError(
                "default backend is already registered"
            )

        self._default = backend

    def register_runtime(
        self,
        runtime_id: str,
        backend: ModelBackend,
    ) -> None:
        key = self._normalize_key(runtime_id)

        if (
            key in self._runtime_backends
            or key in self._model_backends
        ):
            raise ValueError(
                f"backend route already registered: {key}"
            )

        self._runtime_backends[key] = backend

    def register_model(
        self,
        model_id: str,
        backend: ModelBackend,
    ) -> None:
        key = self._normalize_key(model_id)

        if (
            key in self._runtime_backends
            or key in self._model_backends
        ):
            raise ValueError(
                f"backend route already registered: {key}"
            )

        self._model_backends[key] = backend

    def build_router(self) -> RoutingModelBackend:
        if self._default is None:
            raise ValueError(
                "default backend must be registered"
            )

        return RoutingModelBackend(
            default=self._default,
            runtime_backends=dict(
                self._runtime_backends
            ),
            model_backends=dict(
                self._model_backends
            ),
        )

    @staticmethod
    def _normalize_key(value: str) -> str:
        key = value.strip()

        if not key:
            raise ValueError(
                "backend route key must not be empty"
            )

        return key
