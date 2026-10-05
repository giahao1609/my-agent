from __future__ import annotations

import pytest

from my_agent_mcp import server


class FakeCodeGraph:
    def __init__(self, status: str) -> None:
        self.status = status

    async def get_graph_status(self, project_id: str) -> dict[str, object]:
        return {
            "status": self.status,
            "updated_at": None,
            "error": None,
            "node_count": 0,
            "edge_count": 0,
            "file_count": 0,
        }


@pytest.mark.asyncio
async def test_graph_read_guard_allows_ready_graph(monkeypatch):
    monkeypatch.setattr(server, "code_graph", FakeCodeGraph("ready"))

    assert await server._graph_read_guard("demo") is None


@pytest.mark.asyncio
async def test_graph_read_guard_blocks_stale_graph(monkeypatch):
    monkeypatch.setattr(server, "code_graph", FakeCodeGraph("stale"))

    result = await server._graph_read_guard("demo")

    assert result is not None
    assert result["status"] == "stale"
    assert result["project_id"] == "demo"
    assert result["graph_status"]["status"] == "stale"
