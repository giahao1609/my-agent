from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


class CoderRuntimeMode(StrEnum):
    EXTERNAL = "external"
    IN_PROCESS = "in_process"


def _get_coder_runtime_mode() -> CoderRuntimeMode:
    value = os.getenv("CODER_RUNTIME_MODE")

    if value is None:
        return CoderRuntimeMode.EXTERNAL

    normalized = value.strip().lower().replace("-", "_")

    try:
        return CoderRuntimeMode(normalized)
    except ValueError as exc:
        raise ValueError(
            "CODER_RUNTIME_MODE must be external or in_process"
        ) from exc


@dataclass(frozen=True, slots=True)
class ModelSettings:
    backend: str = "openai-compatible"
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        backend = self.backend.strip()
        base_url = self.base_url.strip()
        model = self.model.strip()

        api_key = (
            self.api_key.strip()
            if self.api_key is not None
            else None
        )

        if not backend:
            raise ValueError(
                "model backend must not be empty"
            )

        if self.timeout_seconds <= 0:
            raise ValueError(
                "model timeout_seconds must be greater than zero"
            )

        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(
            self,
            "api_key",
            api_key or None,
        )


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    coder_runtime_mode: CoderRuntimeMode = (
        _get_coder_runtime_mode()
    )

    memory_backend: str = os.getenv("MEMORY_BACKEND", "local")
    memory_path: Path = Path(
        os.getenv("MEMORY_PATH", "./data/memory")
    )

    code_graph_path: Path = Path(
        os.getenv("CODE_GRAPH_PATH", "./data/code_graph")
    )

    sandbox_enabled: bool = _get_bool(
        "SANDBOX_ENABLED",
        True,
    )
    sandbox_timeout: int = _get_int(
        "SANDBOX_TIMEOUT",
        120,
    )

    allow_terminal: bool = _get_bool(
        "ALLOW_TERMINAL",
        True,
    )
    allow_filesystem: bool = _get_bool(
        "ALLOW_FILESYSTEM",
        True,
    )
    allow_network: bool = _get_bool(
        "ALLOW_NETWORK",
        False,
    )

    mcp_host: str = os.getenv(
        "MCP_HOST",
        "127.0.0.1",
    )
    mcp_port: int = _get_int(
        "MCP_PORT",
        8765,
    )

    model_settings: ModelSettings = ModelSettings(
        backend=os.getenv(
            "MODEL_BACKEND",
            "openai-compatible",
        ),
        base_url=os.getenv(
            "MODEL_BASE_URL",
            "",
        ),
        model=os.getenv(
            "MODEL_NAME",
            "",
        ),
        api_key=os.getenv(
            "MODEL_API_KEY",
        ),
        timeout_seconds=_get_float(
            "MODEL_TIMEOUT",
            30.0,
        ),
    )


settings = Settings()
