from __future__ import annotations

import pytest

from core.code_graph import CodeNode, CodeNodeKind
from core.context import ExecutionContext
from integrations.code_graph_knowledge import CodeGraphKnowledgeBackend
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore


@pytest.mark.asyncio
async def test_code_graph_knowledge_search_and_get_node(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()

    node = CodeNode(
        project_id="demo",
        node_id="fn-1",
        kind=CodeNodeKind.FUNCTION,
        name="calculate_total",
        path="app.py",
        qualified_name="app.py::calculate_total",
        language="python",
        line_start=10,
        line_end=20,
    )
    await store.replace_project_graph("demo", (node,), ())
    await store.set_graph_status("demo", "ready")

    backend = CodeGraphKnowledgeBackend(store)
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        project_id="demo",
    )

    results = await backend.search(context, "calculate", limit=10)
    fetched = await backend.get_node(context, "fn-1")

    assert len(results) == 1
    assert results[0]["node_id"] == "fn-1"
    assert results[0]["kind"] == "function"
    assert results[0]["path"] == "app.py"

    assert fetched is not None
    assert fetched["qualified_name"] == "app.py::calculate_total"


@pytest.mark.asyncio
async def test_code_graph_knowledge_rejects_missing_project_scope(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    backend = CodeGraphKnowledgeBackend(store)

    context = ExecutionContext(workspace_id=str(tmp_path))

    with pytest.raises(ValueError, match="project_id"):
        await backend.search(context, "anything")


@pytest.mark.asyncio
async def test_code_graph_knowledge_does_not_return_stale_graph(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()

    node = CodeNode(
        project_id="demo",
        node_id="fn-1",
        kind=CodeNodeKind.FUNCTION,
        name="calculate_total",
        path="app.py",
    )
    await store.replace_project_graph("demo", (node,), ())
    await store.set_graph_status("demo", "stale", "filesystem changed")

    backend = CodeGraphKnowledgeBackend(store)
    context = ExecutionContext(
        workspace_id=str(tmp_path),
        project_id="demo",
    )

    assert await backend.search(context, "calculate") == ()
    assert await backend.get_node(context, "fn-1") is None
