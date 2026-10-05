from __future__ import annotations

import hashlib
import json
from pathlib import Path

import tree_sitter_language_pack as tslp

from .code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from .language_scanner import ScanResult

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "any", "match"}


def stable_node_id(project_id: str, kind: CodeNodeKind, key: str) -> str:
    raw = f"{project_id}:{kind.value}:{key}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]



class LaravelFrameworkScanner:
    name = "laravel"

    def _laravel_root(self, root: Path, path: Path) -> Path | None:
        root = Path(root).resolve()
        current = Path(path).resolve().parent
        while current == root or root in current.parents:
            manifest = current / "composer.json"
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    deps = dict(data.get("require", {}))
                    if "laravel/framework" in deps:
                        return current
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    pass
            if current == root:
                break
            current = current.parent
        return None

    def supports(self, root: Path, path: Path, language: str | None) -> bool:
        return language == "php" and self._laravel_root(root, path) is not None

    def _is_route_file(self, laravel_root: Path, path: Path) -> bool:
        try:
            rel = Path(path).resolve().relative_to(laravel_root.resolve()).as_posix()
        except ValueError:
            return False
        return rel in {"routes/web.php", "routes/console.php", "routes/channels.php"} or rel.startswith("routes/api/")

    def scan_file(self, project_id: str, root: Path, path: Path, source: str, language: str | None) -> ScanResult:
        if not self.supports(root, path, language):
            return ScanResult(nodes=(), edges=())
        laravel_root = self._laravel_root(root, path)
        if laravel_root is None or not self._is_route_file(laravel_root, path):
            return ScanResult(nodes=(), edges=())
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        for method, uri, target, middleware, line in extract_laravel_routes(laravel_root, path, source):
            key = f"{method} {uri}"
            route_id = stable_node_id(project_id, CodeNodeKind.ROUTE, key)
            nodes.append(CodeNode(project_id=project_id, node_id=route_id, kind=CodeNodeKind.ROUTE, name=key, path=rel, qualified_name=key, language="php", line_start=line, line_end=line, metadata={"framework":"laravel","http_method":method,"uri":uri,"middleware":middleware}))
            if target:
                target_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, f"laravel:{target}")
                nodes.append(CodeNode(project_id=project_id, node_id=target_id, kind=CodeNodeKind.EXTERNAL, name=target, language="php", metadata={"framework":"laravel","role":"route_target"}))
                edges.append(CodeEdge(project_id=project_id, source_id=route_id, target_id=target_id, kind=CodeEdgeKind.ROUTES_TO))
        unique_nodes = {n.node_id:n for n in nodes}
        unique_edges = {(e.source_id,e.target_id,e.kind):e for e in edges}
        return ScanResult(nodes=tuple(unique_nodes.values()), edges=tuple(unique_edges.values()))


def walk_ast(node):
    yield node
    for child in node.named_children:
        yield from walk_ast(child)


def ast_text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def safe_line(source: bytes, node) -> int:
    return source.count(b"\n", 0, node.start_byte) + 1


def string_value(source: bytes, node) -> str | None:
    text = ast_text(source, node).strip()
    if len(text) >= 2 and text[0] in {chr(34), chr(39)} and text[-1] == text[0]:
        return text[1:-1]
    return None


def call_name(source: bytes, node) -> str | None:
    name = node.child_by_field_name("name")
    return ast_text(source, name).strip() if name is not None else None


def call_arguments(node):
    args = node.child_by_field_name("arguments")
    if args is None:
        return ()
    return tuple(c for c in args.named_children if c.type == "argument")


def argument_value_node(argument):
    return argument.named_children[0] if argument.named_children else None


def extract_route_calls(source: str):
    raw = source.encode("utf-8")
    tree = tslp.get_parser("php").parse(raw)
    routes = []
    for node in walk_ast(tree.root_node):
        if node.type != "scoped_call_expression":
            continue
        scope = node.child_by_field_name("scope")
        method = call_name(raw, node)
        if scope is None or ast_text(raw, scope).strip() != "Route" or method not in HTTP_METHODS:
            continue
        args = call_arguments(node)
        if not args:
            continue
        first = argument_value_node(args[0])
        uri = string_value(raw, first) if first is not None else None
        target = None
        if len(args) > 1:
            second = argument_value_node(args[1])
            if second is not None:
                target = ast_text(raw, second).strip()
        routes.append((method.upper(), uri, target, safe_line(raw, node)))
    return tuple(routes)


def chain_context(source: bytes, node) -> tuple[list[str], list[str]]:
    prefixes: list[str] = []
    middleware: list[str] = []
    current = node
    while current is not None:
        if current.type in {"scoped_call_expression", "member_call_expression"}:
            name = call_name(source, current)
            args = call_arguments(current)
            if name == "prefix" and args:
                value = argument_value_node(args[0])
                text = string_value(source, value) if value is not None else None
                if text:
                    prefixes.append(text)
            elif name == "middleware":
                for arg in args:
                    value = argument_value_node(arg)
                    text = string_value(source, value) if value is not None else None
                    if text:
                        middleware.append(text)
        current = current.child_by_field_name("object")
    return prefixes, middleware


def route_context(source: bytes, route_node) -> tuple[tuple[str, ...], tuple[str, ...]]:
    prefixes: list[str] = []
    middleware: list[str] = []
    current = route_node.parent
    while current is not None:
        if current.type == "member_call_expression":
            name = call_name(source, current)
            if name == "middleware":
                _, items = chain_context(source, current)
                middleware.extend(items)
            elif name == "group":
                obj = current.child_by_field_name("object")
                if obj is not None:
                    ps, ms = chain_context(source, obj)
                    prefixes.extend(reversed(ps))
                    middleware.extend(reversed(ms))
        current = current.parent
    return tuple(dict.fromkeys(prefixes)), tuple(dict.fromkeys(middleware))


def bootstrap_route_contexts(laravel_root: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    path = Path(laravel_root) / "bootstrap" / "app.php"
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    parser = tslp.get_parser("php")
    tree = parser.parse(raw)
    root = tree.root_node
    variables: dict[str, str] = {}
    contexts: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    for node in walk_ast(root):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None and right is not None:
                key = ast_text(raw, left).strip()
                value = string_value(raw, right)
                if key.startswith("$") and value is not None:
                    variables[key] = value

    for node in walk_ast(root):
        if node.type != "member_call_expression" or call_name(raw, node) != "group":
            continue
        args = call_arguments(node)
        if not args:
            continue
        target_node = argument_value_node(args[0])
        if target_node is None:
            continue
        target = ast_text(raw, target_node)
        if "routes/api/" not in target:
            continue
        prefixes: list[str] = []
        middleware: list[str] = []
        current = node.child_by_field_name("object")
        while current is not None:
            name = call_name(raw, current)
            cargs = call_arguments(current)
            if cargs:
                value_node = argument_value_node(cargs[0])
                text = ast_text(raw, value_node).strip() if value_node is not None else ""
                value = string_value(raw, value_node) if value_node is not None else None
                if value is None:
                    value = variables.get(text)
                if name == "prefix" and value:
                    prefixes.append(value)
                elif name == "middleware" and value:
                    middleware.append(value)
            current = current.child_by_field_name("object")
        contexts["routes/api/*.php"] = (tuple(reversed(prefixes)), tuple(reversed(middleware)))
    return contexts


def extract_laravel_routes(laravel_root: Path, path: Path, source: str):
    raw = source.encode("utf-8")
    parser = tslp.get_parser("php")
    tree = parser.parse(raw)
    root = tree.root_node
    rel = Path(path).resolve().relative_to(Path(laravel_root).resolve()).as_posix()
    outer_prefixes: tuple[str, ...] = ()
    outer_middleware: tuple[str, ...] = ()
    if rel.startswith("routes/api/"):
        outer_prefixes, outer_middleware = bootstrap_route_contexts(laravel_root).get("routes/api/*.php", ((), ()))
    routes = []
    for node in walk_ast(root):
        if node.type != "scoped_call_expression":
            continue
        scope = node.child_by_field_name("scope")
        method = call_name(raw, node)
        if scope is None or ast_text(raw, scope).strip() != "Route" or method not in HTTP_METHODS:
            continue
        args = call_arguments(node)
        if not args:
            continue
        first = argument_value_node(args[0])
        uri = string_value(raw, first) if first is not None else None
        if uri is None:
            continue
        target = None
        if len(args) > 1:
            second = argument_value_node(args[1])
            if second is not None:
                target = ast_text(raw, second).strip()
        local_prefixes, local_middleware = route_context(raw, node)
        parts = [x.strip("/") for x in (*outer_prefixes, *local_prefixes, uri) if x and x.strip("/")]
        full_uri = "/" + "/".join(parts)
        middleware = tuple(dict.fromkeys((*outer_middleware, *local_middleware)))
        routes.append((method.upper(), full_uri, target, middleware, safe_line(raw, node)))
    return tuple(routes)
