#!/usr/bin/env python3
"""CLI Management Utility for MyAgent Two-Tier Runtime Separation.

Provides commands to:
- status: inspect Stable and Development runtime environments.
- install-release: install version-pinned Stable release from verified git ref.
- list-releases: view all local installed releases.
- rollback: roll back Stable symlink to previous or specified release.
- backup-db: transactionally backup production database with integrity checks.
- verify: test isolation boundaries between Stable and Development runtimes.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

# Ensure repository root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.release_installer import ReleaseManager
from core.runtime_environment import (
    RuntimeEnvironment,
    create_safe_sqlite_backup,
    detect_accidental_development_imports,
    get_protected_production_db,
    is_same_file,
    resolve_runtime_environment,
)


def cmd_status(args: argparse.Namespace) -> int:
    """Displays runtime environment topology and isolation status."""
    manager = ReleaseManager()
    cfg = resolve_runtime_environment()

    print("=== MyAgent Runtime Environment Status ===")
    print(f"Active Runtime:     {cfg.environment.value.upper()}")
    print(f"Canonical Root:     {cfg.canonical_source_root}")
    print(f"Active Database:    {cfg.database_path}")
    if cfg.database_path.exists():
        print(f"Database Size:      {cfg.database_path.stat().st_size:,} bytes")
    else:
        print("Database Status:    Not yet initialized")
    print(f"Release Commit:     {cfg.release_commit}")
    print(f"Bound Workspace:    {cfg.workspace_path}")
    print(f"Log Directory:      {cfg.log_dir}")
    print()

    print("=== Release Manager Inventory ===")
    print(f"Releases Root:      {manager.releases_root}")
    print(f"Stable Symlink:     {manager.stable_link}")
    if manager.stable_link.exists() or os.path.islink(manager.stable_link):
        try:
            target = manager.stable_link.resolve()
            print(f"Stable Target:      {target}")
        except Exception as exc:
            print(f"Stable Target:      [Broken link: {exc}]")
    else:
        print("Stable Target:      [Not yet configured]")

    releases = manager.list_releases()
    print(f"Installed Releases: {len(releases)}")
    for r in releases:
        marker = " [ACTIVE STABLE]" if r.is_active_stable else ""
        print(f"  - {r.short_sha} ({r.source_ref}) installed at {r.installed_at}{marker}")

    print()
    print("=== Safety & Isolation Verifications ===")
    prod_db = get_protected_production_db(PROJECT_ROOT)
    print(f"Protected Prod DB:  {prod_db} ({prod_db.stat().st_size:,} bytes)" if prod_db.exists() else f"Protected Prod DB:  {prod_db} (missing)")

    if cfg.environment == RuntimeEnvironment.DEVELOPMENT:
        if is_same_file(cfg.database_path, prod_db):
            print("ISOLATION STATUS:   [CRITICAL VIOLATION] Development is pointing to production DB!")
            return 1
        else:
            print("ISOLATION STATUS:   [PASS] Development runtime is cleanly isolated from production DB.")
    else:
        print("ISOLATION STATUS:   [PASS] Stable runtime configured.")

    return 0


def cmd_install_release(args: argparse.Namespace) -> int:
    """Stages a version-pinned release from git ref without activating it."""
    manager = ReleaseManager()
    ref = args.ref.strip()
    activate = bool(args.activate)
    action_label = "Installing and activating" if activate else "Staging (non-active)"
    print(f"{action_label} release for ref '{ref}'...")

    try:
        rec = manager.install_release(
            git_ref=ref,
            repo_root=PROJECT_ROOT,
            create_venv=args.create_venv,
            activate_as_stable=activate,
        )
        print("Release successfully staged:")
        print(f"  Commit SHA:    {rec.commit_sha}")
        print(f"  Release Path:  {rec.release_path}")
        print(f"  Active Stable: {rec.is_active_stable}")
        if not activate:
            print("  Note: Release is staged only. Run 'promote' to activate as Stable.")
        return 0
    except Exception as exc:
        print(f"Error staging release: {exc}", file=sys.stderr)
        return 1


def cmd_promote(args: argparse.Namespace) -> int:
    """Explicitly promotes a staged release to active Stable."""
    manager = ReleaseManager()
    ref = args.ref.strip()
    print(f"Promoting staged release '{ref}' to active Stable...")

    try:
        rec = manager.promote_release(target_commit_sha_or_ref=ref, repo_root=PROJECT_ROOT)
        print("Promotion successful. Active Stable release:")
        print(f"  Commit SHA:   {rec.commit_sha}")
        print(f"  Release Path: {rec.release_path}")
        return 0
    except Exception as exc:
        print(f"Error during promotion: {exc}", file=sys.stderr)
        return 1


def cmd_list_releases(args: argparse.Namespace) -> int:
    """Lists all installed releases."""
    manager = ReleaseManager()
    releases = manager.list_releases()
    if not releases:
        print("No releases found.")
        return 0

    print(f"Found {len(releases)} release(s):")
    for r in releases:
        active = "*" if r.is_active_stable else " "
        print(f" {active} {r.short_sha} | {r.source_ref:25} | {r.installed_at} | {r.release_path}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Rolls back the Stable symlink."""
    manager = ReleaseManager()
    target_ref = args.ref.strip() if args.ref else None
    print(f"Rolling back Stable release (target: {target_ref or 'previous'})...")

    try:
        rec = manager.rollback_release(target_commit_sha=target_ref)
        print("Rollback successful. New active Stable release:")
        print(f"  Commit SHA:   {rec.commit_sha}")
        print(f"  Release Path: {rec.release_path}")
        return 0
    except Exception as exc:
        print(f"Error during rollback: {exc}", file=sys.stderr)
        return 1


def cmd_backup_db(args: argparse.Namespace) -> int:
    """Creates a transactionally consistent SQLite backup."""
    prod_db = Path(args.src) if args.src else get_protected_production_db(PROJECT_ROOT)
    if args.dest:
        dest_db = Path(args.dest)
    else:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        dest_db = prod_db.parent / f"{prod_db.stem}_backup_{ts}.db"

    print(f"Initiating transactional backup of '{prod_db}' -> '{dest_db}'...")
    try:
        out = create_safe_sqlite_backup(prod_db, dest_db)
        print(f"Backup verified and complete: {out} ({out.stat().st_size:,} bytes)")
        return 0
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1


def cmd_verify(args: argparse.Namespace) -> int:
    """Runs deterministic isolation and boundary checks."""
    print("Running MyAgent Runtime Separation Verification Checks...")
    errors: list[str] = []

    # Check 1: Prod DB protection in dev
    prod_db = get_protected_production_db(PROJECT_ROOT)
    try:
        resolve_runtime_environment(
            env_override="development",
            db_override=str(prod_db),
            source_root_override=PROJECT_ROOT,
        )
        errors.append("CHECK 1 FAILED: Development allowed binding to production DB!")
    except Exception:
        print("[PASS] Check 1: Development binding to legacy production DB fails closed.")

    # Check 2: Dev default database is independent
    cfg_dev = resolve_runtime_environment(
        env_override="development",
        db_override=None,
        source_root_override=PROJECT_ROOT,
    )
    if is_same_file(cfg_dev.database_path, prod_db):
        errors.append("CHECK 2 FAILED: Development default DB resolved to production DB!")
    else:
        print(f"[PASS] Check 2: Development default DB ({cfg_dev.database_path.name}) is independent.")

    # Check 3: Permanent Stable DB protection in dev
    from core.runtime_environment import get_permanent_stable_db_path
    perm_db = get_permanent_stable_db_path()
    try:
        resolve_runtime_environment(
            env_override="development",
            db_override=str(perm_db),
            source_root_override=PROJECT_ROOT,
        )
        errors.append("CHECK 3 FAILED: Development allowed binding to permanent Stable DB!")
    except Exception:
        print("[PASS] Check 3: Development binding to permanent Stable DB fails closed.")

    # Check 4: Unconfigured dev code cannot bind to production DB
    try:
        resolve_runtime_environment(
            env_override=None,
            db_override=str(prod_db),
            source_root_override=PROJECT_ROOT,
        )
        errors.append("CHECK 4 FAILED: Unconfigured dev execution allowed binding to production DB!")
    except Exception:
        print("[PASS] Check 4: Unconfigured dev process binding to production DB fails closed.")

    # Check 5: Accidental import detector
    violations = detect_accidental_development_imports(PROJECT_ROOT)
    if violations:
        print(f"[INFO] Import detector noted {len(violations)} loaded module(s) outside root.")
    else:
        print("[PASS] Check 5: Import detector initialized cleanly.")

    if errors:
        print("\nVerification Failures:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("\nAll runtime separation boundary checks PASSED.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MyAgent Runtime Environment Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Show runtime and isolation status")
    p_status.set_defaults(func=cmd_status)

    # install-release / stage-release
    p_install = subparsers.add_parser("stage-release", aliases=["install-release"], help="Stage pinned release (non-active)")
    p_install.add_argument("--ref", required=True, help="Git commit, tag or branch to stage")
    p_install.add_argument("--create-venv", action="store_true", help="Create isolated release venv")
    p_install.add_argument("--activate", action="store_true", default=False, help="Explicitly activate as Stable upon staging")
    p_install.set_defaults(func=cmd_install_release)

    # promote
    p_promote = subparsers.add_parser("promote", help="Promote a staged release to active Stable")
    p_promote.add_argument("--ref", required=True, help="Git commit SHA or ref of staged release to promote")
    p_promote.set_defaults(func=cmd_promote)

    # list-releases
    p_list = subparsers.add_parser("list-releases", help="List installed releases")
    p_list.set_defaults(func=cmd_list_releases)

    # rollback
    p_rollback = subparsers.add_parser("rollback", help="Roll back stable link")
    p_rollback.add_argument("--ref", default=None, help="Specific commit SHA to rollback to")
    p_rollback.set_defaults(func=cmd_rollback)

    # backup-db
    p_backup = subparsers.add_parser("backup-db", help="Backup SQLite database safely")
    p_backup.add_argument("--src", default=None, help="Source database path")
    p_backup.add_argument("--dest", default=None, help="Destination database path")
    p_backup.set_defaults(func=cmd_backup_db)

    # verify
    p_verify = subparsers.add_parser("verify", help="Run runtime isolation checks")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
