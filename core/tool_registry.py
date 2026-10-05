from __future__ import annotations

from collections.abc import Iterable

from .tools import ToolDefinition, ToolExposure


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f'tool already registered: {tool.name}')
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f'unknown tool: {name}') from exc

    def all(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    def exposed_to(self, exposure: ToolExposure) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool for tool in self._tools.values()
            if exposure in tool.exposure
        )

    def register_many(self, tools: Iterable[ToolDefinition]) -> None:
        for tool in tools:
            self.register(tool)
