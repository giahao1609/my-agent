from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Sequence

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class RuntimeEnvironment(StrEnum):
    STABLE = "stable"
    DEVELOPMENT = "development"


class RuntimeIsolationError(Exception):
    """Base exception for runtime environment isolation violations."""


class ProductionDatabaseProtectionError(RuntimeIsolationError):
    """Raised when an unapproved or development environment targets the production database."""


class CrossEnvironmentPollutionError(RuntimeIsolationError):
    """Raised when an environment attempts cross-environment imports or state corruption."""


class ReleaseVerificationError(RuntimeIsolationError):
    """Raised when release integrity, signature, or git ref check fails."""


def is_same_file(path_a: Path, path_b: Path) -> bool:
    """Checks whether two paths reference the same underlying filesystem inode or path."""
    try:
        resolved_a = path_a.expanduser().resolve()
        resolved_b = path_b.expanduser().resolve()
        if resolved_a == resolved_b:
            return True
    except Exception:
        pass

    try:
        if path_a.exists() and path_b.exists():
            stat_a = os.stat(path_a)
            stat_b = os.stat(path_b)
            if stat_a.st_dev == stat_b.st_dev and stat_a.st_ino == stat_b.st_ino:
                return True
    except Exception:
        pass

    try:
        if os.path.islink(path_a):
            target = Path(os.readlink(path_a))
            if not target.is_absolute():
                target = (path_a.parent / target).resolve()
            if target.resolve() == path_b.expanduser().resolve():
                return True
    except Exception:
        pass

    try:
        if os.path.islink(path_b):
            target = Path(os.readlink(path_b))
            if not target.is_absolute():
                target = (path_b.parent / target).resolve()
            if target.resolve() == path_a.expanduser().resolve():
                return True
    except Exception:
        pass

    return False


def get_protected_production_db(source_root: Path | None = None) -> Path:
    """Returns canonical path to the protected production database."""
    root = (source_root or PROJECT_ROOT).expanduser().resolve()
    # Check explicit env override if provided
    explicit = os.getenv("MYAGENT_PRODUCTION_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (root / "data" / "my_agent.db").resolve()


def create_safe_sqlite_backup(
    src_db: Path,
    backup_dest: Path,
    *,
    timeout: float = 30.0,
    validate_integrity: bool = True,
) -> Path:
    """Creates a transactionally consistent SQLite snapshot using SQLite's Backup API.

    Guarantees:
    - Never corrupts WAL or SHM state of active source connections.
    - Checkpoints WAL transactions before backup using PRAGMA wal_checkpoint(PASSIVE).
    - Runs PRAGMA integrity_check on the destination database.
    - Sets destination file permissions to 0600 and directory permissions to 0700.
    - Refuses to overwrite existing files unless explicitly cleaned.
    - Strictly forbids source and destination collisions.
    """
    src_path = src_db.expanduser().resolve()
    if not src_path.exists() or not src_path.is_file():
        raise FileNotFoundError(f"Source database '{src_path}' does not exist or is not a regular file.")

    dst_path = backup_dest.expanduser().resolve()
    if is_same_file(src_path, dst_path):
        raise ValueError(f"Source and destination database paths cannot be identical: '{src_path}'")

    if dst_path.exists() or os.path.islink(dst_path):
        raise FileExistsError(
            f"Backup destination '{dst_path}' already exists. Overwriting existing backups is prohibited."
        )

    dst_dir = dst_path.parent
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dst_dir, 0o700)
    except OSError:
        pass

    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src_path), timeout=timeout)
        try:
            src_conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error as wal_exc:
            logger.debug("Passive WAL checkpoint on source DB returned: %s", wal_exc)

        dst_conn = sqlite3.connect(str(dst_path), timeout=timeout)
        src_conn.backup(dst_conn)

        if validate_integrity:
            cursor = dst_conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
            if not rows or rows[0][0] != "ok":
                raise ReleaseVerificationError(
                    f"Integrity check failed on backup database '{dst_path}': {rows}"
                )

        try:
            os.chmod(dst_path, 0o600)
        except OSError:
            pass

        return dst_path
    except Exception as exc:
        if dst_conn is not None:
            try:
                dst_conn.close()
            except Exception:
                pass
        if dst_path.exists():
            try:
                dst_path.unlink()
            except OSError:
                pass
        raise RuntimeIsolationError(f"Safe SQLite backup failed: {exc}") from exc
    finally:
        if dst_conn is not None:
            try:
                dst_conn.close()
            except Exception:
                pass
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:
                pass


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    environment: RuntimeEnvironment
    canonical_source_root: Path
    database_path: Path
    workspace_path: Path
    release_commit: str
    log_dir: Path
    is_isolated: bool = True

    def validate(self) -> None:
        """Validates runtime configuration and fails closed on isolation violations."""
        prod_db = get_protected_production_db(self.canonical_source_root)
        perm_db = get_permanent_stable_db_path()

        if self.environment == RuntimeEnvironment.DEVELOPMENT:
            # Development must NEVER write to legacy or permanent production database
            if is_same_file(self.database_path, prod_db):
                raise ProductionDatabaseProtectionError(
                    f"[SAFETY REFUSAL] Development environment (MYAGENT_RUNTIME_ENV='development') is "
                    f"strictly prohibited from binding to the legacy production database '{self.database_path}'."
                )
            if is_same_file(self.database_path, perm_db):
                raise ProductionDatabaseProtectionError(
                    f"[SAFETY REFUSAL] Development environment (MYAGENT_RUNTIME_ENV='development') is "
                    f"strictly prohibited from binding to the permanent Stable database '{self.database_path}'."
                )

        elif self.environment == RuntimeEnvironment.STABLE:
            # Stable must NOT bind to development database
            dev_db = (self.canonical_source_root / "data" / "my_agent_dev.db").resolve()
            if dev_db.exists() and is_same_file(self.database_path, dev_db):
                raise RuntimeIsolationError(
                    f"[SAFETY REFUSAL] Stable environment (MYAGENT_RUNTIME_ENV='stable') cannot bind to "
                    f"the development database '{self.database_path}'."
                )


def detect_accidental_development_imports(
    allowed_root: Path,
    check_modules: Sequence[str] = ("core", "my_agent_mcp", "agents", "persistence"),
) -> list[str]:
    """Inspects loaded sys.modules to verify they resolve exclusively from allowed_root."""
    violations: list[str] = []
    allowed_canonical = allowed_root.expanduser().resolve()

    for mod_name, mod in list(sys.modules.items()):
        if not mod or not hasattr(mod, "__file__") or not mod.__file__:
            continue
        # Check if module belongs to MyAgent package tree
        root_pkg = mod_name.split(".")[0]
        if root_pkg in check_modules:
            mod_file = Path(mod.__file__).expanduser().resolve()
            try:
                if not mod_file.is_relative_to(allowed_canonical):
                    violations.append(
                        f"Module '{mod_name}' loaded from '{mod_file}', which is outside allowed release '{allowed_canonical}'"
                    )
            except AttributeError:
                # Fallback for Python < 3.9 path resolution
                if not str(mod_file).startswith(str(allowed_canonical)):
                    violations.append(
                        f"Module '{mod_name}' loaded from '{mod_file}', which is outside allowed release '{allowed_canonical}'"
                    )

    return violations


DEFAULT_PERMANENT_STABLE_DATA_DIR = Path.home() / ".my-agent" / "data"


def get_permanent_stable_data_dir() -> Path:
    """Returns the target permanent Stable data directory outside the dev tree."""
    explicit = os.getenv("MYAGENT_PERMANENT_DATA_DIR") or os.getenv("MYAGENT_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return DEFAULT_PERMANENT_STABLE_DATA_DIR.expanduser().resolve()


def get_permanent_stable_db_path() -> Path:
    """Returns canonical path to the permanent Stable database outside development."""
    return (get_permanent_stable_data_dir() / "my_agent.db").resolve()


def is_legacy_mcp_server_entrypoint() -> bool:
    """Detects whether the active process is the legacy unconfigured MCP server process.

    Verified criteria:
    - Explicit opt-in via MYAGENT_LEGACY_MCP_SERVER='1'
    - Execution of my_agent_mcp.server via sys.argv (-m my_agent_mcp.server or direct script path)
    - Execution where __main__.__file__ points to my_agent_mcp/server.py
    """
    if os.getenv("MYAGENT_LEGACY_MCP_SERVER") == "1":
        return True
    for arg in sys.argv:
        if "my_agent_mcp.server" in arg or "my_agent_mcp/server.py" in arg:
            return True
    main_mod = sys.modules.get("__main__")
    if main_mod:
        main_file = getattr(main_mod, "__file__", "") or ""
        if "my_agent_mcp/server.py" in main_file or "my_agent_mcp.server" in getattr(main_mod, "__name__", ""):
            return True
    return False


def resolve_runtime_environment(
    env_override: str | None = None,
    db_override: str | None = None,
    ws_override: str | None = None,
    source_root_override: Path | None = None,
) -> RuntimeConfig:
    """Resolves and validates the active MyAgent runtime environment.

    Fails closed on conflicting or unsafe configurations while preserving
    safe backward compatibility for existing unconfigured production processes.
    """
    # 1. Resolve canonical source root
    if source_root_override is not None:
        canonical_root = source_root_override.expanduser().resolve()
    elif os.getenv("MYAGENT_SOURCE_ROOT"):
        canonical_root = Path(os.environ["MYAGENT_SOURCE_ROOT"]).expanduser().resolve()
    else:
        canonical_root = PROJECT_ROOT.resolve()

    raw_db = db_override or os.getenv("MYAGENT_DB_PATH")
    prod_db = get_protected_production_db(canonical_root)
    perm_db = get_permanent_stable_db_path()

    # 2. Determine environment
    raw_env = (env_override or os.getenv("MYAGENT_RUNTIME_ENV") or "").strip().lower()

    if raw_env:
        if raw_env in {"stable", "prod", "production"}:
            env = RuntimeEnvironment.STABLE
        elif raw_env in {"development", "dev"}:
            env = RuntimeEnvironment.DEVELOPMENT
        else:
            raise RuntimeIsolationError(
                f"Invalid MYAGENT_RUNTIME_ENV: '{raw_env}'. Must be 'stable' or 'development'."
            )
    else:
        # P1B-02: Safe backward-compatible inference when MYAGENT_RUNTIME_ENV is unset
        if raw_db:
            cand_db = Path(raw_db).expanduser().resolve()
            if "dev" in cand_db.name.lower() or cand_db.name == "my_agent_dev.db":
                env = RuntimeEnvironment.DEVELOPMENT
            elif (
                is_same_file(cand_db, prod_db)
                or is_same_file(cand_db, perm_db)
                or cand_db.name == "my_agent.db"
            ):
                # When inside a development git checkout, unconfigured code cannot
                # claim STABLE simply by targeting the production database path.
                if (canonical_root / ".git").exists() and not (canonical_root / "release_metadata.json").exists():
                    if not is_legacy_mcp_server_entrypoint():
                        raise ProductionDatabaseProtectionError(
                            f"[SAFETY REFUSAL] Unconfigured development process is prohibited from binding "
                            f"to production database '{cand_db}' without explicit MYAGENT_RUNTIME_ENV='stable'."
                        )
                env = RuntimeEnvironment.STABLE
            else:
                # Custom test/sandbox database
                env = RuntimeEnvironment.DEVELOPMENT
        else:
            if (canonical_root / "release_metadata.json").exists():
                # Staged release source
                env = RuntimeEnvironment.STABLE
            elif (canonical_root / ".git").exists():
                # Unconfigured process in development git repository
                if is_legacy_mcp_server_entrypoint():
                    logger.info(
                        "[LEGACY MCP COMPATIBILITY] Verified legacy production MCP server process. "
                        "Preserving historical binding to production DB."
                    )
                    env = RuntimeEnvironment.STABLE
                else:
                    # Unconfigured dev code, tests, scripts, or interactive execution
                    # Fails safe to DEVELOPMENT to shield production database!
                    logger.info(
                        "[DEVELOPMENT DEFAULT] Unconfigured process in git checkout defaulting to DEVELOPMENT environment."
                    )
                    env = RuntimeEnvironment.DEVELOPMENT
            else:
                env = RuntimeEnvironment.DEVELOPMENT

    # 3. Resolve database path
    if raw_db:
        db_path = Path(raw_db).expanduser().resolve()
    else:
        if env == RuntimeEnvironment.STABLE:
            db_path = prod_db
        else:
            db_path = (canonical_root / "data" / "my_agent_dev.db").resolve()

    # 3. Resolve workspace path
    raw_ws = ws_override or os.getenv("MYAGENT_WORKSPACE")
    if raw_ws:
        ws_path = Path(raw_ws).expanduser().resolve()
    else:
        ws_path = Path.cwd().resolve()

    # 4. Resolve release commit / metadata
    release_commit = "unknown"
    metadata_file = canonical_root / "release_metadata.json"
    if metadata_file.exists():
        try:
            import json
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            release_commit = data.get("commit_sha", "unknown")
        except Exception as exc:
            logger.debug("Failed reading release_metadata.json: %s", exc)

    if release_commit == "unknown" and (canonical_root / ".git").exists():
        try:
            out = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(canonical_root),
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2.0,
            )
            release_commit = out.strip()
        except Exception:
            release_commit = "dev"
    elif release_commit == "unknown":
        release_commit = "dev" if env == RuntimeEnvironment.DEVELOPMENT else "pinned-unknown"

    # 5. Resolve logs directory
    log_dir = canonical_root / "logs" / env.value
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    config = RuntimeConfig(
        environment=env,
        canonical_source_root=canonical_root,
        database_path=db_path,
        workspace_path=ws_path,
        release_commit=release_commit,
        log_dir=log_dir,
        is_isolated=True,
    )

    # Perform mandatory validation
    config.validate()

    return config
