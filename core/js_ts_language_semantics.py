from __future__ import annotations

import posixpath
import re

import tree_sitter_language_pack as tslp

from .code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind

JS_LANGUAGES = {"javascript", "typescript", "tsx", "vue"}
CALLABLE_TYPES = {"function_declaration", "method_definition", "arrow_function", "function_expression"}


def _text(raw: bytes, node) -> str:
    return raw[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _line(node, offset: int = 0) -> int:
    return int(node.start_point[0]) + 1 + offset


def _walk(node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


JSX_NODE_TYPES = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}


def _is_react_component(name: str, node) -> bool:
    return bool(
        name
        and name[0].isupper()
        and any(child.type in JSX_NODE_TYPES for child in _walk(node))
    )


def _field(node, name: str):
    return node.child_by_field_name(name)


def _clean_type(text: str) -> str:
    text = text.strip()
    if text.startswith(":"):
        text = text[1:].strip()
    return re.sub(r"\s+", "", text) or "?"


def _params(raw: bytes, params) -> tuple[str, ...]:
    if params is None:
        return ()
    result: list[str] = []
    for item in params.named_children:
        type_node = _field(item, "type")
        result.append(_clean_type(_text(raw, type_node)) if type_node else "?")
    return tuple(result)


def _signature(raw: bytes, node) -> str:
    params = _params(raw, _field(node, "parameters"))
    ret = _field(node, "return_type")
    return f"({','.join(params)})->{_clean_type(_text(raw, ret)) if ret else '?'}"


def _import_map(raw: bytes, root) -> dict[str, tuple[str, str]]:
    imports: dict[str, tuple[str, str]] = {}
    for node in _walk(root):
        if node.type != "import_statement":
            continue
        source_node = _field(node, "source")
        if source_node is None:
            continue
        module = _text(raw, source_node).strip().strip("'\"")
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            continue
        clause_text = _text(raw, clause).strip()
        default = re.match(r"^([A-Za-z_$][\w$]*)", clause_text)
        if default:
            imports[default.group(1)] = (module, "default")
        ns = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause_text)
        if ns:
            imports[ns.group(1)] = (module, "*")
        named = re.search(r"\{(.*?)\}", clause_text, re.DOTALL)
        if named:
            for part in named.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                bits = re.split(r"\s+as\s+", part)
                imported = bits[0].strip()
                local = bits[-1].strip()
                if local:
                    imports[local] = (module, imported)
    return imports


def _script_units(source: str, language: str):
    if language != "vue":
        yield language, source, 0
        return
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", source, re.IGNORECASE | re.DOTALL):
        attrs, body = match.group(1), match.group(2)
        parser_language = "typescript" if re.search(r"\blang\s*=\s*['\"]ts['\"]", attrs, re.IGNORECASE) else "javascript"
        offset = source[:match.start(2)].count("\n")
        yield parser_language, body, offset


def extract_js_family_ast_graph(project_id: str, rel: str, file_id: str, source: str, language: str, stable_id):
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []

    for parser_language, unit_source, line_offset in _script_units(source, language):
        raw = unit_source.encode("utf-8")
        parser = tslp.get_parser(parser_language)
        tree = parser.parse(raw)
        root = tree.root_node
        imports = _import_map(raw, root)

        for local, (module, imported) in imports.items():
            ext_id = stable_id(project_id, CodeNodeKind.EXTERNAL, f"{language}:{module}")
            nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=module, language=language, metadata={"parser": "tree-sitter", "js_import": True}))
            edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=ext_id, kind=CodeEdgeKind.IMPORTS, metadata={"local": local, "imported": imported, "module": module}))

        type_nodes: dict[str, str] = {}
        methods: dict[tuple[str, str], str] = {}
        functions: dict[str, str] = {}
        callable_ids: dict[tuple[int, int], tuple[str, str | None]] = {}

        def add_node(kind, name, qualified, node, metadata=None, _line_offset=line_offset):
            node_id = stable_id(project_id, kind, qualified)
            nodes.append(CodeNode(project_id=project_id, node_id=node_id, kind=kind, name=name, path=rel, qualified_name=qualified, language=language, line_start=_line(node, _line_offset), line_end=int(node.end_point[0]) + 1 + _line_offset, metadata={"parser": "tree-sitter", **(metadata or {})}))
            edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=node_id, kind=CodeEdgeKind.DEFINES))
            return node_id

        for node in _walk(root):
            if node.type not in {"class_declaration", "interface_declaration"}:
                continue
            name_node = _field(node, "name")
            if name_node is None:
                continue
            name = _text(raw, name_node)
            kind = CodeNodeKind.CLASS if node.type == "class_declaration" else CodeNodeKind.INTERFACE
            qualified = f"{rel}::{name}"
            type_id = add_node(kind, name, qualified, node)
            type_nodes[name] = type_id
            body = _field(node, "body")
            if body:
                for member in body.named_children:
                    if member.type not in {"method_definition", "method_signature"}:
                        continue
                    mn = _field(member, "name")
                    if mn is None:
                        continue
                    method_name = _text(raw, mn)
                    sig = _signature(raw, member)
                    method_qn = f"{qualified}::{method_name}{sig}"
                    method_id = add_node(CodeNodeKind.METHOD, method_name, method_qn, member, {"owner": qualified, "js_signature": sig, "interface_method": kind is CodeNodeKind.INTERFACE})
                    methods[(qualified, method_name)] = method_id
                    callable_ids[(member.start_byte, member.end_byte)] = (method_id, qualified)

            if node.type == "class_declaration":
                header = _text(raw, node)
                body_pos = header.find("{")
                header = header[:body_pos if body_pos >= 0 else len(header)]
                ext = re.search(r"\bextends\s+([A-Za-z_$][\w$]*)", header)
                impl = re.search(r"\bimplements\s+(.+)$", header)
                targets: list[tuple[CodeEdgeKind, str]] = []
                if ext:
                    targets.append((CodeEdgeKind.INHERITS, ext.group(1)))
                if impl:
                    for target in impl.group(1).split(","):
                        target = target.strip().split("<", 1)[0].strip()
                        if target:
                            targets.append((CodeEdgeKind.IMPLEMENTS, target))
                for edge_kind, target_name in targets:
                    target_id = type_nodes.get(target_name)
                    resolved = target_id is not None
                    if target_id is None:
                        target_id = stable_id(project_id, CodeNodeKind.EXTERNAL, f"{rel}::type:{target_name}")
                        nodes.append(CodeNode(project_id=project_id, node_id=target_id, kind=CodeNodeKind.EXTERNAL, name=target_name, language=language, metadata={"parser": "tree-sitter", "js_type_target": target_name}))
                    edges.append(CodeEdge(project_id=project_id, source_id=type_id, target_id=target_id, kind=edge_kind, metadata={"resolved": resolved, "language": language}))

        for node in root.named_children:
            target = node
            if node.type == "export_statement":
                decl = _field(node, "declaration")
                if decl is not None:
                    target = decl
            if target.type == "function_declaration":
                name_node = _field(target, "name")
                if name_node is None:
                    continue
                name = _text(raw, name_node)
                sig = _signature(raw, target)
                qn = f"{rel}::{name}{sig}"
                kind = CodeNodeKind.COMPONENT if _is_react_component(name, target) else CodeNodeKind.FUNCTION
                fid = add_node(kind, name, qn, target, {"js_signature": sig})
                functions[name] = fid
                callable_ids[(target.start_byte, target.end_byte)] = (fid, None)
            elif target.type in {"lexical_declaration", "variable_declaration"}:
                for decl in target.named_children:
                    if decl.type != "variable_declarator":
                        continue
                    value = _field(decl, "value")
                    name_node = _field(decl, "name")
                    if value is None or name_node is None or value.type not in {"arrow_function", "function_expression"}:
                        continue
                    name = _text(raw, name_node)
                    sig = _signature(raw, value)
                    qn = f"{rel}::{name}{sig}"
                    kind = CodeNodeKind.COMPONENT if _is_react_component(name, value) else CodeNodeKind.FUNCTION
                    fid = add_node(kind, name, qn, value, {"js_signature": sig, "function_form": value.type})
                    functions[name] = fid
                    callable_ids[(value.start_byte, value.end_byte)] = (fid, None)

        for decl in _walk(root):
            if decl.type != "variable_declarator":
                continue
            value = _field(decl, "value")
            name_node = _field(decl, "name")
            if value is None or name_node is None or value.type not in {"arrow_function", "function_expression"}:
                continue
            key = (value.start_byte, value.end_byte)
            if key in callable_ids:
                continue
            name = _text(raw, name_node)
            sig = _signature(raw, value)
            qn = f"{rel}::{name}{sig}"
            kind = CodeNodeKind.COMPONENT if _is_react_component(name, value) else CodeNodeKind.FUNCTION
            fid = add_node(kind, name, qn, value, {"js_signature": sig, "function_form": value.type})
            functions.setdefault(name, fid)
            callable_ids[key] = (fid, None)

        node_lookup = {(n.start_byte, n.end_byte): n for n in _walk(root)}

        def external_call(source_id: str, display: str, call_kind: str, metadata=None):
            ext_id = stable_id(project_id, CodeNodeKind.EXTERNAL, f"{language}:call:{display}")
            nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=display, language=language, metadata={"parser": "tree-sitter", "js_call_target": display, **(metadata or {})}))
            edges.append(CodeEdge(project_id=project_id, source_id=source_id, target_id=ext_id, kind=CodeEdgeKind.CALLS, metadata={"resolved": False, "language": language, "js_call_kind": call_kind, **(metadata or {})}))

        def external_use(source_id: str, display: str, metadata=None):
            ext_id = stable_id(project_id, CodeNodeKind.EXTERNAL, f"{language}:use:{display}")
            nodes.append(CodeNode(project_id=project_id, node_id=ext_id, kind=CodeNodeKind.EXTERNAL, name=display, language=language, metadata={"parser": "tree-sitter", "js_use_target": display}))
            edges.append(CodeEdge(project_id=project_id, source_id=source_id, target_id=ext_id, kind=CodeEdgeKind.USES, metadata={"resolved": False, "language": language, **(metadata or {})}))

        for key, (source_id, owner_qn) in list(callable_ids.items()):
            owner_node = node_lookup.get(key)
            if owner_node is None:
                continue
            stack = list(reversed(owner_node.named_children))
            while stack:
                node = stack.pop()
                if node.type in CALLABLE_TYPES and (node.start_byte, node.end_byte) != key:
                    continue
                if node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
                    name_node = _field(node, "name")
                    jsx_name = _text(raw, name_node) if name_node is not None else ""
                    if re.fullmatch(r"[A-Z][A-Za-z0-9_$]*", jsx_name):
                        target_id = functions.get(jsx_name)
                        if target_id:
                            edges.append(
                                CodeEdge(
                                    project_id=project_id,
                                    source_id=source_id,
                                    target_id=target_id,
                                    kind=CodeEdgeKind.USES,
                                    metadata={
                                        "resolved": True,
                                        "language": language,
                                        "js_use_kind": "local_component",
                                    },
                                )
                            )
                        elif jsx_name in imports:
                            module, imported = imports[jsx_name]
                            external_use(
                                source_id,
                                f"{module}:{imported}",
                                {
                                    "module": module,
                                    "imported": imported,
                                    "js_use_kind": "import_component",
                                },
                            )

                if node.type == "call_expression":
                    fn = _field(node, "function")
                    if fn is not None:
                        if fn.type == "identifier":
                            called = _text(raw, fn)
                            if called == "fetch":
                                args = _field(node, "arguments")
                                first_arg = (
                                    args.named_children[0]
                                    if args is not None and args.named_children
                                    else None
                                )
                                fetch_path = ""
                                external_http = False
                                if first_arg is not None:
                                    first_arg_text = _text(raw, first_arg)
                                    if first_arg.type == "string":
                                        fetch_path = first_arg_text.strip("'\\\"")
                                    elif first_arg.type == "template_string":
                                        template_match = re.search(
                                            r"\$\{[^}]+\}(/[^`]*)",
                                            first_arg_text,
                                        )
                                        if template_match is not None:
                                            fetch_path = template_match.group(1)
                                            external_http = True

                                if fetch_path.startswith("/"):
                                    http_method = "GET"
                                    if args is not None and len(args.named_children) > 1:
                                        options_text = _text(raw, args.named_children[1])
                                        method_match = re.search(
                                            r"""\bmethod\s*:\s*["']([A-Za-z]+)["']""",
                                            options_text,
                                        )
                                        if method_match is not None:
                                            http_method = method_match.group(1).upper()

                                    if external_http:
                                        external_call(
                                            source_id,
                                            f"{http_method} {fetch_path}",
                                            "fetch_http",
                                            {
                                                "http_dependency": True,
                                                "uri": fetch_path,
                                                "http_method": http_method,
                                            },
                                        )
                                    else:
                                        external_call(
                                            source_id,
                                            fetch_path,
                                            "fetch_route",
                                            {
                                                "fetch_path": fetch_path,
                                                "http_method": http_method,
                                            },
                                        )
                                    stack.extend(reversed(node.named_children))
                                    continue
                            target_id = methods.get((owner_qn, called)) if owner_qn else None
                            target_id = target_id or functions.get(called)
                            if target_id:
                                edges.append(CodeEdge(project_id=project_id, source_id=source_id, target_id=target_id, kind=CodeEdgeKind.CALLS, metadata={"resolved": True, "language": language, "js_call_kind": "local"}))
                            elif called in imports:
                                module, imported = imports[called]
                                external_call(source_id, f"{module}:{imported}", "import", {"module": module, "imported": imported})
                            else:
                                external_call(source_id, called, "unresolved_identifier")
                        elif fn.type == "member_expression":
                            obj = _field(fn, "object")
                            prop = _field(fn, "property")
                            obj_text = _text(raw, obj) if obj else ""
                            called = _text(raw, prop) if prop else _text(raw, fn)
                            if obj_text == "this" and owner_qn and (owner_qn, called) in methods:
                                edges.append(CodeEdge(project_id=project_id, source_id=source_id, target_id=methods[(owner_qn, called)], kind=CodeEdgeKind.CALLS, metadata={"resolved": True, "language": language, "js_call_kind": "this_method"}))
                            elif obj_text in imports:
                                module, imported = imports[obj_text]
                                external_call(source_id, f"{module}:{called}", "import_member", {"module": module, "imported": called, "via": imported})
                            else:
                                external_call(source_id, _text(raw, fn), "unresolved_member")
                stack.extend(reversed(node.named_children))

        stack = list(reversed(root.named_children))
        while stack:
            node = stack.pop()
            if node.type in CALLABLE_TYPES:
                continue
            if node.type == "call_expression":
                fn = _field(node, "function")
                if fn is not None:
                    if fn.type == "identifier":
                        called = _text(raw, fn)
                        target_id = functions.get(called)
                        if target_id:
                            edges.append(CodeEdge(project_id=project_id, source_id=file_id, target_id=target_id, kind=CodeEdgeKind.CALLS, metadata={"resolved": True, "language": language, "js_call_kind": "module_local"}))
                        elif called in imports:
                            module, imported = imports[called]
                            external_call(file_id, f"{module}:{imported}", "import", {"module": module, "imported": imported, "scope": "module"})
                        else:
                            external_call(file_id, called, "unresolved_identifier", {"scope": "module"})
                    elif fn.type == "member_expression":
                        obj = _field(fn, "object")
                        prop = _field(fn, "property")
                        obj_text = _text(raw, obj) if obj else ""
                        called = _text(raw, prop) if prop else _text(raw, fn)
                        if obj_text in imports:
                            module, imported = imports[obj_text]
                            external_call(file_id, f"{module}:{called}", "import_member", {"module": module, "imported": called, "via": imported, "scope": "module"})
                        else:
                            external_call(file_id, _text(raw, fn), "unresolved_member", {"scope": "module"})
            stack.extend(reversed(node.named_children))

    return tuple(nodes), tuple(edges)


_JS_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")


def _js_module_candidates(source_path: str, module: str) -> tuple[str, ...]:
    if not module.startswith("."):
        return ()
    source_dir = posixpath.dirname(source_path)
    base = posixpath.normpath(posixpath.join(source_dir, module))
    suffix = posixpath.splitext(base)[1].lower()
    candidates = []
    if suffix in _JS_SOURCE_SUFFIXES:
        candidates.append(base)
    else:
        for ext in _JS_SOURCE_SUFFIXES:
            candidates.append(base + ext)
        for ext in _JS_SOURCE_SUFFIXES:
            candidates.append(posixpath.join(base, "index" + ext))
    return tuple(dict.fromkeys(candidates))
_JS_TOP_LEVEL_KINDS = {
    CodeNodeKind.FUNCTION,
    CodeNodeKind.CLASS,
    CodeNodeKind.INTERFACE,
    CodeNodeKind.ENUM,
    CodeNodeKind.VARIABLE,
    CodeNodeKind.COMPONENT,
}


def resolve_js_targets(
    project_id: str,
    nodes: list[CodeNode],
    edges: list[CodeEdge],
    seed_symbols: dict[str, str] | None = None,
    stable_id=None,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    """Resolve relative JS/TS/Vue imports and calls across files."""
    if stable_id is None:
        raise ValueError("stable_id is required")

    seed_symbols = seed_symbols or {}
    node_by_id = {node.node_id: node for node in nodes}
    files = {
        node.path: node
        for node in nodes
        if node.kind is CodeNodeKind.FILE and node.path
    }
    seed_paths = {
        qualified.split("::", 1)[0]
        for qualified in seed_symbols
        if "::" in qualified
    }

    def module_path(source_path: str, module: str) -> str | None:
        for candidate in _js_module_candidates(source_path, module):
            if candidate in files or candidate in seed_paths:
                return candidate
        return None

    def symbol_id(path: str, name: str) -> str | None:
        ids = [
            node.node_id
            for node in nodes
            if node.path == path
            and node.name == name
            and node.kind in _JS_TOP_LEVEL_KINDS
        ]
        prefix = f"{path}::{name}"
        ids.extend(
            node_id
            for qualified, node_id in seed_symbols.items()
            if qualified == prefix or qualified.startswith(prefix + "(")
        )
        ids = list(dict.fromkeys(ids))
        return ids[0] if len(ids) == 1 else None

    imports: dict[tuple[str, str], tuple[str, str]] = {}

    for edge in edges:
        if edge.kind is not CodeEdgeKind.IMPORTS:
            continue

        source = node_by_id.get(edge.source_id)
        if source is None or not source.path:
            continue

        local = str(edge.metadata.get("local") or "")
        module = str(edge.metadata.get("module") or "")
        imported = str(edge.metadata.get("imported") or "")

        if local and module:
            imports[(source.path, local)] = (module, imported)

    resolved: list[CodeEdge] = []

    for edge in edges:
        source = node_by_id.get(edge.source_id)
        source_path = source.path if source else None
        metadata = dict(edge.metadata)

        if edge.kind is CodeEdgeKind.IMPORTS and source_path:
            module = str(metadata.get("module") or "")
            path = module_path(source_path, module)

            if path is not None:
                target = files.get(path)
                target_id = (
                    target.node_id
                    if target
                    else stable_id(project_id, CodeNodeKind.FILE, path)
                )

                metadata.update(
                    resolved=True,
                    resolved_path=path,
                    resolution="relative_module",
                )

                resolved.append(
                    CodeEdge(
                        project_id,
                        edge.source_id,
                        target_id,
                        edge.kind,
                        metadata,
                    )
                )
                continue

        if (
            edge.kind is CodeEdgeKind.CALLS
            and metadata.get("js_call_kind") == "fetch_route"
        ):
            fetch_path = str(metadata.get("fetch_path") or "")
            method = str(metadata.get("http_method") or "GET").upper()
            candidates = [
                node
                for node in nodes
                if node.kind is CodeNodeKind.ROUTE
                and node.metadata.get("framework") == "next"
                and node.metadata.get("http_method") == method
                and node.metadata.get("uri") == fetch_path
            ]

            if len(candidates) == 1:
                metadata.update(
                    resolved=True,
                    resolution="next_fetch_route",
                )
                resolved.append(
                    CodeEdge(
                        project_id,
                        edge.source_id,
                        candidates[0].node_id,
                        edge.kind,
                        metadata,
                    )
                )
                continue

        if (
            edge.kind in {CodeEdgeKind.CALLS, CodeEdgeKind.USES}
            and source_path
            and metadata.get("module")
        ):
            module = str(metadata["module"])
            imported = str(metadata.get("imported") or "")
            path = module_path(source_path, module)
            target_id = symbol_id(path, imported) if path else None

            if path and target_id:
                metadata.update(
                    resolved=True,
                    resolved_path=path,
                    resolution="relative_import_symbol",
                )

                resolved.append(
                    CodeEdge(
                        project_id,
                        edge.source_id,
                        target_id,
                        edge.kind,
                        metadata,
                    )
                )
                continue

        if (
            edge.kind in {CodeEdgeKind.INHERITS, CodeEdgeKind.IMPLEMENTS}
            and source_path
        ):
            target = node_by_id.get(edge.target_id)
            target_name = (
                str(target.metadata.get("js_type_target") or "")
                if target
                else ""
            )

            imported_info = imports.get((source_path, target_name))

            if imported_info:
                module, imported = imported_info
                path = module_path(source_path, module)
                lookup = target_name if imported == "default" else imported
                target_id = symbol_id(path, lookup) if path else None

                if path and target_id:
                    metadata.update(
                        resolved=True,
                        resolved_path=path,
                        resolution="relative_import_type",
                    )

                    resolved.append(
                        CodeEdge(
                            project_id,
                            edge.source_id,
                            target_id,
                            edge.kind,
                            metadata,
                        )
                    )
                    continue

        resolved.append(edge)

    return nodes, resolved
