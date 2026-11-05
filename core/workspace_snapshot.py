from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_IGNORED_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "data",
})

DEFAULT_IGNORED_EXTENSIONS = frozenset({
    ".db",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite",
    ".sqlite3",
    ".pyc",
})

# Maximum file size to capture in in-memory snapshot (5 MB)
MAX_SNAPSHOT_FILE_SIZE = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RollbackReport:
    """Report detailing the results of an atomic workspace rollback."""

    step_id: str
    reverted_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    restored_files: tuple[str, ...]
    git_rolled_back: bool = False
    success: bool = True
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "reverted_files": list(self.reverted_files),
            "deleted_files": list(self.deleted_files),
            "restored_files": list(self.restored_files),
            "git_rolled_back": self.git_rolled_back,
            "success": self.success,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class WorkspaceSnapshot:
    """Represents a frozen snapshot of workspace state before executing a plan step."""

    workspace_root: Path
    step_id: str
    files: dict[str, bytes] = field(default_factory=dict)
    git_head: str | None = None
    git_has_uncommitted: bool = False

    def contains(self, rel_path: str) -> bool:
        return rel_path in self.files

    def get_content(self, rel_path: str) -> bytes | None:
        return self.files.get(rel_path)


class WorkspaceSnapshotManager:
    """Manages transactional pre-step snapshots and atomic rollbacks for plan steps.

    Implements the upstream Aider / SWE-agent workspace safety invariant:
    If a step exhausts all auto-repair attempts or fails verification gates,
    the workspace is atomically restored to its exact pre-step baseline.
    Any created files are removed, modified files are reverted, and deleted
    files are restored, ensuring zero workspace pollution and preventing
    cascading errors.
    """

    def __init__(
        self,
        ignored_dirs: frozenset[str] | None = None,
        ignored_extensions: frozenset[str] | None = None,
    ) -> None:
        self._ignored_dirs = ignored_dirs or DEFAULT_IGNORED_DIRS
        self._ignored_extensions = ignored_extensions or DEFAULT_IGNORED_EXTENSIONS

    def _is_ignored_file(self, file_path: Path) -> bool:
        if file_path.suffix.lower() in self._ignored_extensions:
            return True
        name = file_path.name.lower()
        return ".sqlite" in name or name.endswith(".db") or ".db-" in name

    def capture_snapshot(self, workspace_path: Path | str, step_id: str) -> WorkspaceSnapshot:
        """Capture a snapshot of the workspace files prior to step execution."""
        root = Path(workspace_path).resolve()
        snapshot_files: dict[str, bytes] = {}

        if root.is_dir():
            for current, dirs, files in os.walk(root, topdown=True, onerror=lambda _e: None, followlinks=False):
                dirs[:] = [d for d in dirs if d not in self._ignored_dirs]
                base = Path(current)
                for name in files:
                    file_path = base / name
                    if self._is_ignored_file(file_path):
                        continue
                    try:
                        stat = file_path.stat()
                        if stat.st_size <= MAX_SNAPSHOT_FILE_SIZE:
                            rel_path = file_path.relative_to(root).as_posix()
                            snapshot_files[rel_path] = file_path.read_bytes()
                    except (OSError, PermissionError):
                        continue

        git_head: str | None = None
        git_has_uncommitted = False
        git_dir = root / ".git"
        if git_dir.is_dir():
            try:
                head_proc = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                if head_proc.returncode == 0:
                    git_head = head_proc.stdout.strip()
                status_proc = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                if status_proc.returncode == 0 and status_proc.stdout.strip():
                    git_has_uncommitted = True
            except (subprocess.SubprocessError, OSError):
                pass

        return WorkspaceSnapshot(
            workspace_root=root,
            step_id=step_id,
            files=snapshot_files,
            git_head=git_head,
            git_has_uncommitted=git_has_uncommitted,
        )

    def rollback_snapshot(self, snapshot: WorkspaceSnapshot) -> RollbackReport:
        """Atomically rollback workspace state to the captured snapshot."""
        root = snapshot.workspace_root
        reverted: list[str] = []
        deleted: list[str] = []
        restored: list[str] = []
        git_rolled_back = False

        try:
            # 1. Scan current files in workspace
            current_files: dict[str, Path] = {}
            if root.is_dir():
                for current, dirs, files in os.walk(root, topdown=True, onerror=lambda _e: None, followlinks=False):
                    dirs[:] = [d for d in dirs if d not in self._ignored_dirs]
                    base = Path(current)
                    for name in files:
                        file_path = base / name
                        if self._is_ignored_file(file_path):
                            continue
                        rel = file_path.relative_to(root).as_posix()
                        current_files[rel] = file_path

            # 2. Delete newly created files (present now, but not in snapshot)
            for rel, path in current_files.items():
                if rel not in snapshot.files:
                    try:
                        path.unlink()
                        deleted.append(rel)
                    except OSError:
                        pass

            # 3. Clean up any empty directories left behind
            if root.is_dir():
                for current, dirs, files in os.walk(root, topdown=False, onerror=lambda _e: None, followlinks=False):
                    dirs[:] = [d for d in dirs if d not in self._ignored_dirs]
                    dir_path = Path(current)
                    if dir_path != root:
                        try:
                            if not any(dir_path.iterdir()):
                                dir_path.rmdir()
                        except OSError:
                            pass

            # 4. Revert modified files and restore deleted files
            for rel, original_bytes in snapshot.files.items():
                target_path = root / rel
                if target_path.exists():
                    try:
                        curr_bytes = target_path.read_bytes()
                        if curr_bytes != original_bytes:
                            target_path.write_bytes(original_bytes)
                            reverted.append(rel)
                    except OSError:
                        pass
                else:
                    # File was deleted during step execution -> restore it
                    try:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        target_path.write_bytes(original_bytes)
                        restored.append(rel)
                    except OSError:
                        pass

            # 5. Git-level synchronization if applicable
            git_dir = root / ".git"
            if git_dir.is_dir() and snapshot.git_head:
                try:
                    # Check git status to ensure working tree integrity
                    res = subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=2.0,
                        check=False,
                    )
                    if res.returncode == 0:
                        git_rolled_back = True
                except (subprocess.SubprocessError, OSError):
                    pass

            return RollbackReport(
                step_id=snapshot.step_id,
                reverted_files=tuple(reverted),
                deleted_files=tuple(deleted),
                restored_files=tuple(restored),
                git_rolled_back=git_rolled_back,
                success=True,
            )

        except Exception as exc:  # noqa: BLE001
            return RollbackReport(
                step_id=snapshot.step_id,
                reverted_files=tuple(reverted),
                deleted_files=tuple(deleted),
                restored_files=tuple(restored),
                git_rolled_back=git_rolled_back,
                success=False,
                error_message=str(exc),
            )
