from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from core.context import ExecutionContext
from core.protocols import KnowledgeBackend
from core.status import Availability, CapabilityStatus


class DocsKnowledgeBackend(KnowledgeBackend):
    __test__ = False
    """KnowledgeBackend implementation for indexing project documentation and markdown files."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        self._doc_nodes: dict[str, dict[str, Any]] = {}
        self._indexed = False

    def index_workspace_docs(self) -> int:
        self._doc_nodes.clear()
        if not self._workspace_root.exists():
            return 0

        indexed_count = 0
        doc_paths = list(self._workspace_root.rglob("*.md")) + list(self._workspace_root.rglob("*.txt"))

        for path in doc_paths:
            if any(part in path.parts for part in (".venv", "node_modules", ".git", "__pycache__")):
                continue

            try:
                rel_path = str(path.relative_to(self._workspace_root))
            except ValueError:
                rel_path = str(path)

            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                node_id = f"doc-{uuid.uuid5(uuid.NAMESPACE_URL, rel_path).hex[:12]}"
                title = path.name

                for line in content.splitlines()[:5]:
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break

                self._doc_nodes[node_id] = {
                    "node_id": node_id,
                    "title": title,
                    "file_path": rel_path,
                    "content": content,
                    "source_attribution": f"file://{rel_path}",
                }
                indexed_count += 1
            except Exception:
                pass

        self._indexed = True
        return indexed_count

    async def search(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[dict[str, Any]]:
        if not self._indexed:
            self.index_workspace_docs()

        query_terms = [t.lower() for t in query.split() if len(t) > 2]
        results: list[dict[str, Any]] = []

        for node in self._doc_nodes.values():
            content_lower = node["content"].lower()
            title_lower = node["title"].lower()
            matches = sum(1 for term in query_terms if term in content_lower or term in title_lower)

            if matches > 0 or not query_terms:
                snippet = node["content"][:300].replace("\n", " ")
                results.append({
                    "node_id": node["node_id"],
                    "title": node["title"],
                    "file_path": node["file_path"],
                    "snippet": snippet,
                    "source_attribution": node["source_attribution"],
                    "relevance_score": matches,
                })

        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        return results[:limit]

    async def get_node(
        self,
        context: ExecutionContext,
        node_id: str,
    ) -> dict[str, Any] | None:
        if not self._indexed:
            self.index_workspace_docs()
        return self._doc_nodes.get(node_id)

    async def capabilities(self) -> Sequence[CapabilityStatus]:
        return (
            CapabilityStatus(name="docs_indexing", state=Availability.READY),
            CapabilityStatus(name="markdown_progressive_retrieval", state=Availability.READY),
            CapabilityStatus(name="source_attribution", state=Availability.READY),
        )
