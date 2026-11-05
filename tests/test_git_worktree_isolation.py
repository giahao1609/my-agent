from __future__ import annotations

"""Tests for GitWorktreeManager and step-level Git Worktree Isolation:
- Detection of Git repositories vs non-git directories
- Worktree creation with dedicated branch and cache symlinks (.venv, node_modules)
- Verification of physical isolation: mutations in worktree do not touch base repo
- Merge and cleanup on approved step execution
- Abort and cleanup on failed step execution (zero pollution to base repo)
- End-to-end integration with WholePlanCoordinator
"""

import subprocess
from pathlib import Path

import pytest

from core.agent_role import AgentRole
from core.git_worktree import GitWorktreeManager
from core.handoff_contracts import ImplementationResult, ReviewResult, ReviewStatus
from core.plan_service import PlanService
from core.task_service import TaskService
from core.whole_plan_coordinator import WholePlanCoordinator
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore

# ── Helpers ────────────────────────────────────────────────────────────────────


def _init_git_repo(repo_dir: Path) -> None:
    """Initialize a clean Git repository with an initial commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True, capture_output=True)

    initial_file = repo_dir / "README.md"
    initial_file.write_text("# Project Root\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=str(repo_dir), check=True, capture_output=True)


async def _setup_git_project_env(repo_dir: Path):
    db = repo_dir / ".agents" / "metadata.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    project_store = SQLiteProjectStore(db)
    task_store = SQLiteTaskStore(db)
    plan_store = SQLitePlanStore(db)

    await project_store.initialize()
    await task_store.initialize()
    await plan_store.initialize()

    from core.project import ProjectRecord

    await project_store.create(ProjectRecord(project_id="proj-wt", name="P-WT", workspace_path=str(repo_dir)))
    task_svc = TaskService(project_store=project_store, task_store=task_store)
    plan_svc = PlanService(task_store=task_store, plan_store=plan_store)

    return task_svc, plan_svc, plan_store, task_store


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_is_git_repository_detection(tmp_path: Path) -> None:
    """GitWorktreeManager correctly distinguishes Git repos from plain directories."""
    manager = GitWorktreeManager()
    plain_dir = tmp_path / "plain_dir"
    plain_dir.mkdir()
    assert manager.is_git_repository(plain_dir) is False

    git_dir = tmp_path / "git_repo"
    git_dir.mkdir()
    _init_git_repo(git_dir)
    assert manager.is_git_repository(git_dir) is True


def test_create_worktree_with_cache_symlinks(tmp_path: Path) -> None:
    """Creating a worktree sets up an isolated branch and symlinks dependency caches."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    # Create dummy cache directories in root
    venv_dir = repo_dir / ".venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    nm_dir = repo_dir / "node_modules"
    nm_dir.mkdir()
    (nm_dir / "pkg.json").write_text("{}", encoding="utf-8")

    manager = GitWorktreeManager()
    ctx = manager.create_worktree(repo_dir, "step-abc")

    assert ctx.is_active is True
    assert ctx.step_id == "step-abc"
    assert ctx.worktree_path.is_dir()
    assert ctx.branch_name == "agent-worktree/step-abc"
    assert ctx.base_branch == "main"

    # Verify symlinked caches
    assert ".venv" in ctx.symlinked_caches
    assert "node_modules" in ctx.symlinked_caches
    assert (ctx.worktree_path / ".venv").is_symlink()
    assert (ctx.worktree_path / "node_modules").is_symlink()
    assert (ctx.worktree_path / ".venv" / "pyvenv.cfg").exists()

    # Verify worktree is on the new branch
    res = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=str(ctx.worktree_path),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "agent-worktree/step-abc"

    # Clean up
    abort_rep = manager.abort_and_cleanup(ctx)
    assert abort_rep.success is True
    assert not ctx.worktree_path.exists()


def test_merge_and_cleanup_worktree_on_success(tmp_path: Path) -> None:
    """When a worktree is merged, changes appear on main and the worktree is cleaned up."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    manager = GitWorktreeManager()
    ctx = manager.create_worktree(repo_dir, "step-ok")
    assert ctx.is_active is True

    # Mutate files ONLY inside the worktree
    new_code = ctx.worktree_path / "api.py"
    new_code.write_text("def get_data(): return {'status': 'ok'}\n", encoding="utf-8")

    # Before merge: main must NOT have api.py
    assert not (repo_dir / "api.py").exists()

    # Merge
    report = manager.merge_and_cleanup(ctx, commit_message="feat: add api.py endpoint")
    assert report.success is True
    assert "api.py" in report.files_changed
    assert report.commit_sha is not None

    # After merge: main MUST have api.py
    assert (repo_dir / "api.py").exists()
    assert "get_data" in (repo_dir / "api.py").read_text(encoding="utf-8")

    # Worktree directory and branch are gone
    assert not ctx.worktree_path.exists()
    br_check = subprocess.run(
        ["git", "branch", "--list", ctx.branch_name],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    assert ctx.branch_name not in br_check.stdout


def test_abort_and_cleanup_worktree_on_failure(tmp_path: Path) -> None:
    """When a worktree is aborted, base repo remains untouched with zero pollution."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    manager = GitWorktreeManager()
    ctx = manager.create_worktree(repo_dir, "step-fail")
    assert ctx.is_active is True

    # Mutate files inside the worktree
    broken_code = ctx.worktree_path / "corrupted.py"
    broken_code.write_text("syntax error !!!", encoding="utf-8")

    # Abort
    report = manager.abort_and_cleanup(ctx)
    assert report.success is True
    assert report.worktree_removed is True

    # Base repo remains 100% clean
    assert not (repo_dir / "corrupted.py").exists()
    assert not ctx.worktree_path.exists()

    # Git status on base repo is clean
    st = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    assert st.stdout.strip() == ""


@pytest.mark.asyncio
async def test_whole_plan_coordinator_step_worktree_lifecycle(tmp_path: Path) -> None:
    """WholePlanCoordinator runs steps in isolated worktrees: step 1 merges, step 2 aborts."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    task_svc, plan_svc, plan_store, _task_store = await _setup_git_project_env(repo_dir)

    task = await task_svc.create_task(task_id="task-wt-plan", project_id="proj-wt", objective="Worktree lifecycle")
    await task_svc.start_task(task.task_id)
    await plan_svc.materialize_plan(
        task_id=task.task_id,
        plan_id="plan-wt",
        steps=(
            ("step-1-pass", "Create service module", "instruction"),
            ("step-2-fail", "Create broken module", "instruction"),
        ),
    )

    class _WorktreeExecutor:
        def __init__(self) -> None:
            self.workspaces_executed: list[Path] = []

        async def execute_step(
            self,
            step_id: str,
            step_title: str,
            step_role: AgentRole,
            repair_hint: str = "",
            step_instruction: str = "",
            workspace_path: Path | str | None = None,
        ) -> ImplementationResult:
            target_ws = Path(workspace_path) if workspace_path else repo_dir
            self.workspaces_executed.append(target_ws)

            if step_id == "step-1-pass":
                service_file = target_ws / "service.py"
                service_file.write_text("def run_service(): return True\n", encoding="utf-8")
                return ImplementationResult(
                    step_id=step_id,
                    summary="Created service.py in worktree",
                    success=True,
                    is_mocked=False,
                    execution_evidence={"is_real": True},
                    modified_files=("service.py",),
                )

            # Step 2 writes broken code
            broken_file = target_ws / "broken.py"
            broken_file.write_text("bad syntax", encoding="utf-8")
            return ImplementationResult(
                step_id=step_id,
                summary="Failed execution",
                success=True,
                is_mocked=False,
                execution_evidence={"is_real": True},
                modified_files=("broken.py",),
            )

    class _MultiStepGate:
        def evaluate(self, step_id, implementation_result, test_result, security_result, require_real_execution=True):
            if step_id == "step-1-pass":
                return ReviewResult(
                    step_id=step_id,
                    status=ReviewStatus.APPROVED,
                    summary="Step 1 approved",
                )
            return ReviewResult(
                step_id=step_id,
                status=ReviewStatus.REJECTED,
                summary="Step 2 rejected",
                comments=("Syntax errors in broken.py",),
            )

    executor = _WorktreeExecutor()
    gate = _MultiStepGate()
    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=plan_svc,
        plan_store=plan_store,
        step_executor=executor,
        verification_coordinator=gate,
        enable_worktree_isolation=True,
        max_auto_repair_attempts=1,
    )

    summary = await coordinator.execute_whole_plan("plan-wt", repo_dir)

    # Step 1 completed, Step 2 failed
    assert summary.steps_completed == 1
    assert summary.steps_failed == 1
    assert summary.worktrees_merged == 1
    assert summary.rollbacks_executed == 1

    # Verify execution ran inside dedicated worktrees, NOT on base repo directly
    assert len(executor.workspaces_executed) >= 2
    for ws in executor.workspaces_executed:
        assert ws != repo_dir
        assert ".agents/worktrees" in str(ws)

    # Step 1 changes were merged to main repo
    assert (repo_dir / "service.py").exists()
    assert "run_service" in (repo_dir / "service.py").read_text(encoding="utf-8")

    # Step 2 changes were aborted and NEVER merged to main repo
    assert not (repo_dir / "broken.py").exists()

    # Step 1 telemetry shows worktree_isolated and worktree_merged
    telem_1 = summary.step_telemetry[0]
    assert telem_1["step_id"] == "step-1-pass"
    assert telem_1["worktree_isolated"] is True
    assert telem_1["worktree_merged"] is True

    # Step 2 telemetry shows worktree_isolated and worktree_aborted
    telem_2 = summary.step_telemetry[1]
    assert telem_2["step_id"] == "step-2-fail"
    assert telem_2["worktree_isolated"] is True
    assert telem_2["worktree_aborted"] is True

    # Main repo git status is clean
    git_st = subprocess.run(  # noqa: ASYNC221
        ["git", "status", "--porcelain"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    # Only service.py committed and clean working tree
    assert git_st.stdout.strip() == ""
