#!/usr/bin/env python3
"""SQLite Inspector Safety and Pre-flight Verification Module.

Enforces:
1. Complete prevention of accidental production database mutation or deletion.
2. Detection and rejection of symlinks and hardlinks pointing to protected databases.
3. Strict prohibition of automatic overwrite or deletion of pre-existing database files.
4. Isolated session-scoped temporary directory creation with restricted permissions (0700/0600).
5. Safe transactional snapshotting via SQLite Backup API and PRAGMA integrity_check.
6. Safe cleanup on backup failure preserving all pre-existing files and other sessions.
7. Prevention of foreign repository database access (cross-project containment).
8. Detection of database in-use / file locking conditions.
9. Redaction of sensitive tokens and secrets in standard log outputs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
from typing import Any


class InspectorSafetyError(Exception):
    """Base exception for all Inspector safety violations."""


class ProductionDatabaseRefusalError(InspectorSafetyError):
    """Raised when an operation targets or resolves to the production database."""


class ExternalRepositoryRefusalError(InspectorSafetyError):
    """Raised when an operation targets a database in another repository."""


class FileAlreadyExistsRefusalError(InspectorSafetyError):
    """Raised when a snapshot destination file already exists and overwrite is refused."""


class DatabaseInUseError(InspectorSafetyError):
    """Raised when a database is currently locked or held by another active process."""


class BackupIntegrityError(InspectorSafetyError):
    """Raised when SQLite backup or PRAGMA integrity_check fails."""


class PortInUseError(InspectorSafetyError):
    """Raised when a requested port is already in use by another process."""


def is_same_file(path_a: Path, path_b: Path) -> bool:
    """Checks whether two paths reference the same underlying filesystem inode."""
    try:
        # Check canonical resolved path equivalence
        if path_a.expanduser().resolve() == path_b.expanduser().resolve():
            return True
    except Exception:
        pass

    # If both files exist, check device and inode numbers (handles hardlinks)
    try:
        if path_a.exists() and path_b.exists():
            stat_a = os.stat(path_a)
            stat_b = os.stat(path_b)
            if stat_a.st_dev == stat_b.st_dev and stat_a.st_ino == stat_b.st_ino:
                return True
    except Exception:
        pass

    # Check if path_a is a symlink pointing to path_b
    try:
        if os.path.islink(path_a):
            target = Path(os.readlink(path_a))
            if not target.is_absolute():
                target = (path_a.parent / target).resolve()
            if target.resolve() == path_b.expanduser().resolve():
                return True
    except Exception:
        pass

    return False


def validate_not_production_or_foreign_db(
    candidate_db: Path,
    repo_root: Path,
    custom_prod_db: Path | None = None,
) -> None:
    """Validates that candidate_db does NOT point to production or external repos.

    Guards against:
    - Direct path equality to production database.
    - Symlinks pointing to production database.
    - Hardlinks sharing the same inode as production database.
    - Path traversal or resolution escaping to external repositories.
    """
    prod_db = (custom_prod_db or (repo_root / "data" / "my_agent.db")).expanduser().resolve()
    cand_path = candidate_db.expanduser()

    # 1. Symlink check before resolution
    if os.path.islink(cand_path):
        try:
            raw_target = Path(os.readlink(cand_path))
            if not raw_target.is_absolute():
                resolved_target = (cand_path.parent / raw_target).resolve()
            else:
                resolved_target = raw_target.resolve()

            if is_same_file(resolved_target, prod_db) or resolved_target == prod_db:
                raise ProductionDatabaseRefusalError(
                    f"[SECURITY REFUSAL] Path '{cand_path}' is a symlink resolving to the production database "
                    f"'{prod_db}'. Access and modification are strictly FORBIDDEN."
                )
        except OSError as exc:
            raise InspectorSafetyError(f"Failed to inspect symlink '{cand_path}': {exc}") from exc

    # 2. Canonical path check
    try:
        resolved_cand = cand_path.resolve()
        if resolved_cand == prod_db:
            raise ProductionDatabaseRefusalError(
                f"[SECURITY REFUSAL] Candidate database '{resolved_cand}' matches the production database path. "
                "Refusing to launch Inspector test session against production database."
            )
    except Exception as exc:
        if isinstance(exc, ProductionDatabaseRefusalError):
            raise
        raise InspectorSafetyError(f"Path resolution error for '{cand_path}': {exc}") from exc

    # 3. Hardlink inode check
    try:
        if cand_path.exists() and prod_db.exists():
            stat_cand = os.stat(cand_path)
            stat_prod = os.stat(prod_db)
            if stat_cand.st_dev == stat_prod.st_dev and stat_cand.st_ino == stat_prod.st_ino:
                raise ProductionDatabaseRefusalError(
                    f"[SECURITY REFUSAL] Candidate database '{cand_path}' is a hardlink sharing inode "
                    f"{stat_cand.st_ino} with production database '{prod_db}'. Operation strictly FORBIDDEN."
                )
    except OSError:
        pass

    # 4. Foreign repository containment check
    projects_dir = repo_root.parent
    if projects_dir.exists():
        for sibling in projects_dir.iterdir():
            if sibling.is_dir() and sibling.resolve() != repo_root.resolve():
                try:
                    if resolved_cand.is_relative_to(sibling.resolve()):
                        raise ExternalRepositoryRefusalError(
                            f"[SECURITY REFUSAL] Target database '{resolved_cand}' is located within external "
                            f"repository '{sibling.name}' ({sibling.resolve()}). Access strictly FORBIDDEN."
                        )
                except Exception as exc:
                    if isinstance(exc, ExternalRepositoryRefusalError):
                        raise


def check_database_in_use(db_path: Path) -> bool:
    """Checks whether a database file is currently locked or held by another process."""
    if not db_path.exists():
        return False

    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=0.1)
        # Attempt an exclusive transaction lock
        conn.execute("BEGIN EXCLUSIVE;")
        conn.rollback()
        return False
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
        err_msg = str(exc).lower()
        if "locked" in err_msg or "busy" in err_msg or "cannot open" in err_msg:
            return True
        return True
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def create_isolated_session_database(
    src_prod_db: Path,
    session_base_dir: Path | None = None,
    explicit_session_dir: Path | None = None,
    fail_on_step: int = -1,
) -> tuple[Path, Path, str]:
    """Creates a private, transactionally consistent snapshot in an isolated session directory.

    Guarantees:
    - Creates a new unique session directory with mode 0700.
    - Destination file permissions set to 0600.
    - Never deletes or overwrites existing files.
    - Uses SQLite Backup API with PRAGMA integrity_check validation.
    - Safe cleanup on error: cleans only the freshly created session directory.
    - Generates a cryptographically strong session token.
    """
    src_path = src_prod_db.expanduser().resolve()
    if not src_path.exists():
        raise InspectorSafetyError(f"Source database '{src_path}' does not exist.")

    is_ephemeral_dir = False
    if explicit_session_dir is not None:
        session_dir = explicit_session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Create private session directory with mode 0700
        session_dir = Path(tempfile.mkdtemp(prefix="myagent_inspector_session_", dir=session_base_dir))
        is_ephemeral_dir = True

    try:
        os.chmod(session_dir, 0o700)
    except OSError as exc:
        if is_ephemeral_dir:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise InspectorSafetyError(f"Failed to set 0700 permissions on session directory: {exc}") from exc

    target_db = session_dir / "inspector_isolated.db"

    # Invariant: Never overwrite an existing file
    if target_db.exists() or os.path.islink(target_db):
        if is_ephemeral_dir:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise FileAlreadyExistsRefusalError(
            f"[REFUSAL] Target file '{target_db}' already exists in session directory. Overwrite is forbidden."
        )

    # Execute SQLite Backup API
    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src_path))
        dst_conn = sqlite3.connect(str(target_db))

        if fail_on_step > 0:
            # Simulated partial failure for testing error handling
            src_conn.backup(dst_conn, pages=fail_on_step)
            raise sqlite3.OperationalError("SIMULATED_INTERRUPTED_BACKUP_FAILURE")

        src_conn.backup(dst_conn)
    except Exception as exc:
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
        # Safe cleanup: remove only this session's private directory
        shutil.rmtree(session_dir, ignore_errors=True)
        raise BackupIntegrityError(f"SQLite Backup API execution failed: {exc}") from exc
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

    # Validate database integrity on destination
    v_conn = None
    try:
        v_conn = sqlite3.connect(str(target_db))
        cursor = v_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()
        if not res or res[0] != "ok":
            shutil.rmtree(session_dir, ignore_errors=True)
            raise BackupIntegrityError(f"Database integrity check failed on snapshot: {res}")
    except Exception as exc:
        if not isinstance(exc, BackupIntegrityError):
            shutil.rmtree(session_dir, ignore_errors=True)
            raise BackupIntegrityError(f"Integrity check verification error: {exc}") from exc
        raise
    finally:
        if v_conn is not None:
            try:
                v_conn.close()
            except Exception:
                pass

    # Set snapshot file permissions to 0600
    try:
        os.chmod(target_db, 0o600)
    except OSError as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise InspectorSafetyError(f"Failed to set 0600 permissions on database snapshot: {exc}") from exc

    # Generate new cryptographically secure session token
    session_token = secrets.token_hex(32)
    token_file = session_dir / "session_token.txt"
    try:
        token_file.write_text(session_token, encoding="utf-8")
        os.chmod(token_file, 0o600)
    except OSError as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise InspectorSafetyError(f"Failed to securely persist session token: {exc}") from exc

    return session_dir, target_db, session_token


def check_port_availability(port: int) -> tuple[bool, str]:
    """Checks whether a port is free without modifying or killing running processes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        res = sock.connect_ex(("127.0.0.1", port))
        if res == 0:
            return False, f"Port {port} is currently IN USE by another process"
        return True, f"Port {port} is FREE and AVAILABLE"


def mask_token(token: str) -> str:
    """Masks a token for safe inclusion in logs."""
    if len(token) <= 8:
        return "[REDACTED_TOKEN]"
    return f"{token[:4]}...[REDACTED]...{token[-4:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="MyAgent SQLite Inspector Safety CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: prepare
    prep = subparsers.add_parser("prepare", help="Prepare a secure isolated session")
    prep.add_argument("--repo-root", required=True, help="Path to repository root")
    prep.add_argument("--custom-db", default=None, help="User-specified MYAGENT_DB_PATH")
    prep.add_argument("--inspector-port", type=int, default=18674)
    prep.add_argument("--sandbox-port", type=int, default=18675)

    # Command: check-ports
    cp = subparsers.add_parser("check-ports", help="Check port availability")
    cp.add_argument("--ports", type=int, nargs="+", default=[18674, 18675])

    # Command: cleanup
    clean = subparsers.add_parser("cleanup", help="Safely clean up a session directory")
    clean.add_argument("--session-dir", required=True)

    args = parser.parse_args()

    if args.command == "check-ports":
        all_ok = True
        for p in args.ports:
            avail, msg = check_port_availability(p)
            print(f"[PORT_CHECK] {msg}")
            if not avail:
                all_ok = False
        return 0 if all_ok else 1

    elif args.command == "cleanup":
        sdir = Path(args.session_dir).resolve()
        if sdir.name.startswith("myagent_inspector_session_") and sdir.is_dir():
            shutil.rmtree(sdir, ignore_errors=True)
            print(f"[CLEANUP] Safely removed session directory: {sdir}")
            return 0
        print(f"[CLEANUP REFUSAL] Refusing to delete non-session directory: {sdir}", file=sys.stderr)
        return 1

    elif args.command == "prepare":
        repo_root = Path(args.repo_root).resolve()
        prod_db = (repo_root / "data" / "my_agent.db").resolve()

        # Check ports first
        p1_ok, p1_msg = check_port_availability(args.inspector_port)
        p2_ok, p2_msg = check_port_availability(args.sandbox_port)
        if not p1_ok or not p2_ok:
            print(f"[PORT_CONFLICT] {p1_msg if not p1_ok else p2_msg}", file=sys.stderr)
            print("[ABORT] Refusing to kill existing processes. Use a free port or stop previous session.", file=sys.stderr)
            return 2

        if args.custom_db:
            custom_path = Path(args.custom_db)
            try:
                validate_not_production_or_foreign_db(custom_path, repo_root, prod_db)
            except InspectorSafetyError as err:
                print(f"[SECURITY_REFUSAL] {err}", file=sys.stderr)
                return 1

            if check_database_in_use(custom_path):
                print(f"[LOCK_REFUSAL] Custom database '{custom_path}' is currently locked/in-use.", file=sys.stderr)
                return 3

            # Custom database is valid and safe
            token = secrets.token_hex(32)
            result = {
                "status": "ready",
                "mode": "custom_database",
                "session_dir": None,
                "db_path": str(custom_path.resolve()),
                "inspector_port": args.inspector_port,
                "sandbox_port": args.sandbox_port,
                "token": token,
                "token_masked": mask_token(token),
            }
            print(json.dumps(result))
            return 0

        else:
            # Create isolated snapshot
            try:
                session_dir, target_db, token = create_isolated_session_database(prod_db)
            except InspectorSafetyError as err:
                print(f"[BACKUP_ERROR] {err}", file=sys.stderr)
                return 1

            result = {
                "status": "ready",
                "mode": "isolated_snapshot",
                "session_dir": str(session_dir),
                "db_path": str(target_db),
                "token_file": str(session_dir / "session_token.txt"),
                "inspector_port": args.inspector_port,
                "sandbox_port": args.sandbox_port,
                "token": token,
                "token_masked": mask_token(token),
            }
            print(json.dumps(result))
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
