from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from core.code_graph import CodeNode, CodeNodeKind
from core.context import ExecutionContext
from core.status import Availability, CapabilityStatus


class _CodeGraphStore(Protocol):
    async def get_graph_status(
        self,
        project_id: str,
    ) -> Mapping[str, object]: ...

    async def find_nodes(
        self,
        project_id: str,
        query: str,
        *,
        kinds: Sequence[CodeNodeKind] | None = None,
        limit: int = 50,
    ) -> tuple[CodeNode, ...]: ...

    async def get_node(
        self,
        project_id: str,
        node_id: str,
    ) -> CodeNode | None: ...


def _node_payload(node: CodeNode) -> dict[str, object]:
    return {
        "project_id": node.project_id,
        "node_id": node.node_id,
        "kind": node.kind.value,
        "name": node.name,
        "path": node.path,
        "qualified_name": node.qualified_name,
        "language": node.language,
        "line_start": node.line_start,
        "line_end": node.line_end,
        "metadata": dict(node.metadata),
    }


class CodeGraphKnowledgeBackend:
    def __init__(self, store: _CodeGraphStore) -> None:
        self._store = store

    @staticmethod
    def _project_id(context: ExecutionContext) -> str:
        if context.project_id is None or not context.project_id.strip():
            raise ValueError("project_id is required for Code Graph knowledge")
        return context.project_id

    async def _ready(self, project_id: str) -> bool:
        status = await self._store.get_graph_status(project_id)
        return status.get("status") == "ready"

    async def search(
        self,
        context: ExecutionContext,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[Mapping[str, object], ...]:
        project_id = self._project_id(context)
        if not await self._ready(project_id):
            return ()

        nodes = await self._store.find_nodes(
            project_id,
            query,
            limit=limit,
        )
        return tuple(_node_payload(node) for node in nodes)

    async def get_node(
        self,
        context: ExecutionContext,
        node_id: str,
    ) -> Mapping[str, object] | None:
        project_id = self._project_id(context)
        if not await self._ready(project_id):
            return None

        node = await self._store.get_node(project_id, node_id)
        return _node_payload(node) if node is not None else None

    async def capabilities(self) -> tuple[CapabilityStatus, ...]:
        return (
            CapabilityStatus(
                name="code_graph_knowledge",
                state=Availability.READY,
            ),
        )
