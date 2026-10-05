from __future__ import annotations

from collections.abc import Mapping

from core.context import ExecutionContext
from core.memory import MemoryLevel
from core.protocols import MemoryBackend
from core.tools import ToolDefinition, ToolPermission


def make_capture_memory_tool(memory: MemoryBackend) -> ToolDefinition:
    async def capture_memory(
        context: ExecutionContext,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        kind = arguments.get("kind")
        content = arguments.get("content")

        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")

        record: dict[str, object] = {
            "kind": kind,
            "content": content,
        }

        if "level" in arguments:
            record["level"] = arguments["level"]

        if "importance" in arguments:
            record["importance"] = arguments["importance"]

        if "metadata" in arguments:
            record["metadata"] = arguments["metadata"]

        await memory.capture(
            context,
            (record,),
        )

        return {
            "status": "ok",
            "captured": 1,
        }

    return ToolDefinition(
        name="capture_memory",
        description=(
            "Persist an important project memory such as a decision, "
            "constraint, preference, fact, or durable implementation note."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                },
                "content": {
                    "type": "string",
                },
                "level": {
                    "type": "string",
                    "enum": [level.value for level in MemoryLevel],
                },
                "importance": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "metadata": {
                    "type": "object",
                },
            },
            "required": [
                "kind",
                "content",
            ],
        },
        handler=capture_memory,
        permissions=frozenset({ToolPermission.WRITE}),
    )
