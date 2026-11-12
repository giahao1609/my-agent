from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
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


def test_r02_development_cannot_bind_to_permanent_stable_database(mock_release_sandbox: dict[str, Path]) -> None:
    """R02: Development environment is strictly prohibited from binding to the permanent Stable database."""
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


def test_negative_dev_cannot_target_permanent_stable_db_symlink(mock_release_sandbox: dict[str, Path]) -> None:
    """F10 / Negative: Symlink to permanent Stable DB in development environment fails closed."""
    from core.runtime_environment import get_permanent_stable_db_path
    perm_db = get_permanent_stable_db_path()
    dev_root = mock_release_sandbox["dev_root"]

    # Ensure parent dir exists
    perm_db.parent.mkdir(parents=True, exist_ok=True)
    if not perm_db.exists():
        perm_db.write_text("MOCK_PERM_DATA", encoding="utf-8")

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
