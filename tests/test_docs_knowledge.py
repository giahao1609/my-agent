from __future__ import annotations

import pytest
from pathlib import Path

from core.context import ExecutionContext
from integrations.docs_knowledge_backend import DocsKnowledgeBackend


@pytest.mark.asyncio
async def test_docs_knowledge_backend_indexing_and_search(tmp_path: Path) -> None:
    doc1 = tmp_path / "architecture.md"
    doc1.write_text("# Architecture Document\nMyAgent control plane owns durable lifecycle.", encoding="utf-8")

    doc2 = tmp_path / "README.md"
    doc2.write_text("# MyAgent README\nAI Software Engineering Operating System.", encoding="utf-8")

    backend = DocsKnowledgeBackend(workspace_root=tmp_path)
    count = backend.index_workspace_docs()
    assert count == 2

    context = ExecutionContext(project_id="proj-1", workspace_id="ws-1")
    results = await backend.search(context, "lifecycle control plane", limit=5)
    assert len(results) >= 1
    assert "architecture.md" in results[0]["file_path"] or "README.md" in results[0]["file_path"]
    assert results[0]["source_attribution"].startswith("file://")

    node_id = results[0]["node_id"]
    node = await backend.get_node(context, node_id)
    assert node is not None
    assert "title" in node


@pytest.mark.asyncio
async def test_docs_knowledge_backend_capabilities(tmp_path: Path) -> None:
    backend = DocsKnowledgeBackend(workspace_root=tmp_path)
    caps = await backend.capabilities()
    assert len(caps) >= 3
    assert any(c.name == "docs_indexing" for c in caps)
