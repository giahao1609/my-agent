from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field



@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    runtime_id: str | None = None
    model_id: str | None = None

    def __post_init__(self) -> None:
        if self.runtime_id is not None and not self.runtime_id.strip():
            raise ValueError("runtime_id must not be empty")
        if self.model_id is not None and not self.model_id.strip():
            raise ValueError("model_id must not be empty")


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    tool_call_id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError("tool_call_id must not be empty")
        if not self.name.strip():
            raise ValueError("tool name must not be empty")


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("message role must not be empty")


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    stop: bool = False
