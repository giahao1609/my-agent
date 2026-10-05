from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path

from .code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from .language_scanner import ScanResult


def _stable_node_id(project_id: str, kind: CodeNodeKind, key: str) -> str:
    raw = f"{project_id}:{kind.value}:{key}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


class GoHttpRouteTableScanner:
    """Detect Go composite-literal HTTP prefix route tables."""

    name = "go_http_route_table"

    def supports(self, root: Path, path: Path, language: str | None) -> bool:
        return language == "go"

    def scan_file(
        self,
        project_id: str,
        root: Path,
        path: Path,
        source: str,
        language: str | None,
    ) -> ScanResult:
        if language != "go" or "Prefix:" not in source:
            return ScanResult(nodes=(), edges=())

        root = Path(root).resolve()
        rel = Path(path).resolve().relative_to(root).as_posix()
        nodes: list[CodeNode] = []

        for line_no, line in enumerate(source.splitlines(), start=1):
            prefix_match = re.search(r'\bPrefix\s*:\s*"([^"]+)"', line)
            if prefix_match is None:
                continue

            prefix = prefix_match.group(1)
            method_match = re.search(
                r'\bMethod\s*:\s*http\.Method([A-Za-z]+)',
                line,
            )
            method = (
                method_match.group(1).upper()
                if method_match is not None
                else ""
            )
            backend_match = re.search(r'\bBackend\s*:\s*"([^"]+)"', line)
            backend = (
                backend_match.group(1)
                if backend_match is not None
                else ""
            )

            key = f"{rel}:{line_no}:{method}:{prefix}:{backend}"
            node_id = _stable_node_id(project_id, CodeNodeKind.ROUTE, key)
            qualified = f"{method or '*'} {prefix}"

            metadata = {
                "framework": self.name,
                "adapter": "go_composite_literal",
                "route_match": "prefix",
                "uri_prefix": prefix,
                "http_method": method,
            }
            if backend:
                metadata["backend"] = backend

            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=node_id,
                    kind=CodeNodeKind.ROUTE,
                    name=qualified,
                    path=rel,
                    qualified_name=qualified,
                    language="go",
                    line_start=line_no,
                    line_end=line_no,
                    metadata=metadata,
                )
            )

        return ScanResult(nodes=tuple(nodes), edges=())


def resolve_http_route_targets(
    nodes: list[CodeNode],
    edges: list[CodeEdge],
) -> tuple[list[CodeNode], list[CodeEdge]]:
    routes = [
        node
        for node in nodes
        if node.kind is CodeNodeKind.ROUTE
        and node.metadata.get("route_match") == "prefix"
        and node.metadata.get("uri_prefix")
    ]
    by_id = {node.node_id: node for node in nodes}
    removed_external: set[str] = set()
    resolved_edges: list[CodeEdge] = []

    for edge in edges:
        if edge.kind is not CodeEdgeKind.CALLS:
            resolved_edges.append(edge)
            continue

        target = by_id.get(edge.target_id)
        if (
            target is None
            or target.kind is not CodeNodeKind.EXTERNAL
            or target.metadata.get("http_dependency") is not True
        ):
            resolved_edges.append(edge)
            continue

        uri = str(target.metadata.get("uri") or "")
        method = str(target.metadata.get("http_method") or "GET").upper()
        candidates: list[tuple[int, bool, CodeNode]] = []

        for route in routes:
            prefix = str(route.metadata.get("uri_prefix") or "")
            route_method = str(route.metadata.get("http_method") or "").upper()

            if not uri.startswith(prefix):
                continue
            if route_method and route_method != method:
                continue

            candidates.append((len(prefix), bool(route_method), route))

        if not candidates:
            resolved_edges.append(edge)
            continue

        _, _, route = max(candidates, key=lambda item: (item[0], item[1]))
        metadata = dict(edge.metadata or {})
        metadata.update(
            resolved=True,
            resolution="http_prefix_route",
            matched_prefix=route.metadata.get("uri_prefix"),
        )
        if route.metadata.get("backend"):
            metadata["backend"] = route.metadata["backend"]

        removed_external.add(target.node_id)
        resolved_edges.append(
            replace(edge, target_id=route.node_id, metadata=metadata)
        )

    referenced = {
        endpoint
        for edge in resolved_edges
        for endpoint in (edge.source_id, edge.target_id)
    }
    resolved_nodes = [
        node
        for node in nodes
        if node.node_id not in removed_external or node.node_id in referenced
    ]
    return resolved_nodes, resolved_edges
