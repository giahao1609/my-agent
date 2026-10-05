from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from .language_scanner import ScanResult

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def stable_node_id(project_id: str, kind: CodeNodeKind, key: str) -> str:
    raw = f"{project_id}:{kind.value}:{key}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


class NextFrameworkScanner:
    name = "next"

    def _next_root(self, root: Path, path: Path) -> Path | None:
        root = Path(root).resolve()
        current = Path(path).resolve().parent

        while current == root or root in current.parents:
            manifest = current / "package.json"
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return None
                deps = {
                    **data.get("dependencies", {}),
                    **data.get("devDependencies", {}),
                }
                if "next" in deps:
                    return current
            if current == root:
                break
            current = current.parent

        return None

    def supports(self, root: Path, path: Path, language: str | None) -> bool:
        if language not in {"javascript", "typescript", "tsx"}:
            return False
        if Path(path).name not in {
            "route.js",
            "route.jsx",
            "route.ts",
            "route.tsx",
            "page.js",
            "page.jsx",
            "page.ts",
            "page.tsx",
        }:
            return False
        return self._next_root(root, path) is not None

    def scan_file(
        self,
        project_id: str,
        root: Path,
        path: Path,
        source: str,
        language: str | None,
    ) -> ScanResult:
        if not self.supports(root, path, language):
            return ScanResult(nodes=(), edges=())

        root = Path(root).resolve()
        rel = Path(path).resolve().relative_to(root).as_posix()
        parts = Path(rel).parts

        try:
            app_index = max(i for i, part in enumerate(parts) if part == "app")
        except ValueError:
            return ScanResult(nodes=(), edges=())

        route_parts = [
            part
            for part in parts[app_index + 1 : -1]
            if not (part.startswith("(") and part.endswith(")"))
            and not part.startswith("@")
        ]
        uri = "/" + "/".join(route_parts) if route_parts else "/"

        pattern = re.compile(
            r"\bexport\s+(?:async\s+)?function\s+"
            r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\("
        )

        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []

        for match in pattern.finditer(source):
            method = match.group(1)
            if method not in HTTP_METHODS:
                continue

            key = f"{method} {uri}"
            route_id = stable_node_id(project_id, CodeNodeKind.ROUTE, key)
            target_key = f"next:{rel}:{method}"
            target_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, target_key)
            line = source.count("\n", 0, match.start()) + 1

            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=route_id,
                    kind=CodeNodeKind.ROUTE,
                    name=key,
                    path=rel,
                    qualified_name=key,
                    language=language,
                    line_start=line,
                    line_end=line,
                    metadata={
                        "framework": "next",
                        "router": "app",
                        "http_method": method,
                        "uri": uri,
                    },
                )
            )
            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=target_id,
                    kind=CodeNodeKind.EXTERNAL,
                    name=method,
                    path=rel,
                    language=language,
                    metadata={
                        "framework": "next",
                        "role": "route_handler",
                        "handler_name": method,
                        "handler_path": rel,
                    },
                )
            )
            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=route_id,
                    target_id=target_id,
                    kind=CodeEdgeKind.ROUTES_TO,
                )
            )

        if Path(path).name in {"page.js", "page.jsx", "page.ts", "page.tsx"}:
            page_match = re.search(
                r"\bexport\s+default\s+(?:async\s+)?function\s+"
                r"([A-Za-z_$][\w$]*)\s*\(",
                source,
            )
            if page_match is not None:
                handler_name = page_match.group(1)
                key = f"GET {uri}"
                route_id = stable_node_id(project_id, CodeNodeKind.ROUTE, key)
                target_key = f"next:{rel}:{handler_name}"
                target_id = stable_node_id(
                    project_id,
                    CodeNodeKind.EXTERNAL,
                    target_key,
                )
                line = source.count("\n", 0, page_match.start()) + 1

                nodes.append(
                    CodeNode(
                        project_id=project_id,
                        node_id=route_id,
                        kind=CodeNodeKind.ROUTE,
                        name=key,
                        path=rel,
                        qualified_name=key,
                        language=language,
                        line_start=line,
                        line_end=line,
                        metadata={
                            "framework": "next",
                            "router": "app",
                            "route_type": "page",
                            "http_method": "GET",
                            "uri": uri,
                        },
                    )
                )
                nodes.append(
                    CodeNode(
                        project_id=project_id,
                        node_id=target_id,
                        kind=CodeNodeKind.EXTERNAL,
                        name=handler_name,
                        path=rel,
                        language=language,
                        metadata={
                            "framework": "next",
                            "role": "route_handler",
                            "handler_name": handler_name,
                            "handler_path": rel,
                        },
                    )
                )
                edges.append(
                    CodeEdge(
                        project_id=project_id,
                        source_id=route_id,
                        target_id=target_id,
                        kind=CodeEdgeKind.ROUTES_TO,
                    )
                )

        unique_nodes = {node.node_id: node for node in nodes}
        unique_edges = {
            (edge.source_id, edge.target_id, edge.kind): edge for edge in edges
        }
        return ScanResult(
            nodes=tuple(unique_nodes.values()),
            edges=tuple(unique_edges.values()),
        )


def resolve_next_route_targets(
    nodes: list[CodeNode],
    edges: list[CodeEdge],
    seed_symbols: dict[str, str] | None = None,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    handlers: dict[tuple[str, str], set[str]] = {}

    for node in nodes:
        if (
            node.kind in {CodeNodeKind.FUNCTION, CodeNodeKind.COMPONENT}
            and node.path
            and node.name
        ):
            handlers.setdefault((node.path, node.name), set()).add(node.node_id)

    for qualified, node_id in (seed_symbols or {}).items():
        if "::" not in qualified:
            continue
        path, symbol = qualified.split("::", 1)
        if "::" in symbol:
            continue
        name = symbol.split("(", 1)[0]
        if path and name:
            handlers.setdefault((path, name), set()).add(node_id)

    by_id = {node.node_id: node for node in nodes}
    resolved_external: set[str] = set()
    resolved_edges: list[CodeEdge] = []

    for edge in edges:
        if edge.kind is not CodeEdgeKind.ROUTES_TO:
            resolved_edges.append(edge)
            continue

        target = by_id.get(edge.target_id)
        if (
            target is None
            or target.kind is not CodeNodeKind.EXTERNAL
            or target.metadata.get("framework") != "next"
            or target.metadata.get("role") != "route_handler"
        ):
            resolved_edges.append(edge)
            continue

        path = str(target.metadata.get("handler_path") or "")
        name = str(target.metadata.get("handler_name") or "")
        candidates = handlers.get((path, name), set())

        if len(candidates) != 1:
            resolved_edges.append(edge)
            continue

        resolved_external.add(target.node_id)
        resolved_edges.append(
            CodeEdge(
                project_id=edge.project_id,
                source_id=edge.source_id,
                target_id=next(iter(candidates)),
                kind=edge.kind,
                metadata={**dict(edge.metadata), "resolved": True},
            )
        )

    referenced = {
        endpoint
        for edge in resolved_edges
        for endpoint in (edge.source_id, edge.target_id)
    }
    resolved_nodes = [
        node
        for node in nodes
        if node.node_id not in resolved_external or node.node_id in referenced
    ]
    layout_names = {"layout.js", "layout.jsx", "layout.ts", "layout.tsx"}
    layouts = [
        node
        for node in resolved_nodes
        if node.kind is CodeNodeKind.COMPONENT
        and node.path
        and Path(node.path).name in layout_names
    ]
    existing = {
        (edge.source_id, edge.target_id, edge.kind)
        for edge in resolved_edges
    }

    for route in resolved_nodes:
        if (
            route.kind is not CodeNodeKind.ROUTE
            or route.metadata.get("framework") != "next"
            or route.metadata.get("route_type") != "page"
            or not route.path
        ):
            continue

        page_dir = Path(route.path).parent
        applicable = []

        for layout in layouts:
            layout_dir = Path(layout.path).parent
            if layout_dir == page_dir or layout_dir in page_dir.parents:
                applicable.append((len(layout_dir.parts), layout))

        for _, layout in sorted(applicable):
            key = (route.node_id, layout.node_id, CodeEdgeKind.USES)
            if key in existing:
                continue
            resolved_edges.append(
                CodeEdge(
                    project_id=route.project_id,
                    source_id=route.node_id,
                    target_id=layout.node_id,
                    kind=CodeEdgeKind.USES,
                    metadata={
                        "framework": "next",
                        "role": "layout",
                        "resolved": True,
                        "layout_path": layout.path,
                    },
                )
            )
            existing.add(key)

    return resolved_nodes, resolved_edges
