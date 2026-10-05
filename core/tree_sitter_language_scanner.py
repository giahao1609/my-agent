from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import replace
from pathlib import Path

import tree_sitter_language_pack as tslp

from .code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from .framework_scanner import FrameworkScannerRegistry
from .go_http_route_table_scanner import GoHttpRouteTableScanner, resolve_http_route_targets
from .js_ts_language_semantics import extract_js_family_ast_graph, resolve_js_targets
from .language_scanner import ScanResult
from .laravel_framework_scanner import LaravelFrameworkScanner
from .next_framework_scanner import NextFrameworkScanner, resolve_next_route_targets

logger = logging.getLogger(__name__)

EXTENSION_LANGUAGES = {
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".php": "php",
    ".vue": "vue",
    ".css": "css",
    ".sql": "sql",
    ".tf": "terraform",
    ".rego": "rego",
    ".sh": "bash",
}

NODE_KIND_MAP = {
    "class_declaration": CodeNodeKind.CLASS,
    "interface_declaration": CodeNodeKind.INTERFACE,
    "method_declaration": CodeNodeKind.METHOD,
    "function_declaration": CodeNodeKind.FUNCTION,
    "function_definition": CodeNodeKind.FUNCTION,
    "struct_type": CodeNodeKind.STRUCT,
    "enum_declaration": CodeNodeKind.ENUM,
    "trait_declaration": CodeNodeKind.TRAIT,
}


class TreeSitterLanguageScanner:
    name = "tree-sitter"

    def __init__(self, framework_registry: FrameworkScannerRegistry | None = None) -> None:
        if framework_registry is None:
            framework_registry = FrameworkScannerRegistry()
            framework_registry.register(LaravelFrameworkScanner())
            framework_registry.register(GoHttpRouteTableScanner())
            framework_registry.register(NextFrameworkScanner())
        self.framework_registry = framework_registry

    def language_for(self, path: Path) -> str | None:
        return EXTENSION_LANGUAGES.get(Path(path).suffix.lower())

    def supports(self, path: Path) -> bool:
        language = self.language_for(path)
        return language is not None and tslp.has_language(language)

    def scan_project(
        self,
        project_id: str,
        root: Path,
        *,
        include_paths: set[str] | None = None,
        seed_symbols: dict[str, str] | None = None,
        overlay_files: dict[str, str] | None = None,
    ) -> ScanResult:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("project root must be an existing directory")

        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        project_node_id = stable_node_id(project_id, CodeNodeKind.PROJECT, project_id)
        nodes.append(CodeNode(project_id=project_id, node_id=project_node_id, kind=CodeNodeKind.PROJECT, name=project_id, path="."))

        wanted = set(include_paths or ())
        overlays = dict(overlay_files or {})

        for path in iter_supported_files(root):
            rel = path.relative_to(root).as_posix()
            if wanted and rel not in wanted:
                continue
            language = self.language_for(path)
            if language is None:
                continue
            source = overlays.get(rel)
            if source is None:
                try:
                    source = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

            file_id = stable_node_id(project_id, CodeNodeKind.FILE, rel)
            nodes.append(CodeNode(project_id=project_id, node_id=file_id, kind=CodeNodeKind.FILE, name=path.name, path=rel, language=language))
            edges.append(CodeEdge(project_id=project_id, source_id=project_node_id, target_id=file_id, kind=CodeEdgeKind.CONTAINS))

            result = None
            if language not in {"php", "javascript", "typescript", "tsx", "vue", "terraform"}:
                try:
                    result = tslp.process(source, tslp.ProcessConfig(language=language, structure=True, imports=True, symbols=True))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("tree-sitter processing failed for %s (%s): %s", rel, language, exc)
                    continue

            if language == "php":
                php_nodes, php_edges = extract_php_ast_graph(project_id, rel, file_id, source)
                nodes.extend(php_nodes)
                edges.extend(php_edges)
            elif language == "go":
                go_nodes, go_edges = extract_go_ast_graph(project_id, rel, file_id, source)
                nodes.extend(go_nodes)
                edges.extend(go_edges)
            elif language == "java":
                java_nodes, java_edges = extract_java_ast_graph(project_id, rel, file_id, source)
                nodes.extend(java_nodes)
                edges.extend(java_edges)
            elif language == "terraform":
                tf_nodes, tf_edges = extract_terraform_graph(
                    project_id,
                    rel,
                    file_id,
                    source,
                )
                nodes.extend(tf_nodes)
                edges.extend(tf_edges)
            elif language in {"javascript", "typescript", "tsx", "vue"}:
                js_nodes, js_edges = extract_js_family_ast_graph(project_id, rel, file_id, source, language, stable_node_id)
                nodes.extend(js_nodes)
                edges.extend(js_edges)

            for framework in self.framework_registry.scanners_for(root, path, language):
                fx = framework.scan_file(project_id, root, path, source, language)
                nodes.extend(fx.nodes)
                edges.extend(fx.edges)
                for fx_node in fx.nodes:
                    if fx_node.path == rel:
                        edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=fx_node.node_id, kind=CodeEdgeKind.DEFINES))

            seen_symbols: set[tuple[str, str, int | None]] = set()
            symbol_items = () if result is None or language in {"go", "java", "terraform", "javascript", "typescript", "tsx", "vue"} else list(result.structure) + list(result.symbols)
            for item in symbol_items:
                kind = process_kind(item.kind)
                name = getattr(item, "name", None)
                if kind is None or not name:
                    continue
                line_start, line_end = span_lines(getattr(item, "span", None))
                key = (kind.value, str(name), line_start)
                if key in seen_symbols:
                    continue
                seen_symbols.add(key)
                qualified = f"{rel}::{name}"
                node_id = stable_node_id(project_id, kind, qualified)
                nodes.append(CodeNode(project_id=project_id, node_id=node_id, kind=kind, name=str(name), path=rel, qualified_name=qualified, language=language, line_start=line_start, line_end=line_end, metadata={"parser": "tree-sitter"}))
                edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=node_id, kind=CodeEdgeKind.DEFINES))

            seen_imports: set[str] = set()
            for item in (() if result is None else result.imports):
                source_name = str(getattr(item, "source", "") or "").strip()
                if not source_name or source_name in seen_imports:
                    continue
                seen_imports.add(source_name)
                ext_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, f"{language}:{source_name}")
                nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=source_name, language=language, metadata={"parser": "tree-sitter"}))
                edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=ext_id, kind=CodeEdgeKind.IMPORTS))

        nodes, edges = resolve_php_targets(nodes, edges, seed_symbols)
        nodes, edges = resolve_laravel_route_targets(nodes, edges, seed_symbols)
        nodes, edges = resolve_go_call_targets(nodes, edges, root, seed_symbols)
        nodes, edges = resolve_go_implements(nodes, edges)
        nodes, edges = resolve_java_targets(nodes, edges, seed_symbols)
        nodes, edges = resolve_js_targets(project_id, nodes, edges, seed_symbols, stable_node_id)
        nodes, edges = resolve_next_route_targets(nodes, edges, seed_symbols)
        nodes, edges = resolve_http_route_targets(nodes, edges)
        unique_nodes = {node.node_id: node for node in nodes}
        unique_edges = {(edge.source_id, edge.target_id, edge.kind): edge for edge in edges}
        return ScanResult(nodes=tuple(unique_nodes.values()), edges=tuple(unique_edges.values()))


IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".idea", ".gradle", ".next"}


def iter_supported_files(root: Path):
    root = Path(root).resolve()
    for current, dirs, files in os.walk(root, topdown=True, onerror=lambda e: None, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        base = Path(current)
        for name in files:
            path = base / name
            if path.suffix.lower() in EXTENSION_LANGUAGES:
                yield path


def stable_node_id(project_id: str, kind: CodeNodeKind, key: str) -> str:
    raw = f"{project_id}:{kind.value}:{key}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def span_lines(span) -> tuple[int | None, int | None]:
    if span is None:
        return None, None
    return int(span.start_line) + 1, int(span.end_line) + 1


PROCESS_KIND_MAP = {
    "Class": CodeNodeKind.CLASS,
    "Interface": CodeNodeKind.INTERFACE,
    "Function": CodeNodeKind.FUNCTION,
    "Method": CodeNodeKind.METHOD,
    "Enum": CodeNodeKind.ENUM,
    "Trait": CodeNodeKind.TRAIT,
    "Variable": CodeNodeKind.VARIABLE,
    "Constant": CodeNodeKind.VARIABLE,
    "Type": CodeNodeKind.STRUCT,
}


def process_kind(kind: object) -> CodeNodeKind | None:
    return PROCESS_KIND_MAP.get(str(kind))


def walk_ast(node):
    yield node
    for child in node.named_children:
        yield from walk_ast(child)


def ast_text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def ast_name(source: bytes, node) -> str | None:
    child = node.child_by_field_name("name")
    if child is None:
        return None
    value = ast_text(source, child).strip()
    return value or None


def ast_lines(source: bytes, node) -> tuple[int, int]:
    start = source.count(b"\n", 0, node.start_byte) + 1
    end = source.count(b"\n", 0, node.end_byte) + 1
    return start, end



def go_receiver_type(source: bytes, receiver) -> str | None:
    type_node = None
    for child in receiver.named_children:
        candidate = child.child_by_field_name("type")
        if candidate is not None:
            type_node = candidate
            break
    if type_node is None:
        type_node = receiver
    for node in walk_ast(type_node):
        if node.type == "type_identifier":
            value = ast_text(source, node).strip()
            if value:
                return value
    return None


def go_receiver_form(receiver) -> str:
    return "pointer" if any(node.type == "pointer_type" for node in walk_ast(receiver)) else "value"


def go_canonical_type(source: bytes, node, imports: dict[str, str]) -> str:
    value = ast_text(source, node).strip()
    for alias, import_path in sorted(imports.items(), key=lambda item: len(item[0]), reverse=True):
        value = re.sub(rf"\b{re.escape(alias)}\.", import_path + ".", value)
    return re.sub(r"\s+", "", value)


def go_parameter_types(source: bytes, parameter_list, imports: dict[str, str]) -> tuple[str, ...]:
    result: list[str] = []
    if parameter_list is None:
        return ()
    for parameter in parameter_list.named_children:
        if parameter.type not in {"parameter_declaration", "variadic_parameter_declaration"}:
            continue
        type_node = parameter.child_by_field_name("type")
        if type_node is None:
            continue
        value = go_canonical_type(source, type_node, imports)
        if parameter.type == "variadic_parameter_declaration":
            value = "..." + value
        name_count = sum(
            1
            for index, _child in enumerate(parameter.children)
            if parameter.field_name_for_child(index) == "name"
        )
        result.extend([value] * max(1, name_count))
    return tuple(result)


def go_callable_signature(source: bytes, declaration, imports: dict[str, str]) -> str:
    parameters = go_parameter_types(source, declaration.child_by_field_name("parameters"), imports)
    result_node = declaration.child_by_field_name("result")
    if result_node is None:
        results: tuple[str, ...] = ()
    elif result_node.type == "parameter_list":
        results = go_parameter_types(source, result_node, imports)
    else:
        results = (go_canonical_type(source, result_node, imports),)
    return f"({','.join(parameters)})->({','.join(results)})"



def _terraform_block_end(lines: list[str], start: int) -> int:
    depth = 0
    seen_open = False

    for index in range(start, len(lines)):
        line = re.sub(r'"(?:\\.|[^"\\])*"', '""', lines[index])
        opens = line.count("{")
        closes = line.count("}")

        if opens:
            seen_open = True
        depth += opens - closes

        if seen_open and depth <= 0:
            return index

    return len(lines) - 1


def extract_terraform_graph(
    project_id: str,
    rel: str,
    file_id: str,
    source: str,
):
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []
    lines = source.splitlines()

    resource_ids: dict[tuple[str, str], str] = {}
    resource_blocks: list[tuple[str, int, int, str]] = []

    index = 0
    while index < len(lines):
        line = lines[index]

        resource_match = re.match(
            r'\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{',
            line,
        )
        if resource_match:
            resource_type, resource_name = resource_match.groups()
            full_name = f"{resource_type}.{resource_name}"
            end = _terraform_block_end(lines, index)
            node_id = stable_node_id(
                project_id,
                CodeNodeKind.RESOURCE,
                f"{rel}::terraform::resource::{full_name}",
            )

            resource_ids[(resource_type, resource_name)] = node_id
            resource_blocks.append((node_id, index, end, full_name))

            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=node_id,
                    kind=CodeNodeKind.RESOURCE,
                    name=full_name,
                    path=rel,
                    qualified_name=f"{rel}::{full_name}",
                    language="terraform",
                    line_start=index + 1,
                    line_end=end + 1,
                    metadata={
                        "parser": "terraform_semantics",
                        "terraform_kind": "resource",
                        "resource_type": resource_type,
                        "resource_name": resource_name,
                    },
                )
            )
            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=file_id,
                    target_id=node_id,
                    kind=CodeEdgeKind.DEFINES,
                )
            )
            index = end + 1
            continue

        module_match = re.match(
            r'\s*module\s+"([^"]+)"\s*\{',
            line,
        )
        if module_match:
            module_name = module_match.group(1)
            end = _terraform_block_end(lines, index)
            body = "\n".join(lines[index : end + 1])
            source_match = re.search(
                r'(?m)^\s*source\s*=\s*"([^"]+)"',
                body,
            )
            module_source = source_match.group(1) if source_match else None
            node_id = stable_node_id(
                project_id,
                CodeNodeKind.MODULE,
                f"{rel}::terraform::module::{module_name}",
            )

            metadata = {
                "parser": "terraform_semantics",
                "terraform_kind": "module",
            }
            if module_source is not None:
                metadata["terraform_source"] = module_source

            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=node_id,
                    kind=CodeNodeKind.MODULE,
                    name=module_name,
                    path=rel,
                    qualified_name=f"{rel}::module.{module_name}",
                    language="terraform",
                    line_start=index + 1,
                    line_end=end + 1,
                    metadata=metadata,
                )
            )
            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=file_id,
                    target_id=node_id,
                    kind=CodeEdgeKind.DEFINES,
                )
            )
            index = end + 1
            continue

        index += 1

    emitted_dependencies: set[tuple[str, str]] = set()

    for source_id, start, end, _ in resource_blocks:
        body = "\n".join(lines[start : end + 1])

        for match in re.finditer(
            r'\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b',
            body,
        ):
            target_id = resource_ids.get((match.group(1), match.group(2)))
            if target_id is None or target_id == source_id:
                continue

            key = (source_id, target_id)
            if key in emitted_dependencies:
                continue
            emitted_dependencies.add(key)

            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=source_id,
                    target_id=target_id,
                    kind=CodeEdgeKind.DEPENDS_ON,
                    metadata={
                        "resolution": "terraform_reference",
                        "resolved": True,
                    },
                )
            )

    return tuple(nodes), tuple(edges)

def extract_go_ast_graph(project_id: str, rel: str, file_id: str, source: str):
    raw = source.encode("utf-8")
    parser = tslp.get_parser("go")
    tree = parser.parse(raw)
    root = tree.root_node
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []

    package = ""
    for child in root.named_children:
        if child.type != "package_clause":
            continue
        for part in child.named_children:
            if part.type == "package_identifier":
                package = ast_text(raw, part).strip()
                break
        break

    package_dir = str(Path(rel).parent).replace("\\", "/")
    if package_dir == ".":
        package_dir = ""
    package_key = package_dir or package or "."

    imports: dict[str, str] = {}
    for node in walk_ast(root):
        if node.type != "import_spec":
            continue
        path_node = node.child_by_field_name("path")
        if path_node is None:
            continue
        import_path = ast_text(raw, path_node).strip().strip('"')
        if not import_path:
            continue
        name_node = node.child_by_field_name("name")
        alias = ast_text(raw, name_node).strip() if name_node is not None else import_path.rsplit("/", 1)[-1]
        if alias and alias not in {"_", "."}:
            imports[alias] = import_path

    type_nodes: dict[str, str] = {}

    def add_call_target(
        caller_id: str,
        name: str,
        *,
        target_qn: str | None = None,
        call_kind: str,
        import_path: str | None = None,
    ) -> None:
        key = target_qn or f"{package_key}::{name}"
        ext_key = f"go-call:{call_kind}:{key}"
        target_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, ext_key)
        metadata = {
            "parser": "tree-sitter",
            "go_call_kind": call_kind,
            "resolved": False,
        }
        if target_qn:
            metadata["go_target_qn"] = target_qn
        if import_path:
            metadata["import_path"] = import_path
        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=target_id,
                kind=CodeNodeKind.EXTERNAL,
                name=name,
                qualified_name=target_qn,
                language="go",
                metadata=metadata,
            )
        )
        edges.append(
            CodeEdge(
                project_id=project_id,
                source_id=caller_id,
                target_id=target_id,
                kind=CodeEdgeKind.CALLS,
                metadata={"resolved": False, "go_call_kind": call_kind},
            )
        )

    def emit_calls(decl, caller_id: str, receiver_var: str | None = None, owner_qn: str | None = None) -> None:
        body = decl.child_by_field_name("body")
        if body is None:
            return
        for call in walk_ast(body):
            if call.type != "call_expression":
                continue
            fn = call.child_by_field_name("function")
            if fn is None:
                continue

            if fn.type == "identifier":
                name = ast_text(raw, fn).strip()
                go_predeclared = {
                    "append", "cap", "clear", "close", "complex", "copy", "delete",
                    "imag", "len", "make", "max", "min", "new", "panic", "print",
                    "println", "real", "recover", "bool", "byte", "complex64",
                    "complex128", "error", "float32", "float64", "int", "int8",
                    "int16", "int32", "int64", "rune", "string", "uint", "uint8",
                    "uint16", "uint32", "uint64", "uintptr",
                }
                if name and name not in go_predeclared:
                    add_call_target(
                        caller_id,
                        name,
                        target_qn=f"{package_key}::{name}",
                        call_kind="package_function",
                    )
                continue

            if fn.type != "selector_expression":
                continue

            operand = fn.child_by_field_name("operand")
            field = fn.child_by_field_name("field")
            if operand is None or field is None:
                continue
            field_name = ast_text(raw, field).strip()
            if not field_name:
                continue

            if operand.type == "identifier":
                operand_name = ast_text(raw, operand).strip()
                if operand_name in imports:
                    import_path = imports[operand_name]
                    add_call_target(
                        caller_id,
                        f"{operand_name}.{field_name}",
                        call_kind="import_function",
                        import_path=import_path,
                    )
                elif receiver_var and owner_qn and operand_name == receiver_var:
                    add_call_target(
                        caller_id,
                        field_name,
                        target_qn=f"{owner_qn}::{field_name}",
                        call_kind="receiver_method",
                    )
                else:
                    add_call_target(
                        caller_id,
                        f"{operand_name}.{field_name}",
                        call_kind="unresolved_receiver",
                    )
                continue

            parent = call.parent
            nested_in_selector_chain = (
                parent is not None
                and parent.type == "selector_expression"
                and parent.child_by_field_name("operand") == call
                and parent.parent is not None
                and parent.parent.type == "call_expression"
                and parent.parent.child_by_field_name("function") == parent
            )
            if nested_in_selector_chain:
                continue

            expr = ast_text(raw, fn).strip()
            if expr:
                add_call_target(caller_id, expr, call_kind="chained_selector")

    for child in root.named_children:
        if child.type == "type_declaration":
            for spec in child.named_children:
                if spec.type != "type_spec":
                    continue
                name = ast_name(raw, spec)
                type_expr = spec.child_by_field_name("type")
                if not name or type_expr is None:
                    continue
                if type_expr.type == "interface_type":
                    kind = CodeNodeKind.INTERFACE
                    go_type = "interface"
                elif type_expr.type == "struct_type":
                    kind = CodeNodeKind.STRUCT
                    go_type = "struct"
                else:
                    kind = CodeNodeKind.STRUCT
                    go_type = "named_type"
                qn = f"{package_key}::{name}"
                start, end = ast_lines(raw, spec)
                node_id = stable_node_id(project_id, kind, qn)
                nodes.append(CodeNode(project_id=project_id, node_id=node_id, kind=kind, name=name, path=rel, qualified_name=qn, language="go", line_start=start, line_end=end, metadata={"parser": "tree-sitter", "package": package, "go_type": go_type}))
                edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=node_id, kind=CodeEdgeKind.DEFINES))
                type_nodes[name] = node_id

                if type_expr.type == "interface_type":
                    for member in type_expr.named_children:
                        if member.type != "method_elem":
                            continue
                        method = ast_name(raw, member)
                        if not method:
                            continue
                        mqn = f"{qn}::{method}"
                        ms, me = ast_lines(raw, member)
                        method_id = stable_node_id(project_id, CodeNodeKind.METHOD, mqn)
                        nodes.append(CodeNode(project_id=project_id, node_id=method_id, kind=CodeNodeKind.METHOD, name=method, path=rel, qualified_name=mqn, language="go", line_start=ms, line_end=me, metadata={"parser": "tree-sitter", "package": package, "owner": qn, "interface_method": True, "go_signature": go_callable_signature(raw, member, imports)}))
                        edges.append(CodeEdge(project_id=project_id, source_id=node_id, target_id=method_id, kind=CodeEdgeKind.DEFINES))
        elif child.type == "function_declaration":
            name = ast_name(raw, child)
            if not name:
                continue
            qn = f"{package_key}::{name}"
            start, end = ast_lines(raw, child)
            node_id = stable_node_id(project_id, CodeNodeKind.FUNCTION, qn)
            nodes.append(CodeNode(project_id=project_id, node_id=node_id, kind=CodeNodeKind.FUNCTION, name=name, path=rel, qualified_name=qn, language="go", line_start=start, line_end=end, metadata={"parser": "tree-sitter", "package": package, "go_signature": go_callable_signature(raw, child, imports)}))
            edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=node_id, kind=CodeEdgeKind.DEFINES))
            emit_calls(child, node_id)

        elif child.type == "method_declaration":
            name = ast_name(raw, child)
            receiver = child.child_by_field_name("receiver")
            owner = go_receiver_type(raw, receiver) if receiver is not None else None
            if not name or not owner:
                continue
            owner_qn = f"{package_key}::{owner}"
            qn = f"{owner_qn}::{name}"
            start, end = ast_lines(raw, child)
            node_id = stable_node_id(project_id, CodeNodeKind.METHOD, qn)
            nodes.append(CodeNode(project_id=project_id, node_id=node_id, kind=CodeNodeKind.METHOD, name=name, path=rel, qualified_name=qn, language="go", line_start=start, line_end=end, metadata={"parser": "tree-sitter", "package": package, "owner": owner_qn, "receiver": owner, "receiver_form": go_receiver_form(receiver), "go_signature": go_callable_signature(raw, child, imports)}))
            source_id = type_nodes.get(owner, file_id)
            edges.append(CodeEdge(project_id=project_id, source_id=source_id, target_id=node_id, kind=CodeEdgeKind.DEFINES))

            receiver_var = None
            if receiver is not None:
                for part in walk_ast(receiver):
                    if part.type == "identifier":
                        receiver_var = ast_text(raw, part).strip()
                        if receiver_var:
                            break
            emit_calls(child, node_id, receiver_var=receiver_var, owner_qn=owner_qn)

    return tuple(nodes), tuple(edges)


def resolve_go_implements(nodes: list[CodeNode], edges: list[CodeEdge]) -> tuple[list[CodeNode], list[CodeEdge]]:
    interfaces = {n.qualified_name: n for n in nodes if n.language == "go" and n.kind is CodeNodeKind.INTERFACE and n.qualified_name}
    concrete_types = {n.qualified_name: n for n in nodes if n.language == "go" and n.kind is CodeNodeKind.STRUCT and n.qualified_name and n.metadata.get("go_type") != "interface"}
    interface_methods: dict[str, dict[str, str]] = {}
    concrete_methods: dict[str, list[CodeNode]] = {}
    for n in nodes:
        if n.language != "go" or n.kind is not CodeNodeKind.METHOD:
            continue
        owner = str(n.metadata.get("owner") or "")
        sig = str(n.metadata.get("go_signature") or "")
        if not owner or not sig:
            continue
        if n.metadata.get("interface_method"):
            interface_methods.setdefault(owner, {})[n.name] = sig
        else:
            concrete_methods.setdefault(owner, []).append(n)
    for type_qn, type_node in concrete_types.items():
        package_key = type_qn.rsplit("::", 1)[0]
        methods = concrete_methods.get(type_qn, [])
        value_set = {m.name: str(m.metadata.get("go_signature")) for m in methods if m.metadata.get("receiver_form") == "value"}
        pointer_set = {m.name: str(m.metadata.get("go_signature")) for m in methods}
        for interface_qn, interface_node in interfaces.items():
            if interface_qn.rsplit("::", 1)[0] != package_key:
                continue
            required = interface_methods.get(interface_qn, {})
            if not required:
                continue
            form = None
            if all(value_set.get(name) == sig for name, sig in required.items()):
                form = "value"
            elif all(pointer_set.get(name) == sig for name, sig in required.items()):
                form = "pointer"
            if form:
                edges.append(CodeEdge(project_id=type_node.project_id, source_id=type_node.node_id, target_id=interface_node.node_id, kind=CodeEdgeKind.IMPLEMENTS, metadata={"resolved": True, "language": "go", "receiver_form": form, "method_count": len(required), "scope": "same_package"}))
    return nodes, edges


def resolve_go_call_targets(
    nodes: list[CodeNode],
    edges: list[CodeEdge],
    root: Path,
    seed_symbols: dict[str, str] | None = None,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    symbol_ids = {
        node.qualified_name: node.node_id
        for node in nodes
        if node.language == "go"
        and node.qualified_name
        and node.kind in {CodeNodeKind.FUNCTION, CodeNodeKind.METHOD}
    }
    for qualified_name, node_id in (seed_symbols or {}).items():
        symbol_ids.setdefault(qualified_name, node_id)

    root = Path(root).resolve()
    local_modules: list[tuple[str, str]] = []

    for go_mod in root.rglob("go.mod"):
        if any(part in IGNORED_DIRS for part in go_mod.relative_to(root).parts):
            continue
        try:
            text = go_mod.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        module_match = re.search(r"(?m)^module\s+(\S+)\s*$", text)
        if module_match is None:
            continue
        module_name = module_match.group(1)
        module_dir = go_mod.parent.relative_to(root).as_posix()
        local_modules.append((module_name, "" if module_dir == "." else module_dir))

    local_modules.sort(key=lambda item: len(item[0]), reverse=True)

    replacements: dict[str, str] = {}
    for node in nodes:
        if node.kind is not CodeNodeKind.EXTERNAL:
            continue

        target_qn = node.metadata.get("go_target_qn")

        if not target_qn and node.metadata.get("go_call_kind") == "import_function":
            import_path = str(node.metadata.get("import_path") or "")
            target_name = node.name.rsplit(".", 1)[-1]

            for module_name, module_dir in local_modules:
                if import_path != module_name and not import_path.startswith(module_name + "/"):
                    continue

                suffix = import_path[len(module_name):].lstrip("/")
                package_dir = "/".join(part for part in (module_dir, suffix) if part)
                if package_dir:
                    target_qn = f"{package_dir}::{target_name}"
                break

        if not target_qn:
            continue

        target_id = symbol_ids.get(str(target_qn))
        if target_id and target_id != node.node_id:
            replacements[node.node_id] = target_id

    if not replacements:
        return nodes, edges

    resolved_edges: list[CodeEdge] = []
    for edge in edges:
        target_id = replacements.get(edge.target_id)
        if edge.kind is CodeEdgeKind.CALLS and target_id:
            metadata = dict(edge.metadata or {})
            metadata["resolved"] = True
            resolved_edges.append(replace(edge, target_id=target_id, metadata=metadata))
        else:
            resolved_edges.append(edge)

    referenced = {edge.source_id for edge in resolved_edges} | {edge.target_id for edge in resolved_edges}
    resolved_nodes = [
        node
        for node in nodes
        if node.node_id not in replacements or node.node_id in referenced
    ]
    return resolved_nodes, resolved_edges



def java_package_name(source: bytes, root) -> str:
    for child in root.named_children:
        if child.type != "package_declaration":
            continue
        for part in child.named_children:
            if part.type in {"scoped_identifier", "identifier"}:
                return ast_text(source, part).strip()
    return ""


def java_imports(source: bytes, root) -> dict[str, str]:
    imports: dict[str, str] = {}
    for child in root.named_children:
        if child.type != "import_declaration":
            continue
        text = ast_text(source, child).strip().rstrip(";").strip()
        if not text.startswith("import "):
            continue
        text = text[len("import "):].strip()
        if text.startswith("static "):
            text = text[len("static "):].strip()
        if not text or text.endswith(".*"):
            continue
        imports[text.rsplit(".", 1)[-1]] = text
    return imports


def java_canonical_type(source: bytes, node, imports: dict[str, str]) -> str:
    value = re.sub(r"\s+", "", ast_text(source, node).strip())
    for simple, qualified in sorted(imports.items(), key=lambda item: len(item[0]), reverse=True):
        value = re.sub(rf"\b{re.escape(simple)}\b", qualified, value)
    return value


def java_parameter_types(source: bytes, parameters, imports: dict[str, str]) -> tuple[str, ...]:
    if parameters is None:
        return ()
    result: list[str] = []
    for parameter in parameters.named_children:
        if parameter.type not in {"formal_parameter", "spread_parameter"}:
            continue
        type_node = parameter.child_by_field_name("type")
        if type_node is None:
            continue
        value = java_canonical_type(source, type_node, imports)
        if parameter.type == "spread_parameter":
            value += "..."
        dims = "".join(
            ast_text(source, child).strip()
            for child in parameter.named_children
            if child.type == "dimensions"
        )
        if dims:
            value += re.sub(r"\s+", "", dims)
        result.append(value)
    return tuple(result)


def java_method_signature(source: bytes, declaration, imports: dict[str, str]) -> str:
    params = java_parameter_types(
        source,
        declaration.child_by_field_name("parameters"),
        imports,
    )
    return_node = declaration.child_by_field_name("type")
    return_type = (
        java_canonical_type(source, return_node, imports)
        if return_node is not None
        else "void"
    )
    return f"({','.join(params)})->{return_type}"


def java_resolve_type_name(name: str, package: str, imports: dict[str, str]) -> str:
    clean = re.sub(r"\s+", "", name)
    clean = re.sub(r"<.*>", "", clean)
    clean = clean.rstrip("[]")
    if clean in imports:
        return imports[clean]
    if "." in clean:
        return clean
    return f"{package}.{clean}" if package else clean


def extract_java_ast_graph(project_id: str, rel: str, file_id: str, source: str):
    raw = source.encode("utf-8")
    parser = tslp.get_parser("java")
    tree = parser.parse(raw)
    root = tree.root_node
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []

    package = java_package_name(raw, root)
    imports = java_imports(raw, root)

    def add_relation(source_id: str, target_qn: str, kind: CodeEdgeKind) -> None:
        ext_key = f"java-rel:{kind.value}:{target_qn}"
        target_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, ext_key)
        metadata = {
            "parser": "tree-sitter",
            "language": "java",
            "java_target_qn": target_qn,
            "resolved": False,
            "role": kind.value,
        }
        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=target_id,
                kind=CodeNodeKind.EXTERNAL,
                name=target_qn.rsplit(".", 1)[-1],
                qualified_name=target_qn,
                language="java",
                metadata=metadata,
            )
        )
        edges.append(
            CodeEdge(
                project_id=project_id,
                source_id=source_id,
                target_id=target_id,
                kind=kind,
                metadata={"resolved": False, "language": "java"},
            )
        )

    def add_call(caller_id: str, owner_qn: str, name: str, call_kind: str, display: str) -> None:
        ext_key = f"java-call:{owner_qn}:{name}:{display}"
        target_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, ext_key)
        metadata = {
            "parser": "tree-sitter",
            "language": "java",
            "java_owner_qn": owner_qn,
            "java_method_name": name,
            "java_call_kind": call_kind,
            "resolved": False,
        }
        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=target_id,
                kind=CodeNodeKind.EXTERNAL,
                name=display,
                language="java",
                metadata=metadata,
            )
        )
        edges.append(
            CodeEdge(
                project_id=project_id,
                source_id=caller_id,
                target_id=target_id,
                kind=CodeEdgeKind.CALLS,
                metadata={
                    "resolved": False,
                    "language": "java",
                    "java_call_kind": call_kind,
                },
            )
        )

    top_kinds = {
        "class_declaration": CodeNodeKind.CLASS,
        "interface_declaration": CodeNodeKind.INTERFACE,
    }

    for child in root.named_children:
        kind = top_kinds.get(child.type)
        if kind is None:
            continue
        name = ast_name(raw, child)
        if not name:
            continue
        qn = f"{package}.{name}" if package else name
        start, end = ast_lines(raw, child)
        type_id = stable_node_id(project_id, kind, qn)
        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=type_id,
                kind=kind,
                name=name,
                path=rel,
                qualified_name=qn,
                language="java",
                line_start=start,
                line_end=end,
                metadata={"parser": "tree-sitter", "package": package},
            )
        )
        edges.append(
            CodeEdge(
                project_id=project_id,
                source_id=file_id,
                target_id=type_id,
                kind=CodeEdgeKind.DEFINES,
            )
        )

        if child.type == "class_declaration":
            superclass = child.child_by_field_name("superclass")
            if superclass is not None:
                named = list(superclass.named_children)
                if named:
                    target_qn = java_resolve_type_name(
                        ast_text(raw, named[-1]),
                        package,
                        imports,
                    )
                    add_relation(type_id, target_qn, CodeEdgeKind.INHERITS)

            interfaces = child.child_by_field_name("interfaces")
            if interfaces is not None:
                type_list = next(
                    (part for part in interfaces.named_children if part.type == "type_list"),
                    None,
                )
                if type_list is not None:
                    for part in type_list.named_children:
                        target_qn = java_resolve_type_name(
                            ast_text(raw, part),
                            package,
                            imports,
                        )
                        add_relation(type_id, target_qn, CodeEdgeKind.IMPLEMENTS)

        body = child.child_by_field_name("body")
        if body is None:
            continue

        for member in body.named_children:
            if member.type != "method_declaration":
                continue
            method = ast_name(raw, member)
            if not method:
                continue
            signature = java_method_signature(raw, member, imports)
            mqn = f"{qn}::{method}{signature}"
            ms, me = ast_lines(raw, member)
            method_id = stable_node_id(project_id, CodeNodeKind.METHOD, mqn)
            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=method_id,
                    kind=CodeNodeKind.METHOD,
                    name=method,
                    path=rel,
                    qualified_name=mqn,
                    language="java",
                    line_start=ms,
                    line_end=me,
                    metadata={
                        "parser": "tree-sitter",
                        "package": package,
                        "owner": qn,
                        "java_signature": signature,
                        "interface_method": child.type == "interface_declaration",
                    },
                )
            )
            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=type_id,
                    target_id=method_id,
                    kind=CodeEdgeKind.DEFINES,
                )
            )

            method_body = member.child_by_field_name("body")
            if method_body is None:
                continue
            for invocation in walk_ast(method_body):
                if invocation.type != "method_invocation":
                    continue
                name_node = invocation.child_by_field_name("name")
                if name_node is None:
                    continue
                called = ast_text(raw, name_node).strip()
                if not called:
                    continue
                object_node = invocation.child_by_field_name("object")
                if object_node is None:
                    add_call(method_id, qn, called, "local_method", called)
                    continue
                object_text = ast_text(raw, object_node).strip()
                if object_text == "this":
                    add_call(method_id, qn, called, "this_method", f"this.{called}")
                else:
                    add_call(
                        method_id,
                        "",
                        called,
                        "unresolved_receiver",
                        f"{object_text}.{called}",
                    )

    return tuple(nodes), tuple(edges)


def resolve_java_targets(
    nodes: list[CodeNode],
    edges: list[CodeEdge],
    seed_symbols: dict[str, str] | None = None,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    exact: dict[str, str] = {
        node.qualified_name: node.node_id
        for node in nodes
        if node.language == "java"
        and node.qualified_name
        and node.kind in {CodeNodeKind.CLASS, CodeNodeKind.INTERFACE}
    }
    methods: dict[tuple[str, str], set[str]] = {}
    for node in nodes:
        if node.language != "java" or node.kind is not CodeNodeKind.METHOD:
            continue
        owner = str(node.metadata.get("owner") or "")
        if owner:
            methods.setdefault((owner, node.name), set()).add(node.node_id)

    for qualified_name, node_id in (seed_symbols or {}).items():
        if "::" in qualified_name:
            owner, method_part = qualified_name.split("::", 1)
            method_name = method_part.split("(", 1)[0]
            if owner and method_name:
                methods.setdefault((owner, method_name), set()).add(node_id)
        elif "." in qualified_name:
            exact.setdefault(qualified_name, node_id)

    replacements: dict[str, str] = {}
    for node in nodes:
        if node.kind is not CodeNodeKind.EXTERNAL or node.language != "java":
            continue
        target_qn = str(node.metadata.get("java_target_qn") or "")
        if target_qn and target_qn in exact:
            replacements[node.node_id] = exact[target_qn]
            continue
        owner = str(node.metadata.get("java_owner_qn") or "")
        method_name = str(node.metadata.get("java_method_name") or "")
        if owner and method_name:
            candidates = methods.get((owner, method_name), set())
            if len(candidates) == 1:
                replacements[node.node_id] = next(iter(candidates))

    if not replacements:
        return nodes, edges

    resolved_edges: list[CodeEdge] = []
    for edge in edges:
        target_id = replacements.get(edge.target_id)
        if target_id and edge.kind in {
            CodeEdgeKind.CALLS,
            CodeEdgeKind.INHERITS,
            CodeEdgeKind.IMPLEMENTS,
        }:
            metadata = dict(edge.metadata or {})
            metadata["resolved"] = True
            resolved_edges.append(
                replace(edge, target_id=target_id, metadata=metadata)
            )
        else:
            resolved_edges.append(edge)

    referenced = {
        endpoint
        for edge in resolved_edges
        for endpoint in (edge.source_id, edge.target_id)
    }
    resolved_nodes = [
        node
        for node in nodes
        if node.node_id not in replacements or node.node_id in referenced
    ]
    return resolved_nodes, resolved_edges


def extract_php_ast_graph(project_id: str, rel: str, file_id: str, source: str):
    raw = source.encode("utf-8")
    parser = tslp.get_parser("php")
    tree = parser.parse(raw)
    root = tree.root_node
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []
    namespace = ""
    for child in root.named_children:
        if child.type == "namespace_definition":
            text = ast_text(raw, child).strip()
            if text.startswith("namespace "):
                namespace = text[len("namespace "):].rstrip(";{}").strip()
            break
    use_aliases: dict[str, str] = {}
    for child in root.named_children:
        if child.type != "namespace_use_declaration":
            continue
        for clause in child.named_children:
            if clause.type != "namespace_use_clause":
                continue
            qualified_node = next((n for n in clause.named_children if n.type == "qualified_name"), None)
            if qualified_node is None:
                continue
            target = ast_text(raw, qualified_node).strip()
            alias_node = clause.child_by_field_name("alias")
            local = ast_text(raw, alias_node).strip() if alias_node is not None else target.rsplit(chr(92), 1)[-1]
            if target and local:
                use_aliases[local] = target
    top_map = {"class_declaration": CodeNodeKind.CLASS, "interface_declaration": CodeNodeKind.INTERFACE, "trait_declaration": CodeNodeKind.TRAIT, "enum_declaration": CodeNodeKind.ENUM, "function_definition": CodeNodeKind.FUNCTION}
    for child in root.named_children:
        kind = top_map.get(child.type)
        if kind is None:
            continue
        name = ast_name(raw, child)
        if not name:
            continue
        qn = (namespace + "\\" + name) if namespace else name
        start, end = ast_lines(raw, child)
        node_id = stable_node_id(project_id, kind, qn)
        nodes.append(CodeNode(project_id=project_id, node_id=node_id, kind=kind, name=name, path=rel, qualified_name=qn, language="php", line_start=start, line_end=end, metadata={"parser":"tree-sitter","namespace":namespace}))
        edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=node_id, kind=CodeEdgeKind.DEFINES))
        if child.type == "class_declaration":
            base_clause = next((n for n in child.named_children if n.type == "base_clause"), None)
            if base_clause is not None:
                base_name_node = next((n for n in base_clause.named_children if n.type == "name"), None)
                if base_name_node is not None:
                    base_name = ast_text(raw, base_name_node).strip()
                    base_name = use_aliases.get(base_name, base_name)
                    ext_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, f"php:type:{base_name}")
                    nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=base_name, language="php", metadata={"parser":"tree-sitter","php_type_target":base_name}))
                    edges.append(CodeEdge(project_id=project_id, source_id=node_id, target_id=ext_id, kind=CodeEdgeKind.INHERITS, metadata={"php_type_target":base_name}))
            interface_clause = next((n for n in child.named_children if n.type == "class_interface_clause"), None)
            if interface_clause is not None:
                for interface_node in interface_clause.named_children:
                    if interface_node.type != "name":
                        continue
                    interface_name = ast_text(raw, interface_node).strip()
                    interface_name = use_aliases.get(interface_name, interface_name)
                    if not interface_name:
                        continue
                    ext_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, f"php:type:{interface_name}")
                    nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=interface_name, language="php", metadata={"parser":"tree-sitter","php_type_target":interface_name}))
                    edges.append(CodeEdge(project_id=project_id, source_id=node_id, target_id=ext_id, kind=CodeEdgeKind.IMPLEMENTS, metadata={"php_type_target":interface_name}))
        body = child.child_by_field_name("body")
        if body is None:
            continue
        for member in body.named_children:
            if member.type != "method_declaration":
                continue
            method = ast_name(raw, member)
            if not method:
                continue
            mqn = qn + "::" + method
            ms, me = ast_lines(raw, member)
            method_id = stable_node_id(project_id, CodeNodeKind.METHOD, mqn)
            nodes.append(CodeNode(project_id=project_id, node_id=method_id, kind=CodeNodeKind.METHOD, name=method, path=rel, qualified_name=mqn, language="php", line_start=ms, line_end=me, metadata={"parser":"tree-sitter","namespace":namespace,"owner":qn}))
            edges.append(CodeEdge(project_id=project_id, source_id=node_id, target_id=method_id, kind=CodeEdgeKind.DEFINES))
            parameter_types: dict[str, str] = {}
            parameters = next((n for n in member.named_children if n.type == "formal_parameters"), None)
            if parameters is not None:
                for parameter in parameters.named_children:
                    if parameter.type != "simple_parameter":
                        continue
                    type_node = parameter.child_by_field_name("type")
                    name_node = parameter.child_by_field_name("name")
                    if type_node is None or name_node is None:
                        continue
                    type_name = ast_text(raw, type_node).strip()
                    variable_name = ast_text(raw, name_node).strip().lstrip("$")
                    if type_name and variable_name:
                        parameter_types[variable_name] = use_aliases.get(type_name, type_name)
            for expr in walk_ast(member):
                owner = ""
                call_name = ""
                if expr.type == "scoped_call_expression":
                    scope_node = expr.child_by_field_name("scope")
                    call_name_node = expr.child_by_field_name("name")
                    if scope_node is None or call_name_node is None:
                        continue
                    scope = ast_text(raw, scope_node).strip()
                    call_name = ast_text(raw, call_name_node).strip()
                    owner = use_aliases.get(scope, scope)
                elif expr.type == "member_call_expression":
                    object_node = expr.child_by_field_name("object")
                    call_name_node = expr.child_by_field_name("name")
                    if object_node is None or call_name_node is None:
                        continue
                    variable_name = ast_text(raw, object_node).strip().lstrip("$")
                    call_name = ast_text(raw, call_name_node).strip()
                    owner = parameter_types.get(variable_name, "")
                else:
                    continue
                if not owner or not call_name:
                    continue
                ext_id = stable_node_id(project_id, CodeNodeKind.EXTERNAL, f"php:method:{owner}::{call_name}")
                nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=f"{owner}::{call_name}", language="php", metadata={"parser":"tree-sitter","php_owner_target":owner,"php_method_name":call_name}))
                edges.append(CodeEdge(project_id=project_id, source_id=method_id, target_id=ext_id, kind=CodeEdgeKind.CALLS, metadata={"php_owner_target":owner,"php_method_name":call_name}))
    return tuple(nodes), tuple(edges)


def resolve_php_targets(
    nodes: list[CodeNode],
    edges: list[CodeEdge],
    seed_symbols: dict[str, str] | None = None,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    by_id = {node.node_id: node for node in nodes}
    exact: dict[str, str] = {}
    short: dict[str, set[str]] = {}
    type_kinds = {CodeNodeKind.CLASS, CodeNodeKind.INTERFACE, CodeNodeKind.TRAIT, CodeNodeKind.ENUM}
    methods: dict[tuple[str, str], set[str]] = {}

    for node in nodes:
        if node.language != "php" or node.kind not in type_kinds or not node.qualified_name:
            continue
        exact[node.qualified_name] = node.node_id
        short.setdefault(node.qualified_name.rsplit(chr(92), 1)[-1], set()).add(node.node_id)

    for node in nodes:
        if node.language != "php" or node.kind is not CodeNodeKind.METHOD:
            continue
        owner = str(node.metadata.get("owner") or "")
        if owner:
            methods.setdefault((owner, node.name), set()).add(node.node_id)

    for qualified, node_id in (seed_symbols or {}).items():
        if "::" in qualified:
            owner, method = qualified.rsplit("::", 1)
            if owner and method:
                methods.setdefault((owner, method), set()).add(node_id)
            continue
        exact.setdefault(qualified, node_id)
        short.setdefault(qualified.rsplit(chr(92), 1)[-1], set()).add(node_id)

    resolved_edges: list[CodeEdge] = []
    for edge in edges:
        if edge.kind not in {CodeEdgeKind.CALLS, CodeEdgeKind.INHERITS, CodeEdgeKind.IMPLEMENTS}:
            resolved_edges.append(edge)
            continue
        target = by_id.get(edge.target_id)
        source = by_id.get(edge.source_id)
        if target is None or target.kind is not CodeNodeKind.EXTERNAL or target.language != "php":
            resolved_edges.append(edge)
            continue
        if edge.kind is CodeEdgeKind.CALLS:
            owner = str(target.metadata.get("php_owner_target") or edge.metadata.get("php_owner_target") or "")
            method_name = str(target.metadata.get("php_method_name") or edge.metadata.get("php_method_name") or "")
            candidates = methods.get((owner, method_name), set())
            if not candidates and source is not None and owner and chr(92) not in owner:
                namespace = str(source.metadata.get("namespace") or "")
                if namespace:
                    candidates = methods.get((namespace + chr(92) + owner, method_name), set())
            if len(candidates) == 1:
                metadata = dict(edge.metadata or {})
                metadata["resolved"] = True
                resolved_edges.append(replace(edge, target_id=next(iter(candidates)), metadata=metadata))
            else:
                resolved_edges.append(edge)
            continue
        target_name = str(target.metadata.get("php_type_target") or edge.metadata.get("php_type_target") or "")
        if not target_name:
            resolved_edges.append(edge)
            continue
        target_id = exact.get(target_name)
        if target_id is None and source is not None:
            namespace = str(source.metadata.get("namespace") or "")
            if namespace:
                target_id = exact.get(namespace + chr(92) + target_name)
        if target_id is None:
            candidates = short.get(target_name.rsplit(chr(92), 1)[-1], set())
            if len(candidates) == 1:
                target_id = next(iter(candidates))
        if target_id is None:
            resolved_edges.append(edge)
            continue
        metadata = dict(edge.metadata or {})
        metadata["resolved"] = True
        resolved_edges.append(replace(edge, target_id=target_id, metadata=metadata))

    referenced = {endpoint for edge in resolved_edges for endpoint in (edge.source_id, edge.target_id)}
    resolved_nodes = [node for node in nodes if node.kind is not CodeNodeKind.EXTERNAL or node.node_id in referenced]
    return resolved_nodes, resolved_edges


def resolve_laravel_route_targets(nodes: list[CodeNode], edges: list[CodeEdge], seed_symbols: dict[str, str] | None = None) -> tuple[list[CodeNode], list[CodeEdge]]:
    by_id = {n.node_id: n for n in nodes}
    methods: dict[tuple[str, str], set[str]] = {}
    for node in nodes:
        if node.kind is not CodeNodeKind.METHOD:
            continue
        owner = str(node.metadata.get("owner", ""))
        short_owner = owner.rsplit(chr(92), 1)[-1]
        methods.setdefault((short_owner, node.name), set()).add(node.node_id)

    for qualified, node_id in (seed_symbols or {}).items():
        if "::" not in qualified:
            continue
        owner, method = qualified.rsplit("::", 1)
        short_owner = owner.rsplit(chr(92), 1)[-1]
        methods.setdefault((short_owner, method), set()).add(node_id)

    pattern = re.compile(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)::class\s*,\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]\s*\]")
    resolved_external: set[str] = set()
    out_edges: list[CodeEdge] = []

    for edge in edges:
        if edge.kind is not CodeEdgeKind.ROUTES_TO:
            out_edges.append(edge)
            continue
        target = by_id.get(edge.target_id)
        if target is None or target.kind is not CodeNodeKind.EXTERNAL or target.metadata.get("role") != "route_target":
            out_edges.append(edge)
            continue
        match = pattern.fullmatch(target.name.strip())
        if match is None:
            out_edges.append(edge)
            continue
        candidates = methods.get((match.group(1), match.group(2)), set())
        if len(candidates) != 1:
            out_edges.append(edge)
            continue
        method_id = next(iter(candidates))
        resolved_external.add(target.node_id)
        out_edges.append(CodeEdge(project_id=edge.project_id, source_id=edge.source_id, target_id=method_id, kind=CodeEdgeKind.ROUTES_TO, metadata={**dict(edge.metadata), "resolved": True}))

    out_nodes = [n for n in nodes if n.node_id not in resolved_external]
    return out_nodes, out_edges
