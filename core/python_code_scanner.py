from __future__ import annotations

import ast
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from core.code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind


@dataclass(frozen=True, slots=True)
class PythonScanResult:
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]

def _node_id(project_id: str, kind: CodeNodeKind, key: str) -> str:
    raw = f"{project_id}:{kind.value}:{key}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]

def _module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

def _iter_python_files(root: Path):
    ignored = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".idea", ".gradle", ".next"}
    for current, dirs, files in os.walk(root, topdown=True, onerror=lambda _e: None, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ignored]
        base = Path(current)
        for name in files:
            if name.lower().endswith(".py"):
                yield base / name

def _ast_hash(node: ast.AST) -> str:
    raw = ast.dump(node, include_attributes=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def scan_python_project(project_id: str, root: Path, *, include_paths: set[str] | None = None, seed_symbols: dict[str, str] | None = None, overlay_files: dict[str, str] | None = None) -> PythonScanResult:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("project root must be an existing directory")
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []
    symbol_ids: dict[str, str] = dict(seed_symbols or {})
    pending_calls: list[tuple[str, str, str]] = []
    pending_inherits: list[tuple[str, str, str]] = []
    import_aliases: dict[tuple[str, str], str] = {}

    project_node = CodeNode(project_id, _node_id(project_id, CodeNodeKind.PROJECT, project_id), CodeNodeKind.PROJECT, project_id, path=str(root))
    nodes.append(project_node)
    all_python_files = tuple(_iter_python_files(root))
    module_names = {_module_name(root, p) for p in all_python_files}
    python_files = tuple(p for p in all_python_files if include_paths is None or p.relative_to(root).as_posix() in include_paths)
    seen_external = set()
    for path in python_files:
        rel = path.relative_to(root).as_posix()
        module = _module_name(root, path)
        file_node = CodeNode(project_id, _node_id(project_id, CodeNodeKind.FILE, rel), CodeNodeKind.FILE, path.name, path=rel, language="python")
        module_node = CodeNode(project_id, _node_id(project_id, CodeNodeKind.MODULE, module or rel), CodeNodeKind.MODULE, module or path.stem, path=rel, qualified_name=module, language="python")
        nodes.extend((file_node, module_node))
        edges.append(CodeEdge(project_id, project_node.node_id, file_node.node_id, CodeEdgeKind.CONTAINS))
        edges.append(CodeEdge(project_id, file_node.node_id, module_node.node_id, CodeEdgeKind.DEFINES))
        try:
            source = (overlay_files or {}).get(rel)
            if source is None:
                source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (UnicodeDecodeError, SyntaxError):
            continue
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = f"{module}.{item.name}" if module else item.name
                kind = CodeNodeKind.TEST if (rel.startswith("tests/") or Path(rel).name.startswith("test_")) and item.name.startswith("test_") else CodeNodeKind.FUNCTION
                node = CodeNode(project_id, _node_id(project_id, kind, qn), kind, item.name, path=rel, qualified_name=qn, language="python", line_start=item.lineno, line_end=getattr(item, "end_lineno", item.lineno), metadata={"ast_hash": _ast_hash(item)})
                nodes.append(node)
                symbol_ids[qn] = node.node_id
                for call in _direct_calls(item): pending_calls.append((node.node_id, module, call))
                edges.append(CodeEdge(project_id, module_node.node_id, node.node_id, CodeEdgeKind.DEFINES))
            elif isinstance(item, ast.ClassDef):
                qn = f"{module}.{item.name}" if module else item.name
                node = CodeNode(project_id, _node_id(project_id, CodeNodeKind.CLASS, qn), CodeNodeKind.CLASS, item.name, path=rel, qualified_name=qn, language="python", line_start=item.lineno, line_end=getattr(item, "end_lineno", item.lineno), metadata={"ast_hash": _ast_hash(item)})
                nodes.append(node)
                symbol_ids[qn] = node.node_id
                edges.append(CodeEdge(project_id, module_node.node_id, node.node_id, CodeEdgeKind.DEFINES))
                for base in item.bases:
                    base_name = _call_name(base)
                    if base_name: pending_inherits.append((node.node_id, module, base_name))
                for child in item.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mqn = f"{qn}.{child.name}"
                        method_kind = CodeNodeKind.TEST if (rel.startswith("tests/") or Path(rel).name.startswith("test_")) and child.name.startswith("test_") else CodeNodeKind.METHOD
                        method = CodeNode(project_id, _node_id(project_id, method_kind, mqn), method_kind, child.name, path=rel, qualified_name=mqn, language="python", line_start=child.lineno, line_end=getattr(child, "end_lineno", child.lineno), metadata={"ast_hash": _ast_hash(child)})
                        nodes.append(method)
                        symbol_ids[mqn] = method.node_id
                        for call in _direct_calls(child): pending_calls.append((method.node_id, module, call))
                        edges.append(CodeEdge(project_id, node.node_id, method.node_id, CodeEdgeKind.DEFINES))
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    target = alias.name
                    local = alias.asname or alias.name.split(".", 1)[0]
                    import_aliases[(module, local)] = target
                    internal = target if target in module_names else next((m for m in module_names if m.startswith(target + ".")), None)
                    if internal:
                        target_id = _node_id(project_id, CodeNodeKind.MODULE, internal)
                    else:
                        target_id = _node_id(project_id, CodeNodeKind.EXTERNAL, target)
                        if target_id not in seen_external:
                            seen_external.add(target_id)
                            nodes.append(CodeNode(project_id, target_id, CodeNodeKind.EXTERNAL, target, qualified_name=target, language="python"))
                    edges.append(CodeEdge(project_id, module_node.node_id, target_id, CodeEdgeKind.IMPORTS))
        for stmt in tree.body:
            if isinstance(stmt, ast.ImportFrom):
                if stmt.level:
                    parts = module.split(".") if module else []
                    if path.name != "__init__.py": parts = parts[:-1]
                    if stmt.level > 1: parts = parts[:-(stmt.level - 1)]
                    target = ".".join(parts + (stmt.module.split(".") if stmt.module else []))
                else:
                    target = stmt.module or ""
                for alias in stmt.names:
                    local = alias.asname or alias.name
                    imported = f"{target}.{alias.name}" if target else alias.name
                    import_aliases[(module, local)] = imported
                internal = target if target in module_names else None
                if internal:
                    target_id = _node_id(project_id, CodeNodeKind.MODULE, internal)
                else:
                    target_id = _node_id(project_id, CodeNodeKind.EXTERNAL, target)
                    if target and target_id not in seen_external:
                        seen_external.add(target_id)
                        nodes.append(CodeNode(project_id, target_id, CodeNodeKind.EXTERNAL, target, qualified_name=target, language="python"))
                if target:
                    edges.append(CodeEdge(project_id, module_node.node_id, target_id, CodeEdgeKind.IMPORTS))

    reverse_symbols = {v: k for k, v in symbol_ids.items()}
    for source_id, module, call in pending_calls:
        target_id = None
        if call.startswith("self."):
            source_qn = reverse_symbols.get(source_id, "")
            owner = source_qn.rsplit(".", 1)[0]
            target_id = symbol_ids.get(f"{owner}.{call[5:]}")
        if target_id is None:
            imported = import_aliases.get((module, call))
            if imported:
                target_id = symbol_ids.get(imported)
        if target_id is None and "." in call:
            local, _, member = call.partition(".")
            imported = import_aliases.get((module, local))
            if imported:
                target_id = symbol_ids.get(f"{imported}.{member}")
        if target_id is None:
            target_id = symbol_ids.get(f"{module}.{call}" if module else call)
        if target_id and target_id != source_id:
            edges.append(CodeEdge(project_id, source_id, target_id, CodeEdgeKind.CALLS))

    for source_id, module, base in pending_inherits:
        imported = import_aliases.get((module, base))
        target_id = symbol_ids.get(imported) if imported else None
        if target_id is None:
            target_id = symbol_ids.get(f"{module}.{base}" if module else base)
        if target_id is None and "." not in base:
            matches = [nid for qn, nid in symbol_ids.items() if qn.endswith("." + base)]
            if len(matches) == 1: target_id = matches[0]
        if target_id and target_id != source_id:
            edges.append(CodeEdge(project_id, source_id, target_id, CodeEdgeKind.INHERITS))

    node_map = {n.node_id: n for n in nodes}
    for edge in tuple(edges):
        source = node_map.get(edge.source_id)
        if edge.kind == CodeEdgeKind.CALLS and source and source.kind == CodeNodeKind.TEST:
            edges.append(CodeEdge(project_id, edge.source_id, edge.target_id, CodeEdgeKind.TESTS))
    edge_map = {(e.source_id, e.target_id, e.kind): e for e in edges}
    return PythonScanResult(tuple(node_map.values()), tuple(edge_map.values()))


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None

class _CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.names = []
    def visit_Call(self, node):
        name = _call_name(node.func)
        if name:
            self.names.append(name)
        self.generic_visit(node)
    def visit_FunctionDef(self, node):
        return
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_ClassDef(self, node):
        return


def _direct_calls(fn):
    visitor = _CallVisitor()
    for stmt in fn.body:
        visitor.visit(stmt)
    return tuple(visitor.names)

