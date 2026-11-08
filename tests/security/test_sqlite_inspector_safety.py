#!/usr/bin/env python3
"""Comprehensive Negative Test Suite for SQLite Inspector Safety (10 Mandatory Tests).

Verifies:
1. Test DB is a symlink to production DB -> REFUSED without deleting target.
2. Test DB already exists -> REFUSED overwrite/deletion.
3. Test DB is currently in use by another process -> DETECTED and REFUSED.
4. MYAGENT_DB_PATH points directly to production DB -> REFUSED.
5. MYAGENT_DB_PATH points to external repository DB -> REFUSED.
6. SQLite backup fails midway -> SAFE CLEANUP without data loss.
7. Inspector port is already in use -> REFUSED without killing process.
8. Two concurrent sessions -> FULLY ISOLATED tempdirs, tokens, and databases.
9. Snapshot access permissions -> STRICT 0700 for directory, 0600 for file.
10. Snapshot is actually used by MCP subprocess -> ZERO MUTATION on production DB.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import sys
import tempfile

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest

from core.inspector_db_safety import (
    BackupIntegrityError,
    DatabaseInUseError,
    ExternalRepositoryRefusalError,
    FileAlreadyExistsRefusalError,
    InspectorSafetyError,
    ProductionDatabaseRefusalError,
    check_database_in_use,
    check_port_availability,
    create_isolated_session_database,
    is_same_file,
    validate_not_production_or_foreign_db,
)
from core.project import ProjectRecord
from persistence.sqlite_project_store import SQLiteProjectStore


@pytest.fixture
def mock_system_env(tmp_path: Path):
    """Creates a mock multi-repo environment with mock production and foreign databases."""
    projects_root = tmp_path / "Project"
    projects_root.mkdir()

    repo_myagent = projects_root / "my-agent"
    repo_myagent.mkdir()
    (repo_myagent / "data").mkdir()
    mock_prod_db = repo_myagent / "data" / "my_agent.db"

    # Initialize mock production DB with valuable data
    with sqlite3.connect(mock_prod_db) as conn:
        conn.execute("CREATE TABLE critical_data (id INTEGER PRIMARY KEY, secret TEXT);")
        conn.execute("INSERT INTO critical_data (id, secret) VALUES (1, 'PROD_TOP_SECRET');")
        conn.commit()

    repo_teachora = projects_root / "teachora-ai"
    repo_teachora.mkdir()
    (repo_teachora / "data").mkdir()
    foreign_db = repo_teachora / "data" / "teachora.db"
    with sqlite3.connect(foreign_db) as conn:
        conn.execute("CREATE TABLE foreign_data (id INTEGER PRIMARY KEY, note TEXT);")
        conn.execute("INSERT INTO foreign_data (id, note) VALUES (1, 'FOREIGN_PROPRIETARY');")
        conn.commit()

    return {
        "projects_root": projects_root,
        "repo_myagent": repo_myagent,
        "mock_prod_db": mock_prod_db,
        "repo_teachora": repo_teachora,
        "foreign_db": foreign_db,
    }


# ==============================================================================
# TEST 1: Test DB is a symlink to production DB (REFUSED, NO DELETION)
# ==============================================================================
def test_negative_1_symlink_to_production_db_rejected(mock_system_env, tmp_path):
    repo_myagent = mock_system_env["repo_myagent"]
    mock_prod_db = mock_system_env["mock_prod_db"]

    # Create an external directory with a symlink pointing to production DB
    attacker_dir = tmp_path / "attacker_dir"
    attacker_dir.mkdir()
    malicious_symlink = attacker_dir / "innocent_looking_test.db"
    malicious_symlink.symlink_to(mock_prod_db)

    assert malicious_symlink.is_symlink()
    assert is_same_file(malicious_symlink, mock_prod_db)

    # Validation MUST raise ProductionDatabaseRefusalError
    with pytest.raises(ProductionDatabaseRefusalError) as exc_info:
        validate_not_production_or_foreign_db(
            candidate_db=malicious_symlink,
            repo_root=repo_myagent,
            custom_prod_db=mock_prod_db,
        )
    assert "symlink" in str(exc_info.value).lower()

    # Core Invariant: Production DB must NOT be unlinked or modified
    assert mock_prod_db.exists()
    with sqlite3.connect(mock_prod_db) as conn:
        row = conn.execute("SELECT secret FROM critical_data WHERE id = 1;").fetchone()
        assert row[0] == "PROD_TOP_SECRET"


# ==============================================================================
# TEST 2: Test DB already exists (OVERWRITE FORBIDDEN)
# ==============================================================================
def test_negative_2_existing_target_file_overwrite_forbidden(mock_system_env, tmp_path):
    mock_prod_db = mock_system_env["mock_prod_db"]

    # Pre-create session base directory and an existing target database
    session_base = tmp_path / "pre_existing_session_base"
    session_base.mkdir()

    # Create a specific directory where target file already exists
    target_dir = session_base / "fixed_session"
    target_dir.mkdir()
    existing_file = target_dir / "inspector_isolated.db"
    existing_file.write_text("IMPORTANT_EXISTING_DB_CONTENT", encoding="utf-8")

    # Attempting to snapshot into directory where file exists must raise FileAlreadyExistsRefusalError
    with pytest.raises(FileAlreadyExistsRefusalError):
        create_isolated_session_database(
            src_prod_db=mock_prod_db,
            explicit_session_dir=target_dir,
        )

    # Invariant: existing file is NEVER overwritten or unlinked
    assert existing_file.exists()
    assert existing_file.read_text(encoding="utf-8") == "IMPORTANT_EXISTING_DB_CONTENT"


# ==============================================================================
# TEST 3: Test DB is currently in use by another process (LOCK DETECTED)
# ==============================================================================
def test_negative_3_database_in_use_detected(tmp_path):
    busy_db = tmp_path / "busy_test.db"
    with sqlite3.connect(busy_db) as conn:
        conn.execute("CREATE TABLE test (id INT);")
        conn.commit()

    # Hold an exclusive write lock in process A
    lock_conn = sqlite3.connect(busy_db)
    lock_conn.execute("BEGIN EXCLUSIVE;")

    try:
        # check_database_in_use should detect that database is locked
        is_busy = check_database_in_use(busy_db)
        assert is_busy is True
    finally:
        lock_conn.rollback()
        lock_conn.close()

    # After releasing lock, check_database_in_use should return False
    assert check_database_in_use(busy_db) is False


# ==============================================================================
# TEST 4: MYAGENT_DB_PATH points directly to production DB (REFUSED)
# ==============================================================================
def test_negative_4_myagent_db_path_points_to_production_db(mock_system_env):
    repo_myagent = mock_system_env["repo_myagent"]
    mock_prod_db = mock_system_env["mock_prod_db"]

    with pytest.raises(ProductionDatabaseRefusalError) as exc_info:
        validate_not_production_or_foreign_db(
            candidate_db=mock_prod_db,
            repo_root=repo_myagent,
            custom_prod_db=mock_prod_db,
        )
    assert "matches the production database path" in str(exc_info.value)


# ==============================================================================
# TEST 5: MYAGENT_DB_PATH points to external repository DB (REFUSED)
# ==============================================================================
def test_negative_5_myagent_db_path_points_to_foreign_repo_db(mock_system_env):
    repo_myagent = mock_system_env["repo_myagent"]
    foreign_db = mock_system_env["foreign_db"]

    with pytest.raises(ExternalRepositoryRefusalError) as exc_info:
        validate_not_production_or_foreign_db(
            candidate_db=foreign_db,
            repo_root=repo_myagent,
        )
    assert "teachora-ai" in str(exc_info.value)


# ==============================================================================
# TEST 6: SQLite backup fails midway (SAFE CLEANUP WITHOUT DATA LOSS)
# ==============================================================================
def test_negative_6_sqlite_backup_midway_failure_safe_cleanup(mock_system_env, tmp_path):
    mock_prod_db = mock_system_env["mock_prod_db"]
    base_dir = tmp_path / "sessions_test"
    base_dir.mkdir()

    # Injected failure mid-backup
    with pytest.raises(BackupIntegrityError):
        create_isolated_session_database(
            src_prod_db=mock_prod_db,
            session_base_dir=base_dir,
            fail_on_step=1,  # Injected failure
        )

    # Invariant 1: Source production database is completely intact
    assert mock_prod_db.exists()
    with sqlite3.connect(mock_prod_db) as conn:
        row = conn.execute("SELECT secret FROM critical_data WHERE id = 1;").fetchone()
        assert row[0] == "PROD_TOP_SECRET"

    # Invariant 2: No corrupted partial directory left behind in session base
    remaining_dirs = list(base_dir.glob("myagent_inspector_session_*"))
    assert len(remaining_dirs) == 0


# ==============================================================================
# TEST 7: Inspector port is already in use (REFUSED WITHOUT KILLING)
# ==============================================================================
def test_negative_7_inspector_port_in_use_blocks_launch():
    # Bind a temporary test socket on loopback
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    bound_port = sock.getsockname()[1]

    try:
        available, msg = check_port_availability(bound_port)
        assert available is False
        assert "IN USE" in msg
    finally:
        sock.close()

    # After closing socket, port becomes available
    available_after, _ = check_port_availability(bound_port)
    assert available_after is True


# ==============================================================================
# TEST 8: Two concurrent sessions (FULLY ISOLATED SESSIONS & DATABASES)
# ==============================================================================
def test_negative_8_two_concurrent_sessions_isolated(mock_system_env, tmp_path):
    mock_prod_db = mock_system_env["mock_prod_db"]
    base_dir = tmp_path / "concurrent_sessions"
    base_dir.mkdir()

    # Launch Session 1
    dir1, db1, token1 = create_isolated_session_database(mock_prod_db, session_base_dir=base_dir)

    # Launch Session 2
    dir2, db2, token2 = create_isolated_session_database(mock_prod_db, session_base_dir=base_dir)

    assert dir1 != dir2
    assert db1 != db2
    assert token1 != token2
    assert len(token1) == 64
    assert len(token2) == 64

    # Mutate DB 1: write session 1 data
    with sqlite3.connect(db1) as conn1:
        conn1.execute("INSERT INTO critical_data (id, secret) VALUES (101, 'SESSION_1_DATA');")
        conn1.commit()

    # Verify DB 2 does NOT see session 1 data
    with sqlite3.connect(db2) as conn2:
        rows = conn2.execute("SELECT secret FROM critical_data WHERE id = 101;").fetchall()
        assert len(rows) == 0

    # Verify Mock Production DB does NOT see session 1 data
    with sqlite3.connect(mock_prod_db) as conn_prod:
        rows = conn_prod.execute("SELECT secret FROM critical_data WHERE id = 101;").fetchall()
        assert len(rows) == 0


# ==============================================================================
# TEST 9: Snapshot permissions restricted (0700 DIR, 0600 FILE)
# ==============================================================================
def test_negative_9_snapshot_permissions_restricted(mock_system_env, tmp_path):
    mock_prod_db = mock_system_env["mock_prod_db"]
    session_dir, db_path, token = create_isolated_session_database(mock_prod_db, session_base_dir=tmp_path)

    # Check session directory mode: 0700 (drwx------)
    dir_mode = stat.S_IMODE(os.stat(session_dir).st_mode)
    assert dir_mode == 0o700, f"Expected 0700 for session dir, got {oct(dir_mode)}"

    # Check database snapshot mode: 0600 (-rw-------)
    db_mode = stat.S_IMODE(os.stat(db_path).st_mode)
    assert db_mode == 0o600, f"Expected 0600 for snapshot db, got {oct(db_mode)}"

    # Check token file mode: 0600 (-rw-------)
    token_file = session_dir / "session_token.txt"
    token_mode = stat.S_IMODE(os.stat(token_file).st_mode)
    assert token_mode == 0o600, f"Expected 0600 for token file, got {oct(token_mode)}"
    assert token_file.read_text(encoding="utf-8") == token


# ==============================================================================
# TEST 10: Snapshot is actually used by MCP subprocess (ZERO MUTATION ON PROD)
# ==============================================================================
@pytest.mark.asyncio
async def test_negative_10_snapshot_used_by_mcp_subprocess_zero_prod_mutation(tmp_path):
    workspace_dir = tmp_path / "test_workspace"
    workspace_dir.mkdir()

    # Pre-populate workspace
    (workspace_dir / "README.md").write_text("# Test Workspace\n", encoding="utf-8")

    # Create mock production DB with project record
    prod_db = tmp_path / "mock_prod_mcp.db"
    store = SQLiteProjectStore(prod_db)
    await store.initialize()
    await store.create(ProjectRecord(
        project_id="test-proj-prod",
        name="Test Proj Prod",
        workspace_path=str(workspace_dir),
    ))

    # Take snapshot into isolated session directory
    session_dir, isolated_db, token = create_isolated_session_database(prod_db, session_base_dir=tmp_path)

    # Record row count of tasks in production DB before subprocess starts
    with sqlite3.connect(prod_db) as conn:
        tasks_before = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='tasks';").fetchone()[0]

    # Spawn MCP server with MYAGENT_DB_PATH pointed strictly to isolated_db
    python_bin = sys.executable
    repo_root = str(Path(__file__).resolve().parents[2])

    env = {
        **os.environ,
        "MYAGENT_WORKSPACE": str(workspace_dir),
        "MYAGENT_DB_PATH": str(isolated_db),
        "PYTHONPATH": repo_root,
    }

    params = StdioServerParameters(
        command=python_bin,
        args=["-m", "my_agent_mcp.server"],
        env=env,
        cwd=str(workspace_dir),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Create task inside subprocess
            create_res = await session.call_tool("create_task", {
                "project_id": "test-proj-prod",
                "objective": "Task in isolated snapshot",
            })
            res_data = json.loads(create_res.content[0].text)
            assert res_data.get("status") == "created"
            task_id = res_data.get("task", {}).get("task_id")
            assert task_id is not None

    # Verify task EXISTS in isolated_db
    with sqlite3.connect(isolated_db) as conn_iso:
        iso_task = conn_iso.execute("SELECT objective FROM tasks WHERE task_id = ?;", (task_id,)).fetchone()
        assert iso_task is not None
        assert iso_task[0] == "Task in isolated snapshot"

    # Verify task DOES NOT EXIST in production DB (ZERO PROD MUTATION)
    with sqlite3.connect(prod_db) as conn_prod:
        prod_tables = [r[0] for r in conn_prod.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "tasks" in prod_tables:
            prod_task = conn_prod.execute("SELECT count(*) FROM tasks WHERE task_id = ?;", (task_id,)).fetchone()[0]
            assert prod_task == 0
