from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .context import ExecutionContext


class ToolPermission(StrEnum):
    READ = 'read'
    WRITE = 'write'
    EXECUTE = 'execute'
    NETWORK = 'network'
    DESTRUCTIVE = 'destructive'


class ToolExposure(StrEnum):
    INTERNAL = 'internal'
    CLI = 'cli'
    MCP = 'mcp'
    API = 'api'


ToolResult = Mapping[str, object]
ToolHandler = Callable[[ExecutionContext, Mapping[str, object]], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: ToolHandler
    permissions: frozenset[ToolPermission] = field(default_factory=frozenset)
    exposure: frozenset[ToolExposure] = field(
        default_factory=lambda: frozenset({ToolExposure.INTERNAL})
    )

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError('tool name must not be empty')
        if not self.description.strip():
            raise ValueError('tool description must not be empty')
        if not self.exposure:
            raise ValueError('tool must have at least one exposure')

    @property
    def requires_approval(self) -> bool:
        return bool(
            self.permissions
            & {ToolPermission.DESTRUCTIVE}
        )
