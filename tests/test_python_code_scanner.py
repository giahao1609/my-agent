from core.code_graph import CodeEdgeKind, CodeNodeKind
from core.python_code_scanner import scan_python_project


def test_python_scanner_builds_symbols_and_calls(tmp_path):
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import helper\n\ndef run():\n    return helper()\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    by_qn = {n.qualified_name: n for n in result.nodes if n.qualified_name}
    assert "a.helper" in by_qn
    assert "b.run" in by_qn
    node_by_id = {n.node_id: n for n in result.nodes}
    pairs = {(node_by_id[e.source_id].qualified_name, node_by_id[e.target_id].qualified_name, e.kind) for e in result.edges if e.source_id in node_by_id and e.target_id in node_by_id}
    assert ("b", "a", CodeEdgeKind.IMPORTS) in pairs
    assert ("b.run", "a.helper", CodeEdgeKind.CALLS) in pairs


def test_symbol_ast_hash_changes_when_logic_changes(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def calc():\n    return 1\n", encoding="utf-8")
    first = scan_python_project("demo", tmp_path)
    n1 = next(n for n in first.nodes if n.qualified_name == "a.calc")
    f.write_text("def calc():\n    return 2\n", encoding="utf-8")
    second = scan_python_project("demo", tmp_path)
    n2 = next(n for n in second.nodes if n.qualified_name == "a.calc")
    assert n1.node_id == n2.node_id
    assert n1.metadata["ast_hash"] != n2.metadata["ast_hash"]


def test_unsaved_overlay_is_preferred(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def calc():\n    return 1\n", encoding="utf-8")
    disk = scan_python_project("demo", tmp_path)
    overlay = scan_python_project("demo", tmp_path, overlay_files={"a.py": "def calc():\n    return 999\n"})
    n1 = next(n for n in disk.nodes if n.qualified_name == "a.calc")
    n2 = next(n for n in overlay.nodes if n.qualified_name == "a.calc")
    assert n1.node_id == n2.node_id
    assert n1.metadata["ast_hash"] != n2.metadata["ast_hash"]
    assert f.read_text(encoding="utf-8") == "def calc():\n    return 1\n"


def test_scanner_marks_tests_and_tests_edges(tmp_path):
    (tmp_path / "app.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("from app import helper\n\ndef test_helper():\n    assert helper() == 1\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    test_node = next(n for n in result.nodes if n.name == "test_helper")
    helper = next(n for n in result.nodes if n.qualified_name == "app.helper")
    assert test_node.kind == CodeNodeKind.TEST
    assert any(e.source_id == test_node.node_id and e.target_id == helper.node_id and e.kind == CodeEdgeKind.TESTS for e in result.edges)


def test_scanner_builds_inheritance_edge(tmp_path):
    (tmp_path / "models.py").write_text("class Base:\n    pass\n\nclass Child(Base):\n    pass\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    base = next(n for n in result.nodes if n.qualified_name == "models.Base")
    child = next(n for n in result.nodes if n.qualified_name == "models.Child")
    assert any(e.source_id == child.node_id and e.target_id == base.node_id and e.kind == CodeEdgeKind.INHERITS for e in result.edges)


def test_python_cross_file_import_alias_resolves_call(tmp_path):
    (tmp_path / "service.py").write_text("def create_order():\n    return 1\n", encoding="utf-8")
    (tmp_path / "controller.py").write_text("from service import create_order as make_order\n\ndef handle():\n    return make_order()\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    source = next(n for n in result.nodes if n.qualified_name == "controller.handle")
    target = next(n for n in result.nodes if n.qualified_name == "service.create_order")
    assert any(e.kind is CodeEdgeKind.CALLS and e.source_id == source.node_id and e.target_id == target.node_id for e in result.edges)




def test_python_cross_file_module_alias_resolves_call(tmp_path):
    (tmp_path / "service.py").write_text("def create_order():\n    return 1\n", encoding="utf-8")
    (tmp_path / "controller.py").write_text("import service as svc\n\ndef handle():\n    return svc.create_order()\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    source = next(n for n in result.nodes if n.qualified_name == "controller.handle")
    target = next(n for n in result.nodes if n.qualified_name == "service.create_order")
    assert any(e.kind is CodeEdgeKind.CALLS and e.source_id == source.node_id and e.target_id == target.node_id for e in result.edges)


def test_python_does_not_resolve_unimported_cross_file_call(tmp_path):
    (tmp_path / "service.py").write_text("def create_order():\n    return 1\n", encoding="utf-8")
    (tmp_path / "controller.py").write_text("def handle():\n    return create_order()\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    source = next(n for n in result.nodes if n.qualified_name == "controller.handle")
    target = next(n for n in result.nodes if n.qualified_name == "service.create_order")
    assert not any(e.kind is CodeEdgeKind.CALLS and e.source_id == source.node_id and e.target_id == target.node_id for e in result.edges)


def test_python_incremental_import_alias_resolves_from_seed_symbols(tmp_path):
    (tmp_path / "service.py").write_text("def create_order():\n    return 1\n", encoding="utf-8")
    (tmp_path / "controller.py").write_text("from service import create_order as make_order\n\ndef handle():\n    return make_order()\n", encoding="utf-8")
    full = scan_python_project("demo", tmp_path)
    target = next(n for n in full.nodes if n.qualified_name == "service.create_order")
    seed_symbols = {n.qualified_name: n.node_id for n in full.nodes if n.qualified_name}
    partial = scan_python_project("demo", tmp_path, include_paths={"controller.py"}, seed_symbols=seed_symbols)
    source = next(n for n in partial.nodes if n.qualified_name == "controller.handle")
    assert any(e.kind is CodeEdgeKind.CALLS and e.source_id == source.node_id and e.target_id == target.node_id for e in partial.edges)


def test_python_cross_file_inheritance_alias_resolves(tmp_path):
    (tmp_path / "base.py").write_text("class BaseController:\n    pass\n", encoding="utf-8")
    (tmp_path / "controller.py").write_text("from base import BaseController as Parent\n\nclass OrderController(Parent):\n    pass\n", encoding="utf-8")
    result = scan_python_project("demo", tmp_path)
    child = next(n for n in result.nodes if n.qualified_name == "controller.OrderController")
    parent = next(n for n in result.nodes if n.qualified_name == "base.BaseController")
    assert any(e.kind is CodeEdgeKind.INHERITS and e.source_id == child.node_id and e.target_id == parent.node_id for e in result.edges)
