import pytest

from core.code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore


@pytest.mark.asyncio
async def test_code_graph_traversal_and_impact(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    project_id = "demo"
    nodes = (
        CodeNode(project_id, "discount", CodeNodeKind.FUNCTION, "apply_discount"),
        CodeNode(project_id, "total", CodeNodeKind.FUNCTION, "calculate_total"),
        CodeNode(project_id, "checkout", CodeNodeKind.FUNCTION, "checkout"),
        CodeNode(project_id, "route", CodeNodeKind.ROUTE, "checkout_route"),
        CodeNode(project_id, "test", CodeNodeKind.TEST, "test_checkout"),
    )
    edges = (
        CodeEdge(project_id, "total", "discount", CodeEdgeKind.CALLS),
        CodeEdge(project_id, "checkout", "total", CodeEdgeKind.CALLS),
        CodeEdge(project_id, "route", "checkout", CodeEdgeKind.CALLS),
        CodeEdge(project_id, "test", "checkout", CodeEdgeKind.TESTS),
    )
    await store.replace_project_graph(project_id, nodes, edges)
    deps = await store.dependencies(project_id, "checkout", depth=1)
    assert {n.node_id for n in deps} == {"total"}
    users = await store.dependents(project_id, "discount", depth=3)
    assert {n.node_id for n in users} == {"total", "checkout", "route", "test"}
    impact = await store.impact(project_id, "discount", max_depth=3)
    assert {n.node_id for n in impact.direct_dependents} == {"total"}
    assert {n.node_id for n in impact.transitive_dependents} == {"total", "checkout", "route", "test"}
    assert {n.node_id for n in impact.related_tests} == {"test"}
    assert {n.node_id for n in impact.entrypoints} == {"route"}


@pytest.mark.asyncio
async def test_file_snapshot_persists(tmp_path):
    from core.code_graph_sync import FileFingerprint
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    snap = {"a.py": FileFingerprint("a.py", "abc", 12, 34)}
    await store.save_file_snapshot("demo", snap)
    loaded = await store.load_file_snapshot("demo")
    assert loaded == snap


@pytest.mark.asyncio
async def test_impact_excludes_structural_edges(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    project_id = "demo"
    nodes = (
        CodeNode(project_id, "project", CodeNodeKind.PROJECT, "demo"),
        CodeNode(project_id, "file", CodeNodeKind.FILE, "a.py"),
        CodeNode(project_id, "module", CodeNodeKind.MODULE, "a"),
        CodeNode(project_id, "target", CodeNodeKind.FUNCTION, "target"),
        CodeNode(project_id, "caller", CodeNodeKind.FUNCTION, "caller"),
    )
    edges = (
        CodeEdge(project_id, "project", "file", CodeEdgeKind.CONTAINS),
        CodeEdge(project_id, "file", "module", CodeEdgeKind.DEFINES),
        CodeEdge(project_id, "module", "target", CodeEdgeKind.DEFINES),
        CodeEdge(project_id, "caller", "target", CodeEdgeKind.CALLS),
    )
    await store.replace_project_graph(project_id, nodes, edges)
    impact = await store.impact(project_id, "target", max_depth=5)
    assert {n.node_id for n in impact.direct_dependents} == {"caller"}
    assert {n.node_id for n in impact.transitive_dependents} == {"caller"}


@pytest.mark.asyncio
async def test_semantic_neighbors_exclude_structural_edges(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    pid = "demo"
    nodes = (
        CodeNode(pid, "module", CodeNodeKind.MODULE, "app"),
        CodeNode(pid, "target", CodeNodeKind.FUNCTION, "target"),
        CodeNode(pid, "caller", CodeNodeKind.FUNCTION, "caller"),
        CodeNode(pid, "callee", CodeNodeKind.FUNCTION, "callee"),
    )
    edges = (
        CodeEdge(pid, "module", "target", CodeEdgeKind.DEFINES),
        CodeEdge(pid, "caller", "target", CodeEdgeKind.CALLS),
        CodeEdge(pid, "target", "callee", CodeEdgeKind.CALLS),
    )
    await store.replace_project_graph(pid, nodes, edges)
    deps = await store.semantic_dependencies(pid, "target")
    users = await store.semantic_dependents(pid, "target")
    assert {n.node_id for n in deps} == {"callee"}
    assert {n.node_id for n in users} == {"caller"}


# ── FTS5 & kind-priority tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_nodes_prefix_match(tmp_path):
    """FTS5 prefix match: searching 'appl' should find 'apply_discount'."""
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    pid = "demo"
    nodes = (
        CodeNode(pid, "fn1", CodeNodeKind.FUNCTION, "apply_discount",
                 qualified_name="billing.apply_discount"),
        CodeNode(pid, "fn2", CodeNodeKind.FUNCTION, "calculate_total",
                 qualified_name="billing.calculate_total"),
        CodeNode(pid, "f1",  CodeNodeKind.FILE,     "billing.py"),
    )
    await store.replace_project_graph(pid, nodes, ())

    results = await store.find_nodes(pid, "appl")
    names = {n.name for n in results}
    assert "apply_discount" in names, f"prefix 'appl' should match 'apply_discount'; got {names}"


@pytest.mark.asyncio
async def test_find_nodes_kind_priority(tmp_path):
    """Kind priority: FUNCTION should appear before FILE/DIRECTORY in results."""
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    pid = "demo"
    nodes = (
        CodeNode(pid, "dir1", CodeNodeKind.DIRECTORY, "checkout"),
        CodeNode(pid, "fil1", CodeNodeKind.FILE,      "checkout.py"),
        CodeNode(pid, "fn1",  CodeNodeKind.FUNCTION,  "checkout",
                 qualified_name="shop.checkout"),
        CodeNode(pid, "cls1", CodeNodeKind.CLASS,     "CheckoutService"),
    )
    await store.replace_project_graph(pid, nodes, ())

    results = await store.find_nodes(pid, "checkout")
    kinds = [n.kind.value for n in results]
    # FUNCTION and CLASS must appear before FILE and DIRECTORY.
    semantic_kinds = {"function", "class"}
    structural_kinds = {"file", "directory"}
    last_semantic = max((i for i, k in enumerate(kinds) if k in semantic_kinds), default=-1)
    first_structural = min((i for i, k in enumerate(kinds) if k in structural_kinds), default=len(kinds))
    assert last_semantic < first_structural, (
        f"semantic kinds should precede structural kinds; order was {kinds}"
    )


@pytest.mark.asyncio
async def test_graph_status_orphan_count(tmp_path):
    """get_graph_status should report orphan_node_paths for stale nodes."""
    from core.code_graph_sync import FileFingerprint
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    pid = "demo"

    # Insert a node that references a path not in the file snapshot.
    nodes = (
        CodeNode(pid, "fn1", CodeNodeKind.FUNCTION, "orphaned_fn", path="ghost.py"),
        CodeNode(pid, "fn2", CodeNodeKind.FUNCTION, "real_fn",     path="real.py"),
    )
    await store.replace_project_graph(pid, nodes, ())
    # Only real.py in the snapshot — ghost.py is an orphan.
    snap = {"real.py": FileFingerprint("real.py", "abc123", 100, 1000)}
    await store.save_file_snapshot(pid, snap)

    status = await store.get_graph_status(pid)
    assert status["orphan_node_paths"] == 1, (
        f"Expected 1 orphan path (ghost.py); got {status['orphan_node_paths']}"
    )
