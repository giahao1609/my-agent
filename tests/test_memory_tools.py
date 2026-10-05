from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from core.context import ExecutionContext
from core.status import CapabilityStatus
from core.tools import ToolPermission
from tools.memory_tools import make_capture_memory_tool


class FakeMemory:
    def __init__(self) -> None:
        self.captured: list[
            tuple[ExecutionContext, tuple[Mapping[str, object], ...]]
        ] = []

    async def recall(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[Mapping[str, object]]:
        return ()

    async def capture(
        self,
        context: ExecutionContext,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        self.captured.append((context, tuple(records)))

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return ()


@pytest.mark.asyncio
async def test_capture_memory_tool_writes_memory_through_backend() -> None:
    memory = FakeMemory()
    tool = make_capture_memory_tool(memory)

    context = ExecutionContext(
        workspace_id="workspace-1",
        project_id="project-1",
        user_id="user-1",
    )

    result = await tool.handler(
        context,
        {
            "kind": "decision",
            "content": "Keep runtime transport separate from orchestration.",
            "level": "l2",
            "importance": 0.9,
            "metadata": {"source": "coder"},
        },
    )

    assert result == {
        "status": "ok",
        "captured": 1,
    }
    assert len(memory.captured) == 1

    captured_context, records = memory.captured[0]
    assert captured_context == context
    assert records == (
        {
            "kind": "decision",
            "content": "Keep runtime transport separate from orchestration.",
            "level": "l2",
            "importance": 0.9,
            "metadata": {"source": "coder"},
        },
    )


def test_capture_memory_tool_is_a_write_tool() -> None:
    tool = make_capture_memory_tool(FakeMemory())

    assert tool.name == "capture_memory"
    assert tool.permissions == frozenset({ToolPermission.WRITE})


def test_coder_tool_registry_includes_capture_memory_when_memory_is_provided() -> None:
    from tools.coder_tools import build_coder_tool_registry

    registry = build_coder_tool_registry(FakeMemory())

    assert "capture_memory" in {tool.name for tool in registry.all()}
