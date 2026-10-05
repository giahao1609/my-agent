from __future__ import annotations

import pytest

from core.config import ModelSettings
from integrations.model_backend_factory import build_model_backend
from integrations.openai_compatible_model_backend import (
    OpenAICompatibleModelBackend,
)
from integrations.stdlib_json_transport import StdlibJsonTransport


def test_model_settings_has_safe_defaults() -> None:
    settings = ModelSettings()

    assert settings.backend == "openai-compatible"
    assert settings.base_url == ""
    assert settings.model == ""
    assert settings.api_key is None
    assert settings.timeout_seconds == 30.0


def test_build_model_backend_requires_base_url() -> None:
    settings = ModelSettings(
        model="test-model",
    )

    with pytest.raises(
        ValueError,
        match="base_url",
    ):
        build_model_backend(settings)


def test_build_model_backend_requires_model_name() -> None:
    settings = ModelSettings(
        base_url="https://example.test/v1",
    )

    with pytest.raises(
        ValueError,
        match="model",
    ):
        build_model_backend(settings)


def test_build_model_backend_rejects_unknown_backend_kind() -> None:
    settings = ModelSettings(
        backend="unknown",
        base_url="https://example.test/v1",
        model="test-model",
    )

    with pytest.raises(
        ValueError,
        match="unsupported model backend",
    ):
        build_model_backend(settings)


def test_build_model_backend_creates_openai_compatible_backend() -> None:
    settings = ModelSettings(
        backend="openai-compatible",
        base_url="https://example.test/v1",
        model="test-model",
        api_key="secret",
        timeout_seconds=12.5,
    )

    backend = build_model_backend(settings)

    assert isinstance(
        backend,
        OpenAICompatibleModelBackend,
    )

    assert isinstance(
        backend._transport,
        StdlibJsonTransport,
    )

    assert backend._model == "test-model"
    assert backend._base_url == "https://example.test/v1"
    assert backend._api_key == "secret"
    assert backend._transport._timeout_seconds == 12.5


def test_model_settings_validates_timeout() -> None:
    with pytest.raises(
        ValueError,
        match="timeout",
    ):
        ModelSettings(
            timeout_seconds=0,
        )


def test_model_settings_normalizes_optional_api_key() -> None:
    settings = ModelSettings(
        api_key="   ",
    )

    assert settings.api_key is None


@pytest.mark.asyncio
async def test_build_model_backend_provider_is_lazy() -> None:
    from integrations.model_backend_factory import (
        build_model_backend_provider,
    )

    settings = ModelSettings(
        model="test-model",
    )

    # Building the provider itself must not require complete model config.
    provider = build_model_backend_provider(settings)

    with pytest.raises(
        ValueError,
        match="base_url",
    ):
        await provider.get()


@pytest.mark.asyncio
async def test_build_model_backend_provider_returns_shared_router() -> None:
    from integrations.model_backend_factory import (
        build_model_backend_provider,
    )
    from integrations.routing_model_backend import (
        RoutingModelBackend,
    )

    settings = ModelSettings(
        backend="openai-compatible",
        base_url="https://example.test/v1",
        model="test-model",
        api_key="secret",
        timeout_seconds=8.0,
    )

    provider = build_model_backend_provider(settings)

    first = await provider.get()
    second = await provider.get()

    assert first is second
    assert isinstance(first, RoutingModelBackend)

    # Default leaf backend is built from ModelSettings.
    leaf = first._default

    assert isinstance(
        leaf,
        OpenAICompatibleModelBackend,
    )
    assert leaf._model == "test-model"
    assert leaf._base_url == "https://example.test/v1"
    assert leaf._api_key == "secret"
    assert leaf._transport._timeout_seconds == 8.0
