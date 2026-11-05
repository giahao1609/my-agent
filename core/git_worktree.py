from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_DIRS_TO_LINK = ("node_modules", ".venv", "target", "vendor")


@dataclass(frozen=True, slots=True)
class WorktreeContext:
    """Represents an active, isolated Git worktree workspace for a plan step."""

    step_id: str
    base_repo_path: Path
    worktree_path: Path
    branch_name: str
    base_branch: str
    is_active: bool = True
    symlinked_caches: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "base_repo_path": str(self.base_repo_path),
            "worktree_path": str(self.worktree_path),
            "branch_name": self.branch_name,
            "base_branch": self.base_branch,
            "is_active": self.is_active,
            "symlinked_caches": list(self.symlinked_caches),
        }


@dataclass(frozen=True, slots=True)
class WorktreeMergeReport:
    """Report detailing the merge of an approved worktree back into the base repository."""

    step_id: str
    branch_name: str
    base_branch: str
    success: bool
    commit_sha: str | None = None
    files_changed: tuple[str, ...] = field(default_factory=tuple)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "branch_name": self.branch_name,
            "base_branch": self.base_branch,
            "success": self.success,
            "commit_sha": self.commit_sha,
            "files_changed": list(self.files_changed),
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class WorktreeAbortReport:
    """Report detailing the teardown and cleanup of an aborted/failed worktree."""

    step_id: str
    branch_name: str
    worktree_removed: bool
    branch_deleted: bool
    success: bool
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "branch_name": self.branch_name,
            "worktree_removed": self.worktree_removed,
            "branch_deleted": self.branch_deleted,
            "success": self.success,
            "error_message": self.error_message,
        }


class GitWorktreeManager:
    """Manages transactional step-level Git worktrees for multi-agent isolation.

    Implements the upstream worktree pattern (worktrunk / SWE-agent harness):
    - Each plan step runs inside a dedicated, physical Git worktree on an isolated branch.
    - Build caches (.venv, node_modules) are symlinked from root for instantaneous startup.
    - Approved steps are merged into the target branch without workspace contention.
    - Failed/exhausted steps are cleanly aborted and pruned with zero pollution to main.
    - Gracefully degrades to non-worktree mode if the target workspace is not a Git repo.
    """

    def __init__(
        self,
        *,
        worktree_base_dir: str = ".agents/worktrees",
        enable_cache_symlinks: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._worktree_base_dir = worktree_base_dir
        self._enable_cache_symlinks = enable_cache_symlinks
        self._timeout_seconds = timeout_seconds

    def _run_git(
        self,
        args: list[str],
        cwd: Path,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=check,
        )

    def is_git_repository(self, path: Path | str) -> bool:
        """Check if target path is a valid Git repository."""
        target = Path(path).resolve()
        if not target.exists():
            return False
        try:
            res = self._run_git(["rev-parse", "--is-inside-work-tree"], cwd=target)
            return res.returncode == 0 and res.stdout.strip() == "true"
        except (subprocess.SubprocessError, OSError):
            return False

    def get_current_branch(self, repo_path: Path | str) -> str:
        """Get the current branch or symbolic ref of the repository."""
        target = Path(repo_path).resolve()
        try:
            res = self._run_git(["symbolic-ref", "--short", "HEAD"], cwd=target)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            # In detached HEAD state, fall back to commit SHA
            res_commit = self._run_git(["rev-parse", "HEAD"], cwd=target)
            if res_commit.returncode == 0 and res_commit.stdout.strip():
                return res_commit.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
        return "main"

    def _ensure_git_exclude(self, repo_root: Path) -> None:
        """Ensure worktree directory is ignored locally in .git/info/exclude."""
        git_dir = repo_root / ".git"
        if not git_dir.is_dir():
            return
        exclude_file = git_dir / "info" / "exclude"
        try:
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            existing = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
            lines = [line.strip() for line in existing.splitlines()]
            to_add = [".agents", ".agents/*", self._worktree_base_dir, ".worktrees"]
            needed = [pat for pat in to_add if pat not in lines]
            if needed:
                content = existing + ("\n" if existing and not existing.endswith("\n") else "")
                content += "\n".join(needed) + "\n"
                exclude_file.write_text(content, encoding="utf-8")
        except OSError:
            pass

    def create_worktree(
        self,
        base_repo_path: Path | str,
        step_id: str,
        branch_name: str | None = None,
    ) -> WorktreeContext:
        """Create a dedicated, isolated Git worktree for executing a plan step."""
        root = Path(base_repo_path).resolve()
        if not self.is_git_repository(root):
            return WorktreeContext(
                step_id=step_id,
                base_repo_path=root,
                worktree_path=root,
                branch_name="",
                base_branch="",
                is_active=False,
            )

        base_branch = self.get_current_branch(root)
        clean_step_id = re.sub(r"[^\w.-]", "_", step_id)
        target_branch = branch_name or f"agent-worktree/{clean_step_id}"
        worktrees_root = root / self._worktree_base_dir
        worktree_path = (worktrees_root / f"step-{clean_step_id}").resolve()

        self._ensure_git_exclude(root)

        # 1. Clean up existing worktree or branch if leftover from prior runs
        try:
            if worktree_path.exists():
                self._run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=root)
                if worktree_path.exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
            self._run_git(["branch", "-D", target_branch], cwd=root)
            self._run_git(["worktree", "prune"], cwd=root)
        except Exception:  # noqa: BLE001, S110
            pass

        # 2. Add new git worktree with branch checked out at HEAD
        worktrees_root.mkdir(parents=True, exist_ok=True)
        res_add = self._run_git(
            ["worktree", "add", "-b", target_branch, str(worktree_path), "HEAD"],
            cwd=root,
        )
        if res_add.returncode != 0:
            return WorktreeContext(
                step_id=step_id,
                base_repo_path=root,
                worktree_path=root,
                branch_name="",
                base_branch=base_branch,
                is_active=False,
            )

        # 3. Symlink build/dependency caches (node_modules, .venv, etc.)
        symlinked: list[str] = []
        if self._enable_cache_symlinks:
            for cache_name in CACHE_DIRS_TO_LINK:
                src_cache = root / cache_name
                dst_cache = worktree_path / cache_name
                if src_cache.exists() and not dst_cache.exists():
                    try:
                        os.symlink(src_cache, dst_cache, target_is_directory=src_cache.is_dir())
                        symlinked.append(cache_name)
                    except OSError:
                        pass

        return WorktreeContext(
            step_id=step_id,
            base_repo_path=root,
            worktree_path=worktree_path,
            branch_name=target_branch,
            base_branch=base_branch,
            is_active=True,
            symlinked_caches=tuple(symlinked),
        )

    def merge_and_cleanup(
        self,
        ctx: WorktreeContext,
        commit_message: str | None = None,
    ) -> WorktreeMergeReport:
        """Commit changes in the worktree, merge to base branch, and destroy the worktree."""
        if not ctx.is_active:
            return WorktreeMergeReport(
                step_id=ctx.step_id,
                branch_name=ctx.branch_name,
                base_branch=ctx.base_branch,
                success=False,
                error_message="Cannot merge inactive worktree context",
            )

        root = ctx.base_repo_path
        wt = ctx.worktree_path
        files_changed: list[str] = []

        try:
            # 1. Commit any pending changes inside the worktree
            status_res = self._run_git(["status", "--porcelain"], cwd=wt)
            if status_res.returncode == 0 and status_res.stdout.strip():
                self._run_git(["add", "-A"], cwd=wt)
                msg = commit_message or f"feat(step): apply verified changes for step {ctx.step_id}"
                self._run_git(["commit", "-m", msg], cwd=wt)

            # 2. Inspect changed files between base branch and step branch
            diff_res = self._run_git(["diff", "--name-only", f"{ctx.base_branch}...{ctx.branch_name}"], cwd=root)
            if diff_res.returncode == 0 and diff_res.stdout.strip():
                files_changed = [line.strip() for line in diff_res.stdout.splitlines() if line.strip()]

            # 3. Merge step branch into base branch
            merge_res = self._run_git(
                ["merge", ctx.branch_name, "--no-edit"],
                cwd=root,
            )
            if merge_res.returncode != 0:
                # Merge failed (e.g. conflict) -> abort merge
                self._run_git(["merge", "--abort"], cwd=root)
                return WorktreeMergeReport(
                    step_id=ctx.step_id,
                    branch_name=ctx.branch_name,
                    base_branch=ctx.base_branch,
                    success=False,
                    error_message=f"Git merge failed: {merge_res.stderr.strip() or merge_res.stdout.strip()}",
                )

            # 4. Read final commit SHA
            head_res = self._run_git(["rev-parse", "HEAD"], cwd=root)
            commit_sha = head_res.stdout.strip() if head_res.returncode == 0 else None

            # 5. Teardown and prune worktree
            self._teardown_worktree(root, wt, ctx.branch_name)

            return WorktreeMergeReport(
                step_id=ctx.step_id,
                branch_name=ctx.branch_name,
                base_branch=ctx.base_branch,
                success=True,
                commit_sha=commit_sha,
                files_changed=tuple(files_changed),
            )

        except Exception as exc:  # noqa: BLE001
            return WorktreeMergeReport(
                step_id=ctx.step_id,
                branch_name=ctx.branch_name,
                base_branch=ctx.base_branch,
                success=False,
                error_message=str(exc),
            )

    def abort_and_cleanup(self, ctx: WorktreeContext) -> WorktreeAbortReport:
        """Discard all changes in the worktree, remove the worktree, and delete the temporary branch."""
        if not ctx.is_active:
            return WorktreeAbortReport(
                step_id=ctx.step_id,
                branch_name=ctx.branch_name,
                worktree_removed=False,
                branch_deleted=False,
                success=True,
            )

        root = ctx.base_repo_path
        wt = ctx.worktree_path

        try:
            wt_removed, branch_deleted = self._teardown_worktree(root, wt, ctx.branch_name)
            return WorktreeAbortReport(
                step_id=ctx.step_id,
                branch_name=ctx.branch_name,
                worktree_removed=wt_removed,
                branch_deleted=branch_deleted,
                success=True,
            )
        except Exception as exc:  # noqa: BLE001
            return WorktreeAbortReport(
                step_id=ctx.step_id,
                branch_name=ctx.branch_name,
                worktree_removed=False,
                branch_deleted=False,
                success=False,
                error_message=str(exc),
            )

    def _teardown_worktree(self, root: Path, worktree_path: Path, branch_name: str) -> tuple[bool, bool]:
        """Helper to remove worktree and delete its temporary branch cleanly."""
        wt_removed = False
        branch_deleted = False

        try:
            res_rm = self._run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=root)
            wt_removed = res_rm.returncode == 0
        except Exception:  # noqa: BLE001, S110
            pass

        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
            wt_removed = True

        try:
            res_br = self._run_git(["branch", "-D", branch_name], cwd=root)
            branch_deleted = res_br.returncode == 0
        except Exception:  # noqa: BLE001, S110
            pass

        try:
            self._run_git(["worktree", "prune"], cwd=root)
        except Exception:  # noqa: BLE001, S110
            pass

        return wt_removed, branch_deleted
