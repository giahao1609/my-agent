from __future__ import annotations

from pathlib import Path

from .language_scanner import ScannerRegistry, ScanResult
from .manifest_scanner import ManifestScanner
from .python_language_scanner import PythonLanguageScanner
from .tree_sitter_language_scanner import TreeSitterLanguageScanner


def build_default_scanner_registry() -> ScannerRegistry:
    registry = ScannerRegistry()
    registry.register(PythonLanguageScanner())
    registry.register(TreeSitterLanguageScanner())
    registry.register(ManifestScanner())
    return registry


def scan_polyglot_project(
    project_id: str,
    root: Path,
    *,
    include_paths: set[str] | None = None,
    seed_symbols: dict[str, str] | None = None,
    overlay_files: dict[str, str] | None = None,
    registry: ScannerRegistry | None = None,
) -> ScanResult:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("project root must be an existing directory")

    registry = registry or build_default_scanner_registry()
    selected = set(include_paths or ())
    nodes = []
    edges = []

    for scanner in registry.scanners:
        scanner_paths = None
        if selected:
            scanner_paths = {path for path in selected if scanner.supports(Path(path))}
            if not scanner_paths:
                continue
        result = scanner.scan_project(
            project_id,
            root,
            include_paths=scanner_paths,
            seed_symbols=seed_symbols,
            overlay_files=overlay_files,
        )
        nodes.extend(result.nodes)
        edges.extend(result.edges)

    unique_nodes = {node.node_id: node for node in nodes}
    unique_edges = {(edge.source_id, edge.target_id, edge.kind): edge for edge in edges}
    return ScanResult(nodes=tuple(unique_nodes.values()), edges=tuple(unique_edges.values()))
