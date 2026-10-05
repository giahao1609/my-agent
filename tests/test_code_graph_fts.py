"""Focused tests for the FTS5 full-text search layer in SQLiteCodeGraphStore."""
from __future__ import annotations

import pytest

from core.code_graph import CodeNode, CodeNodeKind
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore


# ── _build_fts_query unit tests ───────────────────────────────────────────────

def test_build_fts_query_simple():
    q = SQLiteCodeGraphStore._build_fts_query("handle")
    assert '"handle"*' in q


def test_build_fts_query_underscore_name():
    """apply_discount is a single α-numeric token — FTS5 gets a single prefix term."""
    q = SQLiteCodeGraphStore._build_fts_query("apply_discount")
    # The regex [^\w\s] keeps underscores — token is 'apply_discount' whole.
    assert '"apply_discount"*' in q


def test_build_fts_query_dotted():
    """Dots are stripped, so plan_service.create_plan → two terms."""
    q = SQLiteCodeGraphStore._build_fts_query("plan_service.create_plan")
    # dot is a non-\w char — splits into 'plan_service' and 'create_plan'
    assert '"plan_service"*' in q
    assert '"create_plan"*' in q


def test_build_fts_query_empty():
    q = SQLiteCodeGraphStore._build_fts_query("")
    assert q == '""*'


# ── FTS5 integration tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_prefix_match_on_underscore_name(tmp_path):
    """Prefix 'appl' should match 'apply_discount' via FTS5 token 'apply'."""
    store = SQLiteCodeGraphStore(tmp_path / "g.db")
    await store.initialize()
    pid = "p"
    await store.replace_project_graph(pid, [
        CodeNode(pid, "n1", CodeNodeKind.FUNCTION, "apply_discount",
                 qualified_name="billing.apply_discount"),
        CodeNode(pid, "n2", CodeNodeKind.FUNCTION, "approve_order",
                 qualified_name="order.approve_order"),
        CodeNode(pid, "n3", CodeNodeKind.FUNCTION, "calculate_total",
                 qualified_name="billing.calculate_total"),
    ], [])
    results = await store.find_nodes(pid, "appl")
    names = {n.name for n in results}
    assert "apply_discount" in names
    assert "approve_order" not in names, (
        "'appl*' should not match 'approve_order' — token is 'approve', not 'appl'"
    )
    assert "calculate_total" not in names


@pytest.mark.asyncio
async def test_fts_qualified_name_token_search(tmp_path):
    """Searching a module token like 'billing' should match qualified names."""
    store = SQLiteCodeGraphStore(tmp_path / "g.db")
    await store.initialize()
    pid = "p"
    await store.replace_project_graph(pid, [
        CodeNode(pid, "n1", CodeNodeKind.FUNCTION, "apply_discount",
                 qualified_name="billing.apply_discount"),
        CodeNode(pid, "n2", CodeNodeKind.FUNCTION, "send_email",
                 qualified_name="notifications.send_email"),
    ], [])
    results = await store.find_nodes(pid, "billing")
    names = {n.name for n in results}
    assert "apply_discount" in names
    assert "send_email" not in names


@pytest.mark.asyncio
async def test_fts_index_stays_in_sync_after_replace(tmp_path):
    """FTS triggers must keep index accurate after replace_project_graph."""
    store = SQLiteCodeGraphStore(tmp_path / "g.db")
    await store.initialize()
    pid = "p"

    await store.replace_project_graph(pid, [
        CodeNode(pid, "old", CodeNodeKind.FUNCTION, "old_function"),
    ], [])
    r1 = await store.find_nodes(pid, "old_func")
    assert any(n.name == "old_function" for n in r1)

    # Replace entirely — old_function gone, new_function appears
    await store.replace_project_graph(pid, [
        CodeNode(pid, "new", CodeNodeKind.FUNCTION, "new_function"),
    ], [])

    r2 = await store.find_nodes(pid, "new_func")
    assert any(n.name == "new_function" for n in r2)

    r3 = await store.find_nodes(pid, "old_func")
    assert not any(n.name == "old_function" for n in r3), (
        "old_function should be removed from FTS index after graph replace"
    )


@pytest.mark.asyncio
async def test_fts_kind_priority_semantic_before_structural(tmp_path):
    """Semantic nodes (function, class) must rank before structural (file, dir)."""
    store = SQLiteCodeGraphStore(tmp_path / "g.db")
    await store.initialize()
    pid = "p"
    await store.replace_project_graph(pid, [
        CodeNode(pid, "d1",  CodeNodeKind.DIRECTORY, "auth"),
        CodeNode(pid, "f1",  CodeNodeKind.FILE,      "auth.py"),
        CodeNode(pid, "fn1", CodeNodeKind.FUNCTION,  "auth_user"),
        CodeNode(pid, "c1",  CodeNodeKind.CLASS,     "AuthService"),
    ], [])

    results = await store.find_nodes(pid, "auth")
    kinds = [n.kind.value for n in results]

    semantic = {"function", "class"}
    structural = {"file", "directory"}
    last_sem = max((i for i, k in enumerate(kinds) if k in semantic), default=-1)
    first_struct = min((i for i, k in enumerate(kinds) if k in structural), default=len(kinds))
    assert last_sem < first_struct, f"Semantic before structural expected; got: {kinds}"


@pytest.mark.asyncio
async def test_find_nodes_normal_query_returns_results(tmp_path):
    """A normal query must return matching nodes regardless of FTS availability."""
    store = SQLiteCodeGraphStore(tmp_path / "g.db")
    await store.initialize()
    pid = "p"
    await store.replace_project_graph(pid, [
        CodeNode(pid, "n1", CodeNodeKind.FUNCTION, "process_payment"),
    ], [])
    results = await store.find_nodes(pid, "process")
    assert any(n.name == "process_payment" for n in results)
