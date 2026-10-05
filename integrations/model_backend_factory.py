from __future__ import annotations

from core.config import ModelSettings
from core.protocols import ModelBackend

from .model_backend_registry import ModelBackendRegistry
from .openai_compatible_model_backend import (
    OpenAICompatibleModelBackend,
)
from .shared_model_backend_provider import (
    SharedModelBackendProvider,
)
from .stdlib_json_transport import StdlibJsonTransport


def build_model_backend(
    settings: ModelSettings,
) -> ModelBackend:
    if settings.backend != "openai-compatible":
        raise ValueError(
            f"unsupported model backend: {settings.backend}"
        )

    if not settings.base_url:
        raise ValueError(
            "model base_url is required"
        )

    if not settings.model:
        raise ValueError(
            "model name is required"
        )

    transport = StdlibJsonTransport(
        timeout_seconds=settings.timeout_seconds,
    )

    return OpenAICompatibleModelBackend(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        transport=transport,
    )



def build_model_backend_provider(
    settings: ModelSettings,
) -> SharedModelBackendProvider:
    async def build():
        registry = ModelBackendRegistry()

        registry.set_default(
            build_model_backend(settings)
        )

        return registry.build_router()

    return SharedModelBackendProvider(build)
