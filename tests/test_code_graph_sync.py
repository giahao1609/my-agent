import pytest

from core.code_graph_sync import diff_snapshots, snapshot_code_files, sync_code_graph
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore


def test_snapshot_diff_detects_current_state(tmp_path):
    a_file = tmp_path / "a.py"
    a_file.write_text("x = 1\n", encoding="utf-8")
    old = snapshot_code_files(tmp_path)
    a_file.write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 1\n", encoding="utf-8")
    new = snapshot_code_files(tmp_path)
    changes = diff_snapshots(old, new)
    assert changes.modified == ("a.py",)
    assert changes.added == ("b.py",)
    assert changes.deleted == ()


def test_snapshot_diff_detects_deleted_file(tmp_path):
    f = tmp_path / "old.py"
    f.write_text("x = 1\n", encoding="utf-8")
    old = snapshot_code_files(tmp_path)
    f.unlink()
    new = snapshot_code_files(tmp_path)
    changes = diff_snapshots(old, new)
    assert changes.deleted == ("old.py",)


@pytest.mark.asyncio
async def test_sync_code_graph_persists_current_graph(tmp_path):
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    changes = await sync_code_graph("demo", tmp_path, store)
    assert changes.added == ("a.py",)
    nodes = await store.find_nodes("demo", "helper")
    assert any(n.name == "helper" for n in nodes)
    snap = await store.load_file_snapshot("demo")
    assert "a.py" in snap
    status = await store.get_graph_status("demo")
    assert status["status"] == "ready"


@pytest.mark.asyncio
async def test_sync_failure_marks_graph_failed(tmp_path):
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    missing = tmp_path / "missing"
    with pytest.raises(ValueError):
        await sync_code_graph("demo", missing, store)
    status = await store.get_graph_status("demo")
    assert status["status"] == "failed"


@pytest.mark.asyncio
async def test_incremental_sync_updates_only_changed_state(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def helper():\n    return 1\n", encoding="utf-8")
    b.write_text("from a import helper\n\ndef run():\n    return helper()\n", encoding="utf-8")
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    await sync_code_graph("demo", tmp_path, store)
    a.write_text("def helper():\n    return 2\n\ndef extra():\n    return 3\n", encoding="utf-8")
    changes = await sync_code_graph("demo", tmp_path, store)
    assert changes.modified == ("a.py",)
    assert any(n.qualified_name == "a.extra" for n in await store.find_nodes("demo", "extra"))
    assert any(n.qualified_name == "b.run" for n in await store.find_nodes("demo", "run"))


@pytest.mark.asyncio
async def test_incremental_sync_updates_ast_hash_for_same_symbol(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def calc():\n    return 1\n", encoding="utf-8")
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    await sync_code_graph("demo", tmp_path, store)
    before = next(n for n in await store.find_nodes("demo", "calc") if n.qualified_name == "a.calc")
    f.write_text("def calc():\n    return 2\n", encoding="utf-8")
    await sync_code_graph("demo", tmp_path, store)
    after = next(n for n in await store.find_nodes("demo", "calc") if n.qualified_name == "a.calc")
    assert before.node_id == after.node_id
    assert before.metadata["ast_hash"] != after.metadata["ast_hash"]


@pytest.mark.asyncio
async def test_sync_marks_stale_when_files_change_during_scan(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("def calc():\n    return 1\n", encoding="utf-8")
    store = SQLiteCodeGraphStore(tmp_path / "graph.db")
    await store.initialize()
    await sync_code_graph("demo", tmp_path, store)
    before = next(n for n in await store.find_nodes("demo", "calc") if n.qualified_name == "a.calc")
    f.write_text("def calc():\n    return 2\n", encoding="utf-8")
    calls = 0
    def racing_snapshot(root):
        nonlocal calls
        calls += 1
        snap = snapshot_code_files(root)
        if calls == 1:
            f.write_text("def calc():\n    return 3\n", encoding="utf-8")
        return snap
    monkeypatch.setattr("core.code_graph_sync.snapshot_code_files", racing_snapshot)
    changes = await sync_code_graph("demo", tmp_path, store)
    status = await store.get_graph_status("demo")
    after = next(n for n in await store.find_nodes("demo", "calc") if n.qualified_name == "a.calc")
    assert status["status"] == "stale"
    assert changes.modified == ("a.py",)
    assert after.metadata["ast_hash"] == before.metadata["ast_hash"]


@pytest.mark.asyncio
async def test_incremental_laravel_route_keeps_persisted_controller_target(tmp_path):
    (tmp_path / "composer.json").write_text(
        '{"require":{"laravel/framework":"^13.0"}}',
        encoding="utf-8",
    )
    controller = tmp_path / "app" / "Http" / "Controllers" / "AuthController.php"
    controller.parent.mkdir(parents=True)
    controller.write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "class AuthController {\n"
        "    public function login() {}\n"
        "}\n",
        encoding="utf-8",
    )
    route = tmp_path / "routes" / "api" / "auth.php"
    route.parent.mkdir(parents=True)
    route.write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "Route::post('/login', [AuthController::class, 'login']);\n",
        encoding="utf-8",
    )

    db = tmp_path / "graph.db"
    store = SQLiteCodeGraphStore(db)
    await store.initialize()
    await sync_code_graph("demo", tmp_path, store)

    route.write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "Route::post('/sign-in', [AuthController::class, 'login']);\n",
        encoding="utf-8",
    )
    changes = await sync_code_graph("demo", tmp_path, store)
    assert changes.modified == ("routes/api/auth.php",)

    import sqlite3
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT r.name, m.qualified_name, e.metadata_json
            FROM code_edges e
            JOIN code_nodes r
              ON r.project_id = e.project_id AND r.node_id = e.source_id
            JOIN code_nodes m
              ON m.project_id = e.project_id AND m.node_id = e.target_id
            WHERE e.project_id = ?
              AND e.kind = ?
              AND r.name = ?
            """,
            ("demo", "routes_to", "POST /sign-in"),
        ).fetchone()

    assert row is not None
    assert row[1] == "App\\Http\\Controllers\\AuthController::login"
    assert '"resolved": true' in row[2]
