from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Generator
import pytest

from core.release_installer import ReleaseManager, ReleaseRecord, _compute_file_sha256
from core.runtime_environment import (
    ProductionDatabaseProtectionError,
    ReleaseVerificationError,
    RuntimeConfig,
    RuntimeEnvironment,
    RuntimeIsolationError,
    create_safe_sqlite_backup,
    detect_accidental_development_imports,
    get_permanent_stable_data_dir,
    get_protected_production_db,
    is_same_file,
    resolve_runtime_environment,
)
from core.security_authorization import SecurityAuthorizationError


@pytest.fixture
def mock_release_sandbox(tmp_path: Path) -> dict[str, Path]:
    """Sets up an isolated directory hierarchy for release and runtime testing."""
    releases_root = tmp_path / "releases"
    stable_link = tmp_path / "stable"
    venvs_root = tmp_path / "venvs"
    data_dir = tmp_path / "data"
    dev_root = tmp_path / "dev_repo"

    releases_root.mkdir()
    venvs_root.mkdir()
    data_dir.mkdir()
    dev_root.mkdir()

    # Create dummy core and data files in dev_root
    (dev_root / "core").mkdir()
    (dev_root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (dev_root / "core" / "sample.py").write_text("VERSION = 'dev_1'\n", encoding="utf-8")
    (dev_root / "data").mkdir()
    (dev_root / "data" / "my_agent.db").write_text("PROD_MOCK_DATA", encoding="utf-8")
    (dev_root / "data" / "my_agent_dev.db").write_text("DEV_MOCK_DATA", encoding="utf-8")

    return {
        "root": tmp_path,
        "releases_root": releases_root,
        "stable_link": stable_link,
        "venvs_root": venvs_root,
        "data_dir": data_dir,
        "dev_root": dev_root,
    }


def test_scenario_1_stable_source_remains_unchanged_after_development_changes(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-1: Modifying development files does not mutate installed pinned release source."""
    releases_root = mock_release_sandbox["releases_root"]
    dev_root = mock_release_sandbox["dev_root"]
    commit_sha = "a1b2c3d4e5f67890123456789abcdef012345678"

    # Simulate installed release directory
    rel_dir = releases_root / commit_sha
    rel_dir.mkdir()
    (rel_dir / "core").mkdir()
    (rel_dir / "core" / "sample.py").write_text("VERSION = 'pinned_release_1'\n", encoding="utf-8")
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({"commit_sha": commit_sha, "short_sha": "a1b2c3d", "source_ref": "v1.0.0"}),
        encoding="utf-8",
    )

    # Now mutate the development repo
    (dev_root / "core" / "sample.py").write_text("VERSION = 'mutated_by_developer_work'\n", encoding="utf-8")
    (dev_root / "core" / "new_uncommitted_file.py").write_text("DIRTY_STATE = True\n", encoding="utf-8")

    # Assert pinned release is 100% untouched
    pinned_content = (rel_dir / "core" / "sample.py").read_text(encoding="utf-8")
    assert pinned_content == "VERSION = 'pinned_release_1'\n"
    assert not (rel_dir / "core" / "new_uncommitted_file.py").exists()


def test_scenario_2_stable_imports_resolve_only_from_pinned_release(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-2: Detects accidental imports that originate outside the pinned release root."""
    rel_dir = mock_release_sandbox["releases_root"] / "release_v1"
    rel_dir.mkdir()

    violations = detect_accidental_development_imports(allowed_root=rel_dir, check_modules=("core",))
    assert len(violations) > 0
    assert any("core" in v for v in violations)


def test_scenario_3_stable_and_development_database_paths_independent(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-3: Stable and Development default databases resolve to completely distinct files."""
    dev_root = mock_release_sandbox["dev_root"]

    cfg_stable = resolve_runtime_environment(
        env_override="stable",
        source_root_override=dev_root,
    )
    cfg_dev = resolve_runtime_environment(
        env_override="development",
        source_root_override=dev_root,
    )

    assert cfg_stable.environment == RuntimeEnvironment.STABLE
    assert cfg_dev.environment == RuntimeEnvironment.DEVELOPMENT
    assert cfg_stable.database_path.name == "my_agent.db"
    assert cfg_dev.database_path.name == "my_agent_dev.db"
    assert not is_same_file(cfg_stable.database_path, cfg_dev.database_path)


def test_scenario_4_development_cannot_bind_or_write_to_stable_database(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-4: Development environment attempting to bind to production DB fails closed."""
    dev_root = mock_release_sandbox["dev_root"]
    prod_db = dev_root / "data" / "my_agent.db"

    # Direct path match in development raises ProductionDatabaseProtectionError
    with pytest.raises(ProductionDatabaseProtectionError) as exc_info:
        resolve_runtime_environment(
            env_override="development",
            db_override=str(prod_db),
            source_root_override=dev_root,
        )
    assert "strictly prohibited from binding to" in str(exc_info.value)
    assert "production database" in str(exc_info.value)

    # Symlink pointing to production DB must also fail closed
    symlink_db = dev_root / "data" / "sneaky_symlink.db"
    try:
        os.symlink(prod_db, symlink_db)
        with pytest.raises(ProductionDatabaseProtectionError):
            resolve_runtime_environment(
                env_override="development",
                db_override=str(symlink_db),
                source_root_override=dev_root,
            )
    finally:
        if symlink_db.exists() or os.path.islink(symlink_db):
            symlink_db.unlink()


def test_scenario_5_invalid_runtime_configuration_fails_closed(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-5: Invalid runtime environment string or conflicting configuration fails closed."""
    dev_root = mock_release_sandbox["dev_root"]

    with pytest.raises(RuntimeIsolationError) as exc_info:
        resolve_runtime_environment(
            env_override="invalid_env_name_123",
            source_root_override=dev_root,
        )
    assert "Invalid MYAGENT_RUNTIME_ENV" in str(exc_info.value)

    # Stable attempting to use development DB fails closed
    dev_db = dev_root / "data" / "my_agent_dev.db"
    with pytest.raises(RuntimeIsolationError) as exc_info2:
        resolve_runtime_environment(
            env_override="stable",
            db_override=str(dev_db),
            source_root_override=dev_root,
        )
    assert "cannot bind to the development database" in str(exc_info2.value)


def test_scenario_6_release_identity_reflects_actual_pinned_commit(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-6: Release metadata correctly populates release_commit on startup."""
    rel_dir = mock_release_sandbox["releases_root"] / "rel_pinned"
    rel_dir.mkdir()
    expected_commit = "91120a091fc174d87b720edde4b4c2daafd3e908"
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({"commit_sha": expected_commit, "source_ref": "feat/p1a"}),
        encoding="utf-8",
    )

    cfg = resolve_runtime_environment(
        env_override="stable",
        source_root_override=rel_dir,
    )
    assert cfg.release_commit == expected_commit


def test_scenario_7_rollback_preserves_persistent_data(mock_release_sandbox: dict[str, Path]) -> None:
    """AC-7: Rollback switches the Stable pointer without mutating persistent data."""
    releases_root = mock_release_sandbox["releases_root"]
    stable_link = mock_release_sandbox["stable_link"]
    venvs_root = mock_release_sandbox["venvs_root"]
    data_dir = mock_release_sandbox["data_dir"]

    manager = ReleaseManager(
        releases_root=releases_root,
        stable_link=stable_link,
        venvs_root=venvs_root,
        data_dir=data_dir,
    )

    # Setup 2 releases: v1 and v2
    rel1 = releases_root / "1111111111111111111111111111111111111111"
    rel1.mkdir()
    (rel1 / "core").mkdir()
    (rel1 / "my_agent_mcp").mkdir()
    (rel1 / "persistence").mkdir()
    f1 = rel1 / "core" / "sample.py"
    f1.write_text("V=1\n", encoding="utf-8")
    (rel1 / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": "1111111111111111111111111111111111111111",
            "short_sha": "1111111",
            "source_ref": "v1.0",
            "installed_at": "2026-11-10T10:00:00Z",
            "file_hashes": {"core/sample.py": _compute_file_sha256(f1)},
        }),
        encoding="utf-8",
    )

    rel2 = releases_root / "2222222222222222222222222222222222222222"
    rel2.mkdir()
    (rel2 / "core").mkdir()
    (rel2 / "my_agent_mcp").mkdir()
    (rel2 / "persistence").mkdir()
    f2 = rel2 / "core" / "sample.py"
    f2.write_text("V=2\n", encoding="utf-8")
    (rel2 / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": "2222222222222222222222222222222222222222",
            "short_sha": "2222222",
            "source_ref": "v2.0",
            "installed_at": "2026-11-11T10:00:00Z",
            "file_hashes": {"core/sample.py": _compute_file_sha256(f2)},
        }),
        encoding="utf-8",
    )

    # Initially activate rel2
    manager._atomic_symlink_switch(rel2)
    assert stable_link.resolve() == rel2

    # Put real persistent database in data_dir
    persistent_db = data_dir / "my_agent.db"
    conn = sqlite3.connect(str(persistent_db))
    conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT);")
    conn.execute("INSERT INTO records (name) VALUES ('important_user_task');")
    conn.commit()
    conn.close()

    # Perform rollback
    new_active = manager.rollback_release()
    assert new_active.commit_sha == "1111111111111111111111111111111111111111"
    assert stable_link.resolve() == rel1

    # Verify persistent database is 100% intact
    conn2 = sqlite3.connect(str(persistent_db))
    cur = conn2.cursor()
    cur.execute("SELECT name FROM records;")
    rows = cur.fetchall()
    conn2.close()
    assert rows == [("important_user_task",)]


def test_scenario_8_existing_project_authorization_remains_enforced(tmp_path: Path) -> None:
    """AC-8: Authorization manager and cross-project IDOR prevention remain intact."""
    from persistence.sqlite_project_store import SQLiteProjectStore
    from core.security_authorization import SecurityAuthorizationManager
    from core.project import ProjectRecord

    db_path = tmp_path / "auth_test.db"
    store = SQLiteProjectStore(db_path)

    import asyncio
    async def _run() -> None:
        await store.initialize()
        ws_a = tmp_path / "workspace_a"
        ws_b = tmp_path / "workspace_b"
        ws_a.mkdir()
        ws_b.mkdir()

        await store.create(ProjectRecord(project_id="proj-a", name="A", workspace_path=str(ws_a)))
        await store.create(ProjectRecord(project_id="proj-b", name="B", workspace_path=str(ws_b)))

        auth = SecurityAuthorizationManager(project_store=store, server_workspace_root=ws_a)

        pa = await auth.authorize_project("proj-a")
        assert pa.project_id == "proj-a"

        with pytest.raises(SecurityAuthorizationError) as exc_info:
            await auth.authorize_project("proj-b")
        assert exc_info.value.violation_type == "CROSS_PROJECT_IDOR"

    asyncio.run(_run())


def test_safe_sqlite_backup_transactional_integrity(tmp_path: Path) -> None:
    """Verifies that create_safe_sqlite_backup creates a consistent copy in WAL mode."""
    src_db = tmp_path / "source.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO items (val) VALUES ('test_val_1'), ('test_val_2');")
    conn.commit()

    backup_dest = tmp_path / "backup.db"
    result = create_safe_sqlite_backup(src_db, backup_dest)
    assert result == backup_dest
    assert backup_dest.exists()

    bconn = sqlite3.connect(str(backup_dest))
    cur = bconn.cursor()
    cur.execute("SELECT val FROM items ORDER BY id;")
    rows = cur.fetchall()
    bconn.close()
    conn.close()

    assert rows == [("test_val_1",), ("test_val_2",)]


def test_p1b_01_stage_release_does_not_activate_stable_symlink(mock_release_sandbox: dict[str, Path]) -> None:
    """P1B-01: Staging a release does NOT activate it; promotion requires explicit promote_release."""
    releases_root = mock_release_sandbox["releases_root"]
    stable_link = mock_release_sandbox["stable_link"]
    venvs_root = mock_release_sandbox["venvs_root"]

    manager = ReleaseManager(
        releases_root=releases_root,
        stable_link=stable_link,
        venvs_root=venvs_root,
    )

    sha = "3333333333333333333333333333333333333333"
    rel_dir = releases_root / sha
    rel_dir.mkdir()
    (rel_dir / "core").mkdir()
    (rel_dir / "my_agent_mcp").mkdir()
    (rel_dir / "persistence").mkdir()
    f = rel_dir / "core" / "sample.py"
    f.write_text("V=1\n", encoding="utf-8")
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha,
            "short_sha": "3333333",
            "source_ref": "v1.1.0",
            "installed_at": "2026-11-11T12:00:00Z",
            "file_hashes": {"core/sample.py": _compute_file_sha256(f)},
        }),
        encoding="utf-8",
    )

    # Initially, stable_link does NOT exist
    assert not stable_link.exists()

    # List releases: release is staged but NOT active
    releases = manager.list_releases()
    assert len(releases) == 1
    assert releases[0].is_active_stable is False

    # Promote explicitly
    promoted = manager.promote_release(sha)
    assert promoted.is_active_stable is True
    assert stable_link.exists()
    assert stable_link.resolve() == rel_dir


def test_p1b_02_safe_runtime_resolution_unconfigured_process(mock_release_sandbox: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """P1B-02: Legacy/unconfigured processes resolve safely without silent switch to dev DB."""
    dev_root = mock_release_sandbox["dev_root"]
    prod_db = dev_root / "data" / "my_agent.db"
    dev_db = dev_root / "data" / "my_agent_dev.db"
    (dev_root / ".git").mkdir(exist_ok=True)

    # Remove PYTEST_CURRENT_TEST and runtime env vars to simulate outside-pytest unconfigured process
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MYAGENT_RUNTIME_ENV", raising=False)
    monkeypatch.delenv("MYAGENT_DB_PATH", raising=False)

    # 1. Unconfigured launch from dev git checkout defaults to DEVELOPMENT to shield production database
    cfg = resolve_runtime_environment(source_root_override=dev_root)
    assert cfg.environment == RuntimeEnvironment.DEVELOPMENT
    assert is_same_file(cfg.database_path, dev_db)

    # 2. Verified legacy production MCP server process resolves to STABLE and connects to production DB
    monkeypatch.setenv("MYAGENT_LEGACY_MCP_SERVER", "1")
    cfg_legacy = resolve_runtime_environment(source_root_override=dev_root)
    assert cfg_legacy.environment == RuntimeEnvironment.STABLE
    assert is_same_file(cfg_legacy.database_path, prod_db)
    monkeypatch.delenv("MYAGENT_LEGACY_MCP_SERVER", raising=False)

    # 3. dev launch with MYAGENT_DB_PATH=my_agent_dev.db resolves to DEVELOPMENT
    cfg_dev = resolve_runtime_environment(
        db_override=str(dev_db),
        source_root_override=dev_root,
    )
    assert cfg_dev.environment == RuntimeEnvironment.DEVELOPMENT
    assert is_same_file(cfg_dev.database_path, dev_db)

    # 4. Development environment attempting to bind to production DB fails closed
    with pytest.raises(ProductionDatabaseProtectionError):
        resolve_runtime_environment(
            env_override="development",
            db_override=str(prod_db),
            source_root_override=dev_root,
        )

    # 5. Unconfigured dev process attempting to bind to production DB fails closed
    with pytest.raises(ProductionDatabaseProtectionError):
        resolve_runtime_environment(
            env_override=None,
            db_override=str(prod_db),
            source_root_override=dev_root,
        )


def test_p1b_03_permanent_stable_data_dir() -> None:
    """P1B-03: Permanent Stable data directory resolves outside the development repository."""
    perm_dir = get_permanent_stable_data_dir()
    assert perm_dir == (Path.home() / ".my-agent" / "data").resolve()


def test_r02_development_cannot_bind_to_permanent_stable_database(
    mock_release_sandbox: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R02: Development environment is strictly prohibited from binding to the permanent Stable database."""
    isolated_perm_dir = mock_release_sandbox["root"] / "isolated_perm_data_r02"
    isolated_perm_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(isolated_perm_dir))

    from core.runtime_environment import get_permanent_stable_db_path
    perm_db = get_permanent_stable_db_path()
    dev_root = mock_release_sandbox["dev_root"]

    with pytest.raises(ProductionDatabaseProtectionError) as exc_info:
        resolve_runtime_environment(
            env_override="development",
            db_override=str(perm_db),
            source_root_override=dev_root,
        )
    assert "strictly prohibited from binding to the permanent Stable database" in str(exc_info.value)


def test_r03_verify_release_integrity_detects_tampering_and_blocks_promotion(mock_release_sandbox: dict[str, Path]) -> None:
    """R03: Tampering with staged release contents fails integrity check and refuses promotion."""
    from core.release_installer import _compute_file_sha256
    releases_root = mock_release_sandbox["releases_root"]
    stable_link = mock_release_sandbox["stable_link"]

    manager = ReleaseManager(releases_root=releases_root, stable_link=stable_link)
    sha = "4444444444444444444444444444444444444444"
    rel_dir = releases_root / sha
    rel_dir.mkdir()
    (rel_dir / "core").mkdir()
    (rel_dir / "my_agent_mcp").mkdir()
    (rel_dir / "persistence").mkdir()

    sample_file = rel_dir / "core" / "app.py"
    sample_file.write_text("ORIGINAL_CONTENT = True\n", encoding="utf-8")

    # Record legitimate file hashes
    file_hashes = {
        "core/app.py": _compute_file_sha256(sample_file),
    }
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha,
            "short_sha": "4444444",
            "source_ref": "v1.2.0",
            "installed_at": "2026-11-11T14:00:00Z",
            "file_hashes": file_hashes,
        }),
        encoding="utf-8",
    )

    # 1. Untampered release passes integrity check
    check_ok = manager.verify_release_integrity(rel_dir)
    assert check_ok["valid"] is True

    # 2. Tamper with sample_file content
    sample_file.write_text("TAMPERED_INJECTED_CODE = True\n", encoding="utf-8")
    check_tampered = manager.verify_release_integrity(rel_dir)
    assert check_tampered["valid"] is False
    assert "TAMPERING_DETECTED" in check_tampered["reason"]

    # 3. Promotion must fail closed
    with pytest.raises(ReleaseVerificationError) as exc_info:
        manager.promote_release(sha)
    assert "failed integrity verification" in str(exc_info.value)

    # Restore legitimate content
    sample_file.write_text("ORIGINAL_CONTENT = True\n", encoding="utf-8")
    assert manager.verify_release_integrity(rel_dir)["valid"] is True

    # 4. Inject an unauthorized file
    injected_file = rel_dir / "core" / "backdoor.py"
    injected_file.write_text("MALICIOUS = True\n", encoding="utf-8")
    check_injected = manager.verify_release_integrity(rel_dir)
    assert check_injected["valid"] is False
    assert "Unauthorized file" in check_injected["reason"]


def test_negative_rejected_unapproved_git_ref(tmp_path: Path) -> None:
    """F10 / Negative: Resolving an unapproved or invalid git ref fails closed."""
    manager = ReleaseManager(releases_root=tmp_path / "releases")
    with pytest.raises(ReleaseVerificationError) as exc_info:
        manager.resolve_git_commit("nonexistent_branch_or_ref_12345", repo_root=Path.cwd())
    assert "Failed to resolve git ref" in str(exc_info.value)


def test_negative_missing_or_empty_manifest_file_hashes(mock_release_sandbox: dict[str, Path]) -> None:
    """F10 / Negative: Release manifest without file_hashes or with empty dict fails integrity."""
    releases_root = mock_release_sandbox["releases_root"]
    manager = ReleaseManager(releases_root=releases_root)
    sha = "5555555555555555555555555555555555555555"
    rel_dir = releases_root / sha
    rel_dir.mkdir()
    (rel_dir / "core").mkdir()
    (rel_dir / "my_agent_mcp").mkdir()
    (rel_dir / "persistence").mkdir()

    # Manifest with missing file_hashes
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha,
            "short_sha": "5555555",
            "source_ref": "v1.3.0",
        }),
        encoding="utf-8",
    )
    res1 = manager.verify_release_integrity(rel_dir)
    assert res1["valid"] is False
    assert "Missing or empty 'file_hashes'" in res1["reason"]

    # Manifest with empty file_hashes
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha,
            "short_sha": "5555555",
            "source_ref": "v1.3.0",
            "file_hashes": {},
        }),
        encoding="utf-8",
    )
    res2 = manager.verify_release_integrity(rel_dir)
    assert res2["valid"] is False
    assert "Missing or empty 'file_hashes'" in res2["reason"]


def test_negative_manifest_commit_sha_mismatch(mock_release_sandbox: dict[str, Path]) -> None:
    """F10 / Negative: Manifest commit_sha not matching directory name fails integrity."""
    releases_root = mock_release_sandbox["releases_root"]
    manager = ReleaseManager(releases_root=releases_root)
    dir_sha = "6666666666666666666666666666666666666666"
    mismatched_sha = "7777777777777777777777777777777777777777"
    rel_dir = releases_root / dir_sha
    rel_dir.mkdir()
    (rel_dir / "core").mkdir()
    (rel_dir / "my_agent_mcp").mkdir()
    (rel_dir / "persistence").mkdir()

    f = rel_dir / "core" / "sample.py"
    f.write_text("V=1\n", encoding="utf-8")
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": mismatched_sha,
            "file_hashes": {"core/sample.py": _compute_file_sha256(f)},
        }),
        encoding="utf-8",
    )
    res = manager.verify_release_integrity(rel_dir)
    assert res["valid"] is False
    assert "does not match release directory" in res["reason"]


def test_negative_rollback_refuses_tampered_release(mock_release_sandbox: dict[str, Path]) -> None:
    """F10 / Negative: Rollback refuses to switch symlink if target release is tampered."""
    releases_root = mock_release_sandbox["releases_root"]
    stable_link = mock_release_sandbox["stable_link"]
    manager = ReleaseManager(releases_root=releases_root, stable_link=stable_link)

    # Setup active release 1
    sha1 = "8888888888888888888888888888888888888888"
    rel1 = releases_root / sha1
    rel1.mkdir()
    (rel1 / "core").mkdir()
    (rel1 / "my_agent_mcp").mkdir()
    (rel1 / "persistence").mkdir()
    f1 = rel1 / "core" / "app.py"
    f1.write_text("V1\n", encoding="utf-8")
    (rel1 / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha1,
            "installed_at": "2026-11-10T10:00:00Z",
            "file_hashes": {"core/app.py": _compute_file_sha256(f1)},
        }),
        encoding="utf-8",
    )

    # Setup prior release 2 (will be tampered)
    sha2 = "9999999999999999999999999999999999999999"
    rel2 = releases_root / sha2
    rel2.mkdir()
    (rel2 / "core").mkdir()
    (rel2 / "my_agent_mcp").mkdir()
    (rel2 / "persistence").mkdir()
    f2 = rel2 / "core" / "app.py"
    f2.write_text("V2\n", encoding="utf-8")
    (rel2 / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha2,
            "installed_at": "2026-11-09T10:00:00Z",
            "file_hashes": {"core/app.py": _compute_file_sha256(f2)},
        }),
        encoding="utf-8",
    )

    # Point stable to rel1
    manager._atomic_symlink_switch(rel1)
    assert stable_link.resolve() == rel1

    # Tamper with rel2
    f2.write_text("TAMPERED_V2\n", encoding="utf-8")

    # Attempt rollback to rel2 must fail closed
    with pytest.raises(ReleaseVerificationError) as exc_info:
        manager.rollback_release(sha2)
    assert "failed integrity verification" in str(exc_info.value)
    # Symlink MUST remain on rel1
    assert stable_link.resolve() == rel1


def test_negative_rollback_bad_target_and_no_prior(mock_release_sandbox: dict[str, Path]) -> None:
    """F10 / Negative: Rollback to nonexistent target or with no prior releases fails closed."""
    releases_root = mock_release_sandbox["releases_root"]
    stable_link = mock_release_sandbox["stable_link"]
    manager = ReleaseManager(releases_root=releases_root, stable_link=stable_link)

    # No releases installed
    with pytest.raises(ReleaseVerificationError) as exc1:
        manager.rollback_release()
    assert "No installed releases available" in str(exc1.value)

    # Setup single release
    sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    rel = releases_root / sha
    rel.mkdir()
    (rel / "core").mkdir()
    (rel / "my_agent_mcp").mkdir()
    (rel / "persistence").mkdir()
    f = rel / "core" / "app.py"
    f.write_text("V=A\n", encoding="utf-8")
    (rel / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha,
            "file_hashes": {"core/app.py": _compute_file_sha256(f)},
        }),
        encoding="utf-8",
    )
    manager._atomic_symlink_switch(rel)

    # Rollback when only one release exists
    with pytest.raises(ReleaseVerificationError) as exc2:
        manager.rollback_release()
    assert "No prior release found" in str(exc2.value)

    # Rollback to non-existent sha
    with pytest.raises(ReleaseVerificationError) as exc3:
        manager.rollback_release("bad_sha_0000000000")
    assert "was not found in installed releases" in str(exc3.value)


def test_negative_backup_source_dest_collision(tmp_path: Path) -> None:
    """F10 / Negative: create_safe_sqlite_backup refuses identical source and destination."""
    db_file = tmp_path / "colliding.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (x INT);")
    conn.close()

    with pytest.raises(ValueError) as exc:
        create_safe_sqlite_backup(db_file, db_file)
    assert "cannot be identical" in str(exc.value)


def test_negative_backup_missing_source(tmp_path: Path) -> None:
    """F10 / Negative: create_safe_sqlite_backup fails if source does not exist."""
    missing_db = tmp_path / "nonexistent.db"
    dest_db = tmp_path / "dest.db"
    with pytest.raises(FileNotFoundError):
        create_safe_sqlite_backup(missing_db, dest_db)


def test_negative_dev_cannot_target_permanent_stable_db_symlink(
    mock_release_sandbox: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F10 / Negative: Symlink to permanent Stable DB in development environment fails closed."""
    isolated_perm_dir = mock_release_sandbox["root"] / "isolated_perm_data_symlink"
    isolated_perm_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(isolated_perm_dir))

    from core.runtime_environment import get_permanent_stable_db_path
    perm_db = get_permanent_stable_db_path()
    assert str(isolated_perm_dir) in str(perm_db)

    perm_db.write_text("MOCK_PERM_DATA", encoding="utf-8")
    dev_root = mock_release_sandbox["dev_root"]

    symlink_path = dev_root / "data" / "symlink_to_permanent.db"
    try:
        os.symlink(perm_db, symlink_path)
        with pytest.raises(ProductionDatabaseProtectionError) as exc:
            resolve_runtime_environment(
                env_override="development",
                db_override=str(symlink_path),
                source_root_override=dev_root,
            )
        assert "permanent Stable database" in str(exc.value)
    finally:
        if symlink_path.exists() or os.path.islink(symlink_path):
            symlink_path.unlink()


def test_server_rebind_stores_dynamically(tmp_path: Path) -> None:
    """F06 / Dynamic rebind: Verifies server.rebind_stores updates DB_PATH and store singletons."""
    import my_agent_mcp.server as srv
    initial_db = srv.DB_PATH
    new_db = tmp_path / "dynamic_test.db"

    try:
        srv.rebind_stores(new_db)
        assert srv.DB_PATH == new_db
        assert srv.projects._database_path == new_db
        assert srv.conversations._database_path == new_db
        assert srv.tasks._database_path == new_db
        assert srv.plans._database_path == new_db
        assert srv.decisions._database_path == new_db
    finally:
        # Restore initial binding
        srv.rebind_stores(initial_db)


def test_regression_r1_multicycle_symlink_reinstantiation(mock_release_sandbox: dict[str, Path]) -> None:
    """R1: Multi-cycle promotion and rollback with re-instantiated ReleaseManager preserves symlink and data."""
    releases_root = mock_release_sandbox["releases_root"]
    stable_link = mock_release_sandbox["stable_link"]
    data_dir = mock_release_sandbox["data_dir"]

    # Persistent database with initial records
    persistent_db = data_dir / "my_agent.db"
    conn = sqlite3.connect(str(persistent_db))
    conn.execute("CREATE TABLE r1_data (id INT, item TEXT);")
    conn.execute("INSERT INTO r1_data VALUES (1, 'unmodified_production_payload');")
    conn.commit()
    conn.close()

    # 1. Setup Release A
    sha_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    rel_a = releases_root / sha_a
    rel_a.mkdir(parents=True)
    (rel_a / "core").mkdir()
    (rel_a / "my_agent_mcp").mkdir()
    (rel_a / "persistence").mkdir()
    f_a = rel_a / "core" / "app.py"
    f_a.write_text("VERSION = 'A'\n", encoding="utf-8")
    (rel_a / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha_a,
            "short_sha": "aaaaaaa",
            "source_ref": "v1.0",
            "installed_at": "2026-11-10T10:00:00Z",
            "file_hashes": {"core/app.py": _compute_file_sha256(f_a)},
        }),
        encoding="utf-8",
    )

    # 2. Setup Release B
    sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    rel_b = releases_root / sha_b
    rel_b.mkdir(parents=True)
    (rel_b / "core").mkdir()
    (rel_b / "my_agent_mcp").mkdir()
    (rel_b / "persistence").mkdir()
    f_b = rel_b / "core" / "app.py"
    f_b.write_text("VERSION = 'B'\n", encoding="utf-8")
    (rel_b / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha_b,
            "short_sha": "bbbbbbb",
            "source_ref": "v2.0",
            "installed_at": "2026-11-11T10:00:00Z",
            "file_hashes": {"core/app.py": _compute_file_sha256(f_b)},
        }),
        encoding="utf-8",
    )

    # Cycle 1: Manager 1 stages and promotes A
    m1 = ReleaseManager(releases_root=releases_root, stable_link=stable_link, data_dir=data_dir)
    rec_a = m1.promote_release(sha_a)
    assert rec_a.is_active_stable is True
    assert os.path.islink(stable_link)
    assert stable_link.resolve() == rel_a
    del m1

    # Cycle 2: Fresh Manager 2 promotes B
    m2 = ReleaseManager(releases_root=releases_root, stable_link=stable_link, data_dir=data_dir)
    assert m2.stable_link == stable_link
    assert os.path.islink(m2.stable_link)
    rec_b = m2.promote_release(sha_b)
    assert rec_b.is_active_stable is True
    assert os.path.islink(stable_link)
    assert stable_link.resolve() == rel_b
    del m2

    # Cycle 3: Fresh Manager 3 rolls back to A
    m3 = ReleaseManager(releases_root=releases_root, stable_link=stable_link, data_dir=data_dir)
    assert m3.stable_link == stable_link
    rec_roll = m3.rollback_release(sha_a)
    assert rec_roll.commit_sha == sha_a
    assert os.path.islink(stable_link)
    assert stable_link.resolve() == rel_a
    del m3

    # Assert persistent database was never modified
    conn2 = sqlite3.connect(str(persistent_db))
    rows = conn2.execute("SELECT item FROM r1_data;").fetchall()
    conn2.close()
    assert rows == [("unmodified_production_payload",)]


def test_regression_r2_tests_never_touch_real_permanent_stable_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R2: Validates that test suites use isolated directory hierarchies and never mutate real permanent DB."""
    fake_perm_dir = tmp_path / "fake_perm"
    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(fake_perm_dir))

    from core.runtime_environment import get_permanent_stable_db_path
    resolved_path = get_permanent_stable_db_path()
    assert str(fake_perm_dir) in str(resolved_path)
    assert str(Path.home() / ".my-agent" / "data") not in str(resolved_path)


def test_regression_r3_shared_module_launch_shape_fails_safe_to_dev(
    mock_release_sandbox: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: Real module-launch shape (python -m my_agent_mcp.server) in dev repository fails safe to dev without env."""
    dev_root = mock_release_sandbox["dev_root"]
    (dev_root / ".git").mkdir(exist_ok=True)
    dev_db = dev_root / "data" / "my_agent_dev.db"
    prod_db = dev_root / "data" / "my_agent.db"

    # Simulate sys.argv containing the shared entrypoint
    monkeypatch.setattr("sys.argv", ["python", "-m", "my_agent_mcp.server"])
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MYAGENT_RUNTIME_ENV", raising=False)
    monkeypatch.delenv("MYAGENT_DB_PATH", raising=False)
    monkeypatch.delenv("MYAGENT_LEGACY_MCP_SERVER", raising=False)

    # Unconfigured module execution MUST fail safe to DEVELOPMENT
    cfg = resolve_runtime_environment(source_root_override=dev_root)
    assert cfg.environment == RuntimeEnvironment.DEVELOPMENT
    assert is_same_file(cfg.database_path, dev_db)
    assert not is_same_file(cfg.database_path, prod_db)

    # Attempting to bind to production DB without explicit declaration fails closed
    with pytest.raises(ProductionDatabaseProtectionError) as exc:
        resolve_runtime_environment(
            db_override=str(prod_db),
            source_root_override=dev_root,
        )
    assert "prohibited from binding to production database" in str(exc.value)

    # Explicit declaration via MYAGENT_RUNTIME_ENV=stable successfully resolves to STABLE
    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "stable")
    cfg_stable = resolve_runtime_environment(source_root_override=dev_root)
    assert cfg_stable.environment == RuntimeEnvironment.STABLE
    assert is_same_file(cfg_stable.database_path, prod_db)


def test_regression_r4_staged_candidate_venv_dependency_isolation(
    mock_release_sandbox: dict[str, Path],
) -> None:
    """R4: Candidate release venv is fully populated, verified, and free of dev PYTHONPATH leakage."""
    releases_root = mock_release_sandbox["releases_root"]
    venvs_root = mock_release_sandbox["venvs_root"]
    manager = ReleaseManager(releases_root=releases_root, venvs_root=venvs_root)

    sha = "cccccccccccccccccccccccccccccccccccccccc"
    rel_dir = releases_root / sha
    rel_dir.mkdir(parents=True)

    # Mock release directory with required structure
    repo_root = Path(__file__).resolve().parent.parent
    for mod in ["core", "my_agent_mcp", "persistence", "agents", "integrations", "eval"]:
        if (repo_root / mod).exists():
            shutil.copytree(repo_root / mod, rel_dir / mod)

    venv_target = venvs_root / sha
    manager.prepare_and_verify_release_venv(
        release_path=rel_dir,
        venv_path=venv_target,
        python_executable=sys.executable,
    )

    assert venv_target.exists()
    vpy = venv_target / "bin" / "python"
    assert vpy.exists()

    # Verify that the release interpreter imports my_agent_mcp directly from rel_dir
    res = subprocess.run(
        [
            str(vpy),
            "-c",
            "import my_agent_mcp, mcp; print(my_agent_mcp.__file__)",
        ],
        cwd="/tmp",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        timeout=15.0,
    )
    assert res.returncode == 0
    assert str(rel_dir) in res.stdout
    assert str(repo_root) not in res.stdout


def test_regression_cache_suppression_and_release_integrity(
    mock_release_sandbox: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Regression: Enforce runtime cache suppression and strict immutable release integrity.

    Verifies 4 required criteria:
    1. A release passes integrity verification immediately after staging and venv prep.
    2. Verification still passes after release-venv imports without generating in-tree __pycache__.
    3. Verification still passes after an isolated MCP startup check.
    4. Deliberate source tampering remains rejected (strict integrity maintained).
    """
    from core.release_installer import _compute_file_sha256

    releases_root = mock_release_sandbox["releases_root"]
    venvs_root = mock_release_sandbox["venvs_root"]
    manager = ReleaseManager(releases_root=releases_root, venvs_root=venvs_root)

    sha = "dddddddddddddddddddddddddddddddddddddddd"
    rel_dir = releases_root / sha
    rel_dir.mkdir(parents=True)

    repo_root = Path(__file__).resolve().parent.parent
    for mod in ["core", "my_agent_mcp", "persistence", "agents", "integrations", "eval"]:
        if (repo_root / mod).exists():
            shutil.copytree(
                repo_root / mod,
                rel_dir / mod,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

    # Compute valid file hashes for all files in the release directory
    file_hashes: dict[str, str] = {}
    for fpath in rel_dir.rglob("*"):
        if fpath.is_file() and fpath.name != "release_metadata.json":
            rel_posix = fpath.relative_to(rel_dir).as_posix()
            file_hashes[rel_posix] = _compute_file_sha256(fpath)

    metadata = {
        "commit_sha": sha,
        "short_sha": "ddddddd",
        "source_ref": "v1.3.0",
        "installed_at": "2026-11-12T10:00:00Z",
        "file_hashes": file_hashes,
    }
    (rel_dir / "release_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # Prepare and verify release venv
    venv_target = venvs_root / sha
    manager.prepare_and_verify_release_venv(
        release_path=rel_dir,
        venv_path=venv_target,
        python_executable=sys.executable,
    )
    vpy = venv_target / "bin" / "python"
    assert vpy.exists()

    # 1. Immediately after staging and venv prep: integrity passes, no in-tree pycaches
    check_1 = manager.verify_release_integrity(rel_dir)
    assert check_1["valid"] is True, f"Check 1 failed: {check_1.get('reason')}"
    assert list(rel_dir.glob("**/__pycache__")) == [], "Unexpected __pycache__ created in release source"

    # 2. After release-venv imports: integrity still passes, no in-tree pycaches
    res_import = subprocess.run(
        [str(vpy), "-c", "import core.runtime_environment, my_agent_mcp.server; print('IMPORT_OK')"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        timeout=15.0,
    )
    assert res_import.returncode == 0, f"Import failed: {res_import.stderr}"
    check_2 = manager.verify_release_integrity(rel_dir)
    assert check_2["valid"] is True, f"Check 2 failed: {check_2.get('reason')}"
    assert list(rel_dir.glob("**/__pycache__")) == [], "__pycache__ generated during release-venv imports"

    # 3. After isolated MCP startup check: integrity still passes
    mcp_code = (
        "import asyncio\n"
        "from my_agent_mcp.server import _initialize, my_agent_status\n"
        "asyncio.run(_initialize())\n"
        "print('MCP_INIT_OK')\n"
    )
    res_mcp = subprocess.run(
        [str(vpy), "-c", mcp_code],
        cwd="/tmp",
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(Path.home()),
            "MYAGENT_DB_PATH": str(tmp_path / "isolated_startup.db"),
            "MYAGENT_PERMANENT_DATA_DIR": str(tmp_path / "isolated_perm"),
        },
        timeout=15.0,
    )
    assert res_mcp.returncode == 0, f"MCP startup failed: {res_mcp.stderr}"
    check_3 = manager.verify_release_integrity(rel_dir)
    assert check_3["valid"] is True, f"Check 3 failed: {check_3.get('reason')}"
    assert list(rel_dir.glob("**/__pycache__")) == [], "__pycache__ generated during MCP startup"

    # 4. Deliberate source tampering remains rejected (strict integrity preserved)
    tamper_file = rel_dir / "core" / "unauthorized_injected.py"
    tamper_file.write_text("malicious = True\n", encoding="utf-8")
    check_4 = manager.verify_release_integrity(rel_dir)
    assert check_4["valid"] is False
    assert "TAMPERING_DETECTED" in check_4.get("reason", "")


def test_p1c_b1_hooks_runtime_resolution_and_legacy_pre_merge_compatibility(
    mock_release_sandbox: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1C-B1: Hooks resolve Stable/Dev DB paths properly and never redirect legacy unconfigured hooks to dev DB."""
    from my_agent_mcp import transcript_sync, workspace_hook

    dev_root = mock_release_sandbox["dev_root"]
    (dev_root / ".git").mkdir(exist_ok=True)
    prod_db = dev_root / "data" / "my_agent.db"
    dev_db = dev_root / "data" / "my_agent_dev.db"

    isolated_perm_dir = mock_release_sandbox["root"] / "isolated_perm_p1c_b1"
    isolated_perm_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(isolated_perm_dir))

    rel_dir = mock_release_sandbox["releases_root"] / "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    rel_dir.mkdir(parents=True)
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({"commit_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "file_hashes": {}}),
        encoding="utf-8",
    )

    # 1. Pre-merge compatibility: Legacy global hooks running in git checkout WITHOUT env vars (outside pytest)
    # MUST preserve binding to legacy production DB and NEVER silently redirect writes into my_agent_dev.db!
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MYAGENT_RUNTIME_ENV", raising=False)
    monkeypatch.delenv("MYAGENT_DB_PATH", raising=False)

    hook_legacy_cfg = workspace_hook.resolve_hook_runtime_config(source_root_override=dev_root)
    sync_legacy_cfg = transcript_sync.resolve_sync_runtime_config(source_root_override=dev_root)
    assert hook_legacy_cfg.environment == RuntimeEnvironment.STABLE
    assert sync_legacy_cfg.environment == RuntimeEnvironment.STABLE
    assert is_same_file(hook_legacy_cfg.database_path, prod_db)
    assert is_same_file(sync_legacy_cfg.database_path, prod_db)
    assert not is_same_file(hook_legacy_cfg.database_path, dev_db)
    assert is_same_file(hook_legacy_cfg.data_dir, dev_root / "data")

    # 2. Post-cutover Stable release hooks: Running from staged/promoted release resolves to permanent Stable DB
    hook_stable_cfg = workspace_hook.resolve_hook_runtime_config(source_root_override=rel_dir)
    sync_stable_cfg = transcript_sync.resolve_sync_runtime_config(source_root_override=rel_dir)
    assert hook_stable_cfg.environment == RuntimeEnvironment.STABLE
    assert sync_stable_cfg.environment == RuntimeEnvironment.STABLE
    assert is_same_file(hook_stable_cfg.database_path, isolated_perm_dir / "my_agent.db")
    assert is_same_file(sync_stable_cfg.database_path, isolated_perm_dir / "my_agent.db")
    assert is_same_file(hook_stable_cfg.data_dir, isolated_perm_dir)
    assert not hook_stable_cfg.database_path.is_relative_to(rel_dir)

    # 3. Development hooks: Explicit MYAGENT_RUNTIME_ENV=development resolves to my_agent_dev.db
    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "development")
    hook_dev_cfg = workspace_hook.resolve_hook_runtime_config(source_root_override=dev_root)
    sync_dev_cfg = transcript_sync.resolve_sync_runtime_config(source_root_override=dev_root)
    assert hook_dev_cfg.environment == RuntimeEnvironment.DEVELOPMENT
    assert sync_dev_cfg.environment == RuntimeEnvironment.DEVELOPMENT
    assert is_same_file(hook_dev_cfg.database_path, dev_db)
    assert is_same_file(sync_dev_cfg.database_path, dev_db)
    assert is_same_file(hook_dev_cfg.data_dir, dev_root / "data")


@pytest.mark.asyncio
async def test_p1c_b1_hooks_end_to_end_and_cross_workspace_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P1C-B1: End-to-end workspace_hook and transcript_sync execution with cross-workspace isolation."""
    from my_agent_mcp import transcript_sync, workspace_hook
    from persistence.sqlite_conversation_store import SQLiteConversationStore
    from persistence.sqlite_project_store import SQLiteProjectStore

    disposable_db = tmp_path / "hook_e2e" / "my_agent_dev.db"
    disposable_db.parent.mkdir(parents=True)
    untouched_prod_db = tmp_path / "untouched_prod.db"
    untouched_prod_db.write_text("UNTOUCHED_PROD", encoding="utf-8")

    ws_alpha = tmp_path / "ws_alpha"
    ws_beta = tmp_path / "ws_beta"
    ws_alpha.mkdir()
    ws_beta.mkdir()

    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "development")
    monkeypatch.setenv("MYAGENT_DB_PATH", str(disposable_db))
    monkeypatch.setenv("MYAGENT_PRODUCTION_DB_PATH", str(untouched_prod_db))

    # 1. Run workspace_hook for ws_alpha
    hook_payload = {"workspacePaths": [str(ws_alpha)], "invocationNum": 0}
    monkeypatch.setattr(
        workspace_hook.sys,
        "stdin",
        type("FakeStdin", (), {"read": lambda self: json.dumps(hook_payload)})(),
    )
    await workspace_hook.main()
    out = json.loads(capsys.readouterr().out)
    assert len(out["injectSteps"]) == 1
    assert "ws_alpha" in out["injectSteps"][0]["ephemeralMessage"]

    # Verify ws_alpha is registered in disposable_db and untouched_prod_db is unchanged
    pstore = SQLiteProjectStore(disposable_db)
    await pstore.initialize()
    projs = await pstore.list()
    assert len(projs) == 1
    assert projs[0].project_id == "ws_alpha"
    assert untouched_prod_db.read_text(encoding="utf-8") == "UNTOUCHED_PROD"

    # 2. Run transcript_sync for unregistered ws_beta -> must NOT cross-pollute ws_alpha
    transcript_file = tmp_path / "transcript.jsonl"
    transcript_file.write_text(
        json.dumps({
            "step_index": 1,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "<USER_REQUEST>Hello from session</USER_REQUEST>",
            "created_at": "2026-11-12T10:00:00Z",
        }) + "\n",
        encoding="utf-8",
    )

    added_unregistered = await transcript_sync.sync({
        "conversationId": "conv-beta-1",
        "transcriptPath": str(transcript_file),
        "workspacePaths": [str(ws_beta)],
    })
    assert added_unregistered == 0

    # 3. Run transcript_sync for registered ws_alpha -> syncs cleanly into disposable_db
    added_alpha = await transcript_sync.sync({
        "conversationId": "conv-alpha-1",
        "transcriptPath": str(transcript_file),
        "workspacePaths": [str(ws_alpha)],
    })
    assert added_alpha == 1

    cstore = SQLiteConversationStore(disposable_db)
    await cstore.initialize()
    msgs = await cstore.history("ws_alpha", "conv-alpha-1")
    assert len(msgs) == 1
    assert msgs[0].content == "Hello from session"
    assert untouched_prod_db.read_text(encoding="utf-8") == "UNTOUCHED_PROD"


@pytest.mark.asyncio
async def test_p1c_b1_negative_hooks_reject_protected_db_and_aliases(
    mock_release_sandbox: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1C-B1 Negative: Hooks in development fail closed when targeting legacy/permanent prod DB or symlink aliases."""
    from my_agent_mcp import transcript_sync, workspace_hook

    dev_root = mock_release_sandbox["dev_root"]
    (dev_root / ".git").mkdir(exist_ok=True)
    prod_db = dev_root / "data" / "my_agent.db"

    isolated_perm_dir = mock_release_sandbox["root"] / "isolated_perm_neg_b1"
    isolated_perm_dir.mkdir(parents=True, exist_ok=True)
    perm_db = isolated_perm_dir / "my_agent.db"
    perm_db.write_text("PERM_STABLE_DB", encoding="utf-8")

    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(isolated_perm_dir))
    monkeypatch.setenv("MYAGENT_SOURCE_ROOT", str(dev_root))
    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "development")

    alias_link = dev_root / "data" / "prod_alias.db"
    os.symlink(prod_db, alias_link)

    for forbidden_target in (prod_db, perm_db, alias_link):
        monkeypatch.setenv("MYAGENT_DB_PATH", str(forbidden_target))
        with pytest.raises(ProductionDatabaseProtectionError):
            workspace_hook.resolve_hook_runtime_config(source_root_override=dev_root)
        with pytest.raises(ProductionDatabaseProtectionError):
            transcript_sync.resolve_sync_runtime_config(source_root_override=dev_root)
        with pytest.raises(ProductionDatabaseProtectionError):
            await transcript_sync.sync({"conversationId": "c1", "transcriptPath": "/tmp/none", "workspacePaths": []})


def test_p1c_b2_persistent_assets_resolution_and_provenance_key_preservation(
    mock_release_sandbox: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1C-B2: Provenance keys, designs, and browser artifacts use runtime data_dir and never overwrite existing keys."""
    from core.design_md_generator import DesignMdGenerator
    from core.ui_design_style_engine import UiDesignStyleEngine
    from core.user_model import TrustedProvenanceAuthority

    dev_root = mock_release_sandbox["dev_root"]
    (dev_root / ".git").mkdir(exist_ok=True)

    isolated_perm_dir = tmp_path / "perm_stable_data"
    isolated_perm_dir.mkdir(parents=True)
    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(isolated_perm_dir))

    rel_dir = mock_release_sandbox["releases_root"] / "ffffffffffffffffffffffffffffffffffffffff"
    rel_dir.mkdir(parents=True)
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({"commit_sha": "ffffffffffffffffffffffffffffffffffffffff", "file_hashes": {}}),
        encoding="utf-8",
    )

    # 1. Verify Stable release resolves data_dir, designs_dir, provenance_key_path, browser_artifacts_dir to ~/.my-agent/data
    cfg_stable = resolve_runtime_environment(env_override="stable", source_root_override=rel_dir)
    assert cfg_stable.data_dir == isolated_perm_dir.resolve()
    assert cfg_stable.designs_dir == (isolated_perm_dir / "designs").resolve()
    assert cfg_stable.provenance_key_path == (isolated_perm_dir / "provenance.key").resolve()
    assert cfg_stable.browser_artifacts_dir == (isolated_perm_dir / "browser_artifacts").resolve()

    # 2. Verify Development resolves data_dir to dev_root / "data"
    cfg_dev = resolve_runtime_environment(env_override="development", source_root_override=dev_root)
    assert cfg_dev.data_dir == (dev_root / "data").resolve()
    assert cfg_dev.designs_dir == (dev_root / "data" / "designs").resolve()
    assert cfg_dev.provenance_key_path == (dev_root / "data" / "provenance.key").resolve()
    assert cfg_dev.browser_artifacts_dir == (dev_root / "data" / "browser_artifacts").resolve()

    # 3. Provenance key preservation: pre-existing key in Stable data_dir is NEVER regenerated or overwritten
    existing_key_file = isolated_perm_dir / "provenance.key"
    expected_secret = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
    existing_key_file.write_text(expected_secret, encoding="utf-8")
    existing_key_file.chmod(0o600)
    original_inode = existing_key_file.stat().st_ino

    monkeypatch.setenv("MYAGENT_SOURCE_ROOT", str(rel_dir))
    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "stable")
    monkeypatch.delenv("MYAGENT_DB_PATH", raising=False)
    monkeypatch.delenv("MYAGENT_PROVENANCE_SECRET", raising=False)

    TrustedProvenanceAuthority.set_key_path(None)
    TrustedProvenanceAuthority.set_secret(None)
    try:
        resolved_key_path = TrustedProvenanceAuthority.get_key_path()
        assert resolved_key_path == existing_key_file.resolve()
        loaded_secret = TrustedProvenanceAuthority.get_secret()
        assert loaded_secret == expected_secret
        assert existing_key_file.read_text(encoding="utf-8") == expected_secret
        assert existing_key_file.stat().st_ino == original_inode

        token = TrustedProvenanceAuthority.issue_token()
        assert TrustedProvenanceAuthority.validate_token(token) is True

        # Empty/corrupted key file must fail closed rather than silently regenerating
        empty_key = tmp_path / "empty_provenance.key"
        empty_key.write_text("   \n", encoding="utf-8")
        TrustedProvenanceAuthority.set_key_path(empty_key)
        with pytest.raises(RuntimeError, match="refusing to regenerate or overwrite"):
            TrustedProvenanceAuthority.get_secret()
    finally:
        TrustedProvenanceAuthority.set_key_path(None)
        TrustedProvenanceAuthority.set_secret(None)
    # 4. DesignMdGenerator writes to Stable designs_dir (outside rel_dir) and preserves release cleanliness
    ui_engine = UiDesignStyleEngine()
    ctx = ui_engine.analyze_context(description="Fintech trading portal", sector="fintech")
    profile = ui_engine.generate_style_profile(ctx)
    gen = DesignMdGenerator()
    out_design = gen.write_to_agent_repo(profile, project_id="fintech-portal")
    assert out_design == (isolated_perm_dir / "designs" / "fintech-portal.design.md").resolve()
    assert out_design.exists()
    assert not (rel_dir / "data").exists()
    assert gen.read_from_agent_repo("fintech-portal") is not None


def test_p1c_b2_negative_immutable_release_rejects_mutable_writes_and_preserves_integrity(
    mock_release_sandbox: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1C-B2 Negative: Immutable release directories reject DB, data_dir, key, and design writes."""
    from core.design_md_generator import DesignMdGenerator
    from core.ui_design_style_engine import UiDesignStyleEngine
    from core.user_model import TrustedProvenanceAuthority

    releases_root = mock_release_sandbox["releases_root"]
    manager = ReleaseManager(releases_root=releases_root)
    sha = "1212121212121212121212121212121212121212"
    rel_dir = releases_root / sha
    rel_dir.mkdir(parents=True)
    (rel_dir / "core").mkdir()
    (rel_dir / "my_agent_mcp").mkdir()
    (rel_dir / "persistence").mkdir()
    app_file = rel_dir / "core" / "app.py"
    app_file.write_text("IMMUTABLE = True\n", encoding="utf-8")
    (rel_dir / "release_metadata.json").write_text(
        json.dumps({
            "commit_sha": sha,
            "short_sha": "1212121",
            "source_ref": "v1.4.0",
            "file_hashes": {"core/app.py": _compute_file_sha256(app_file)},
        }),
        encoding="utf-8",
    )

    # 1. Attempting to place database_path inside immutable release fails closed
    with pytest.raises(RuntimeIsolationError, match="IMMUTABLE RELEASE PROTECTION"):
        resolve_runtime_environment(
            env_override="stable",
            db_override=str(rel_dir / "data" / "my_agent.db"),
            source_root_override=rel_dir,
        )

    # 2. Attempting to place data_dir inside immutable release fails closed
    with pytest.raises(RuntimeIsolationError, match="IMMUTABLE RELEASE PROTECTION"):
        resolve_runtime_environment(
            env_override="stable",
            data_dir_override=rel_dir / "data",
            source_root_override=rel_dir,
        )

    # 3. Attempting to write provenance.key inside immutable release fails closed
    TrustedProvenanceAuthority.set_key_path(rel_dir / "data" / "provenance.key")
    try:
        with pytest.raises(RuntimeIsolationError, match="IMMUTABLE RELEASE PROTECTION"):
            TrustedProvenanceAuthority.get_secret()
    finally:
        TrustedProvenanceAuthority.set_key_path(None)
        TrustedProvenanceAuthority.set_secret(None)
    # 4. Attempting to write design contract inside immutable release fails closed
    ui_engine = UiDesignStyleEngine()
    ctx = ui_engine.analyze_context(description="Test", sector="saas_b2b")
    profile = ui_engine.generate_style_profile(ctx)
    gen = DesignMdGenerator()
    with pytest.raises(RuntimeIsolationError, match="IMMUTABLE RELEASE PROTECTION"):
        gen.write_to_agent_repo(profile, project_id="bad-proj", storage_dir=rel_dir / "data" / "designs")

    # 5. Release integrity remains 100% valid with zero unexpected files created
    integrity = manager.verify_release_integrity(rel_dir)
    assert integrity["valid"] is True
    assert not (rel_dir / "data").exists()


def test_pr20_p1_1_development_custom_data_dir_does_not_conflict_with_permanent_stable_dir(
    mock_release_sandbox: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR20 P1-1: Valid Development MYAGENT_DATA_DIR does not conflict with permanent Stable directory protection."""
    dev_root = mock_release_sandbox["dev_root"]
    (dev_root / ".git").mkdir(exist_ok=True)
    custom_dev_data = tmp_path / "custom_dev_data_dir"
    custom_dev_data.mkdir(parents=True)

    # 1. With MYAGENT_PERMANENT_DATA_DIR unset, MYAGENT_DATA_DIR in development resolves cleanly
    monkeypatch.delenv("MYAGENT_PERMANENT_DATA_DIR", raising=False)
    monkeypatch.setenv("MYAGENT_RUNTIME_ENV", "development")
    monkeypatch.setenv("MYAGENT_DATA_DIR", str(custom_dev_data))

    assert get_permanent_stable_data_dir() == (Path.home() / ".my-agent" / "data").resolve()
    cfg = resolve_runtime_environment(source_root_override=dev_root)
    assert cfg.environment == RuntimeEnvironment.DEVELOPMENT
    assert cfg.data_dir == custom_dev_data.resolve()
    assert cfg.designs_dir == (custom_dev_data / "designs").resolve()
    assert cfg.provenance_key_path == (custom_dev_data / "provenance.key").resolve()
    assert cfg.browser_artifacts_dir == (custom_dev_data / "browser_artifacts").resolve()

    # 2. Negative: Development MYAGENT_DATA_DIR targeting permanent Stable data_dir, subdirectory, or symlink fails closed
    isolated_perm_dir = tmp_path / "isolated_perm_stable_dir"
    isolated_perm_dir.mkdir(parents=True)
    perm_sub_dir = isolated_perm_dir / "designs"
    perm_sub_dir.mkdir(parents=True)
    perm_alias = tmp_path / "perm_dir_alias"
    os.symlink(isolated_perm_dir, perm_alias)

    monkeypatch.setenv("MYAGENT_PERMANENT_DATA_DIR", str(isolated_perm_dir))
    for forbidden_dir in (isolated_perm_dir, perm_sub_dir, perm_alias):
        monkeypatch.setenv("MYAGENT_DATA_DIR", str(forbidden_dir))
        with pytest.raises(ProductionDatabaseProtectionError, match="permanent Stable data directory"):
            resolve_runtime_environment(source_root_override=dev_root)


def test_pr20_p1_2_provenance_key_creation_fails_closed_on_persistence_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR20 P1-2: Provenance key creation fails closed on any persistence error and never caches or returns an unpersisted secret."""
    from core.user_model import TrustedProvenanceAuthority

    key_path = tmp_path / "secure_data" / "provenance.key"
    TrustedProvenanceAuthority.set_key_path(key_path)
    TrustedProvenanceAuthority.set_secret(None)
    monkeypatch.delenv("MYAGENT_PROVENANCE_SECRET", raising=False)

    try:
        # 1. Simulate os.open failure (e.g. PermissionError)
        def _fail_open(*args: object, **kwargs: object) -> int:
            raise PermissionError("Simulated permission refusal on provenance.key")

        with monkeypatch.context() as mp:
            mp.setattr(os, "open", _fail_open)
            with pytest.raises(PermissionError, match="Simulated permission refusal"):
                TrustedProvenanceAuthority.get_secret()

        assert TrustedProvenanceAuthority._SECRET is None
        assert TrustedProvenanceAuthority._CACHED_KEY_PATH is None
        assert not key_path.exists()

        # 2. Simulate write failure after file descriptor creation (ensuring partial file is cleaned up and nothing is cached)
        real_fdopen = os.fdopen

        def _failing_fdopen(fd: int, *args: object, **kwargs: object) -> object:
            os.close(fd)
            raise OSError("Simulated disk full during provenance key write")

        with monkeypatch.context() as mp:
            mp.setattr(os, "fdopen", _failing_fdopen)
            with pytest.raises(OSError, match="Simulated disk full"):
                TrustedProvenanceAuthority.issue_token()

        assert TrustedProvenanceAuthority._SECRET is None
        assert TrustedProvenanceAuthority._CACHED_KEY_PATH is None
        assert not key_path.exists()

        # 3. Once persistence succeeds, secret is written, verified, and cached
        secret = TrustedProvenanceAuthority.get_secret()
        assert len(secret) == 64
        assert key_path.exists()
        assert key_path.read_text(encoding="utf-8").strip() == secret
        assert TrustedProvenanceAuthority._SECRET == secret
        assert TrustedProvenanceAuthority._CACHED_KEY_PATH == key_path.resolve()
    finally:
        TrustedProvenanceAuthority.set_key_path(None)
        TrustedProvenanceAuthority.set_secret(None)
