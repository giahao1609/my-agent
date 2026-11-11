from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .agent_role import AgentRole
    from .decision_service import DecisionService
    from .status import CapabilityStatus

from .error_reflexion import ErrorReflexionEngine, ReflexionDiagnosis, StepRepairJournal
from .git_worktree import (
    GitWorktreeManager,
    WorktreeAbortReport,
    WorktreeContext,
    WorktreeMergeReport,
)
from .goal_drift_monitor import GoalDriftMonitor
from .handoff_contracts import ImplementationResult, ReviewResult, ReviewStatus
from .plan import PlanStepState
from .plan_service import PlanService
from .plan_store import PlanStore
from .security_scanner import SecurityScannerRegistry
from .task_service import TaskService
from .targeted_test_selector import TargetedTestSelector
from .test_runner import TestRunnerRegistry
from .verification_gate_coordinator import VerificationGateCoordinator
from .workspace_snapshot import RollbackReport, WorkspaceSnapshotManager


class StepExecutor(Protocol):
    """Protocol for executing a single PlanStep via a specialist agent runtime.

    Implement this protocol to connect WholePlanCoordinator to a real agent
    runtime (e.g. CoderRuntimeWorker, SpecialistRouter). The coordinator
    calls ``execute_step`` before running verification gates so that actual
    workspace changes can be verified rather than a synthetic summary.

    Parameters
    ----------
    step_id: Unique identifier of the PlanStep being executed.
    step_title: Human-readable description of the step.
    step_role: AgentRole assigned to this step.
    repair_hint: Non-empty string when this is a repair attempt with context
                 from the previous failed review.
    step_instruction: Detailed instruction/acceptance criteria for the step.
    workspace_path: The isolated working directory for this step (e.g. Git worktree).

    Returns
    -------
    ImplementationResult describing what was done, which files were modified,
    and whether the agent reported success.
    """

    async def execute_step(
        self,
        step_id: str,
        step_title: str,
        step_role: AgentRole,
        repair_hint: str = "",
        step_instruction: str = "",
        workspace_path: Path | str | None = None,
    ) -> ImplementationResult: ...


class _NoOpStepExecutor:
    """Fallback executor used when no real agent runtime is wired in.

    Produces a synthetic ImplementationResult that carries the step metadata
    so that the gate pipeline (tests, security, drift monitor) can still run.
    This matches the previous behaviour and allows unit tests to work without
    a real agent runtime. Production usage should always inject a concrete
    StepExecutor.
    """

    is_real_executor: bool = False

    async def execute_step(
        self,
        step_id: str,
        step_title: str,
        step_role: AgentRole,
        repair_hint: str = "",
        step_instruction: str = "",
        workspace_path: Path | str | None = None,
    ) -> ImplementationResult:
        summary = (
            f"[NoOpStepExecutor] Simulated execution of step '{step_title}' "
            f"under role '{step_role.value}'"
            + (f" [repair: {repair_hint}]" if repair_hint else "")
            + (f" [workspace: {workspace_path}]" if workspace_path else "")
        )
        return ImplementationResult(
            step_id=step_id,
            summary=summary,
            success=True,
            is_mocked=True,
            execution_evidence={
                "step_id": step_id,
                "executor_identity": "_NoOpStepExecutor",
                "is_real": False,
                "evidence_source": "simulation",
                "workspace_path": str(workspace_path) if workspace_path else None,
            },
        )


@dataclass
class WholePlanExecutionSummary:
    task_id: str
    plan_id: str
    steps_completed: int
    steps_failed: int
    steps_skipped: int
    plan_completed: bool
    steps_auto_repaired: int = 0
    rollbacks_executed: int = 0
    worktrees_merged: int = 0
    review_history: list[dict[str, Any]] = field(default_factory=list)
    # Per-step telemetry: list of {"step_id", "duration_ms", "attempts", "outcome", ...}
    step_telemetry: list[dict[str, Any]] = field(default_factory=list)
    execution_state: str = "completed"
    current_step_id: str | None = None
    current_session_id: str | None = None
    action_required: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "steps_skipped": self.steps_skipped,
            "plan_completed": self.plan_completed,
            "steps_auto_repaired": self.steps_auto_repaired,
            "rollbacks_executed": self.rollbacks_executed,
            "worktrees_merged": self.worktrees_merged,
            "review_history": self.review_history,
            "step_telemetry": self.step_telemetry,
            "execution_state": self.execution_state,
            "current_step_id": self.current_step_id,
            "current_session_id": self.current_session_id,
            "action_required": self.action_required,
        }


@dataclass
class StepAdvanceResult:
    step_id: str
    status: str
    execution_state: str
    plan_completed: bool = False
    review: dict[str, Any] | None = None
    next_step_id: str | None = None
    next_session_id: str | None = None
    attempt: int = 1
    repair_hint: str = ""
    action_required: str = ""
    rollback_executed: bool = False
    failure_details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "execution_state": self.execution_state,
            "plan_completed": self.plan_completed,
            "review": self.review,
            "next_step_id": self.next_step_id,
            "next_session_id": self.next_session_id,
            "attempt": self.attempt,
            "repair_hint": self.repair_hint,
            "action_required": self.action_required,
            "rollback_executed": self.rollback_executed,
            "failure_details": list(self.failure_details),
        }


class WholePlanCoordinator:
    """Coordinates automated multi-step plan execution across specialist agent roles,

    testing gates, security scanners, and verification reviews until the entire plan
    completes.

    Upstream Architecture Alignment (worktrunk / Aider / SWE-agent / MetaGPT Reflexion):
    - Physical Git Worktree Isolation: Every step runs in a dedicated, isolated Git worktree
      branch with symlinked dependency caches (.venv, node_modules) for zero workspace contention.
    - Strictly bounded 3-attempt self-healing loop: initial attempt (Attempt 0) +
      up to 3 automated repair retries (Attempts 1, 2, 3). Total attempts per step <= 4.
    - Transactional pre-step snapshot: captures pre-step baseline before execution begins.
    - Reflexion memory journal: preserves failure history and diagnoses across repair attempts
      to actively prevent repeating identical failed patches or invalid syntax.
    - Atomic rollback on exhaustion: if all 3 repair retries fail verification gates,
      the coordinator restores the workspace atomically to the exact pre-step baseline,
      cleans up any created files, marks the step failed, and stops execution safely.
    - Human-in-the-Loop decision escalation: optionally escalates exhausted steps with structured
      diagnostics and three actionable remediation paths.
    """

    def __init__(
        self,
        *,
        task_service: TaskService,
        plan_service: PlanService,
        plan_store: PlanStore,
        step_executor: StepExecutor | None = None,
        test_runner_registry: TestRunnerRegistry | None = None,
        security_scanner_registry: SecurityScannerRegistry | None = None,
        verification_coordinator: VerificationGateCoordinator | None = None,
        goal_drift_monitor: GoalDriftMonitor | None = None,
        snapshot_manager: WorkspaceSnapshotManager | None = None,
        worktree_manager: GitWorktreeManager | None = None,
        decision_service: DecisionService | None = None,
        targeted_test_selector: TargetedTestSelector | None = None,
        agent_run_service: Any | None = None,
        project_id: str | None = None,
        max_step_retries: int = 3,
        max_auto_repair_attempts: int = 3,
        enable_worktree_isolation: bool = True,
    ) -> None:
        self._task_service = task_service
        self._plan_service = plan_service
        self._plan_store = plan_store
        # Wire a real agent executor when available; fall back to no-op for tests.
        self._step_executor: StepExecutor = step_executor or _NoOpStepExecutor()
        self._test_runner = test_runner_registry or TestRunnerRegistry()
        self._security_scanner = security_scanner_registry or SecurityScannerRegistry()
        self._gate_coordinator = verification_coordinator or VerificationGateCoordinator(require_real_execution=True)
        self._goal_drift_monitor = goal_drift_monitor or GoalDriftMonitor()
        self._snapshot_manager = snapshot_manager or WorkspaceSnapshotManager()
        self._worktree_manager = worktree_manager or GitWorktreeManager()
        self._decision_service = decision_service
        self._targeted_test_selector = targeted_test_selector or TargetedTestSelector()
        self._agent_run_service = agent_run_service
        self._project_id = project_id
        self.max_step_retries = max_step_retries
        self.max_auto_repair_attempts = max_auto_repair_attempts
        self.enable_worktree_isolation = enable_worktree_isolation

    def capabilities(self) -> Sequence[CapabilityStatus]:
        """Report truthful capability status for autonomous plan execution."""
        from core.status import Availability, CapabilityStatus

        is_mocked = isinstance(self._step_executor, _NoOpStepExecutor) or not getattr(
            self._step_executor, "is_real_executor", True
        )
        if is_mocked:
            return (
                CapabilityStatus(
                    name="whole_plan_autonomous_execution",
                    state=Availability.MOCKED,
                    reason="Step executor is simulated or _NoOpStepExecutor; produces synthetic results without agent execution",
                    implementation=type(self._step_executor).__name__,
                    provider_or_backend="in_memory_mock",
                    verification_method="executor_type_check",
                    evidence=f"Active step executor {type(self._step_executor).__name__} is non-real or simulated",
                ),
            )
        return (
            CapabilityStatus(
                name="whole_plan_autonomous_execution",
                state=Availability.AVAILABLE,
                reason=None,
                implementation=type(self._step_executor).__name__,
                provider_or_backend="concrete_step_executor",
                verification_method="injected_step_executor",
                evidence=f"Active step executor: {type(self._step_executor).__name__}",
            ),
        )

    def _extract_diagnoses(self, review: Any) -> list[ReflexionDiagnosis]:
        """Extract structured diagnoses from verification review."""
        diagnoses: list[ReflexionDiagnosis] = []
        if hasattr(review, "test_result") and review.test_result is not None:
            tr = review.test_result
            if hasattr(tr, "failures") and tr.failures:
                for f in list(tr.failures)[:3]:
                    diagnoses.append(ErrorReflexionEngine.diagnose_test_failure(str(f)))
            elif hasattr(tr, "failure_details") and tr.failure_details:
                for f in list(tr.failure_details)[:3]:
                    diagnoses.append(ErrorReflexionEngine.diagnose_test_failure(str(f)))

        if hasattr(review, "security_result") and review.security_result is not None:
            sr = review.security_result
            if hasattr(sr, "findings") and sr.findings:
                for f in list(sr.findings)[:2]:
                    diagnoses.append(ErrorReflexionEngine.diagnose_security_finding(f))

        if not diagnoses and hasattr(review, "required_repairs") and review.required_repairs:
            for rep in review.required_repairs:
                if "AssertionError" in rep or "Test failure:" in rep or ".py" in rep or "File " in rep:
                    diagnoses.append(ErrorReflexionEngine.diagnose_test_failure(rep))

        if not diagnoses and hasattr(review, "comments") and review.comments:
            for comm in review.comments:
                if "AssertionError" in comm or ".py" in comm or "File " in comm:
                    diagnoses.append(ErrorReflexionEngine.diagnose_test_failure(comm))

        return diagnoses

    def _package_ast_context(self, diagnosis: ReflexionDiagnosis, workspace_root: Path) -> str:
        """Safely extract AST error context with SensitiveDataGate sanitization."""
        if not diagnosis.target_file or diagnosis.target_file == "unknown_location":
            return ""
        try:
            from tools.error_context_packager import ErrorContextPackager

            packager = ErrorContextPackager(workspace_root=workspace_root)
            full_path = workspace_root / diagnosis.target_file
            if full_path.is_file():
                return packager.package_targeted_error(
                    file_path=diagnosis.target_file,
                    line_number=diagnosis.line_number or 1,
                    error_description=diagnosis.root_cause_summary,
                    context_lines=25,
                )
        except Exception:  # noqa: BLE001, S110
            pass
        return ""

    def _build_repair_hint(self, review: Any) -> str:
        """Construct backwards-compatible repair hint from review results."""
        hints: list[str] = []
        diagnoses = self._extract_diagnoses(review)

        if hasattr(review, "test_result") and review.test_result is not None:
            tr = review.test_result
            if hasattr(tr, "failures") and tr.failures:
                hints.append(f"Test failures: {'; '.join(str(f) for f in list(tr.failures)[:3])}")
            elif hasattr(tr, "failure_details") and tr.failure_details:
                hints.append(f"Test failures: {'; '.join(str(f) for f in list(tr.failure_details)[:3])}")
            if hasattr(tr, "summary") and tr.summary:
                hints.append(tr.summary)

        if hasattr(review, "security_result") and review.security_result is not None:
            sr = review.security_result
            if hasattr(sr, "findings") and sr.findings:
                hints.append(
                    f"Security findings ({len(sr.findings)}): "
                    + "; ".join(f.description for f in list(sr.findings)[:2])
                )

        if hasattr(review, "reasons") and review.reasons:
            hints.extend(list(review.reasons)[:2])
        if hasattr(review, "comments") and review.comments:
            hints.extend(list(review.comments)[:2])
        if hasattr(review, "required_repairs") and review.required_repairs:
            hints.extend(list(review.required_repairs)[:2])

        base_hint = " | ".join(hints) if hints else "Review rejected without specific details"
        if diagnoses:
            reflexion_summary = " | ".join(d.format_brief() for d in diagnoses[:2])
            return f"{base_hint} || Reflexion: {reflexion_summary}"

        return base_hint

    def _build_reflexion_repair_hint(
        self,
        *,
        review: Any,
        attempt: int,
        max_attempts: int,
        journal: StepRepairJournal | None = None,
        diagnoses: list[ReflexionDiagnosis] | None = None,
        workspace_root: Path | None = None,
        drift_detected: bool = False,
    ) -> str:
        """Build a comprehensive, bounded repair prompt with Reflexion memory and anti-looping protection."""
        parts: list[str] = [
            f"[AUTONOMOUS REPAIR ATTEMPT {attempt}/{max_attempts}]",
        ]

        # 1. Base failure hint
        base_hint = self._build_repair_hint(review)
        parts.append(f"Rejection Reason: {base_hint}")

        # 2. Reflexion diagnoses
        if diagnoses:
            diag_str = " | ".join(d.format_brief() for d in diagnoses[:2])
            parts.append(f"Reflexion Diagnosis: {diag_str}")

        # 3. Anti-looping advisory from past failed attempts
        if journal:
            anti_looping = journal.build_anti_looping_section()
            if anti_looping:
                parts.append(anti_looping)

        # 4. Critical drift & anti-tampering warning
        if drift_detected:
            parts.append(
                "[CRITICAL DRIFT GUARD] Goal drift was detected in the previous attempt. "
                "Do NOT edit test files or modify files outside authorized scope to force tests to pass. "
                "Ensure your fix is strictly applied to the source implementation."
            )

        # 5. Surgical AST code context if target is identified
        if diagnoses and workspace_root:
            ast_ctx = self._package_ast_context(diagnoses[0], workspace_root)
            if ast_ctx:
                parts.append(f"\n[SURGICAL CODE CONTEXT]\n{ast_ctx}")

        return "\n\n".join(parts)

    async def _run_gates_for_step(
        self,
        workspace_path: Path,
        step_id: str,
        step_title: str,
        step_role: Any,
        repair_hint: str = "",
        modified_files: Sequence[str] = (),
        allowed_paths: Sequence[str] | None = None,
        step_instruction: str = "",
    ) -> Any:
        """Invoke the step executor then run all verification gates.

        Flow:
            1. Call ``self._step_executor.execute_step()`` — real agent runtime performs changes.
            2. Run ``GoalDriftMonitor`` against modified files reported by executor.
            3. Run ``TestRunnerRegistry`` and ``SecurityScannerRegistry``.
            4. Evaluate all results through ``VerificationGateCoordinator``.
        """
        # ── 1. Execute via wired agent runtime ───────────────────────────────
        try:
            imp_res = await self._step_executor.execute_step(
                step_id=step_id,
                step_title=step_title,
                step_role=step_role,
                repair_hint=repair_hint,
                step_instruction=step_instruction,
                workspace_path=workspace_path,
            )
        except TypeError:
            try:
                imp_res = await self._step_executor.execute_step(
                    step_id=step_id,
                    step_title=step_title,
                    step_role=step_role,
                    repair_hint=repair_hint,
                    step_instruction=step_instruction,
                )
            except TypeError:
                imp_res = await self._step_executor.execute_step(
                    step_id=step_id,
                    step_title=step_title,
                    step_role=step_role,
                    repair_hint=repair_hint,
                )

        # ── 1b. Check if executor reported WAITING_FOR_HOST ─────────────────
        evidence = getattr(imp_res, "execution_evidence", {}) or {}
        if evidence.get("runtime_id") == "external_host_bridge" or "WAITING_FOR_HOST" in evidence.get("notes", ""):
            return ReviewResult(
                step_id=step_id,
                status=ReviewStatus.WAITING_FOR_HOST,
                summary=imp_res.summary,
                comments=("Waiting for external host coder worker execution via next_coder_command",),
            )

        # ── 2. Goal drift check using files reported by executor ──────────────
        if hasattr(imp_res, "modified_files") and imp_res.modified_files:
            reported_files: Sequence[str] = list(imp_res.modified_files)
        elif hasattr(imp_res, "changed_files") and imp_res.changed_files:
            reported_files = [getattr(f, "path", str(f)) for f in imp_res.changed_files]
        else:
            reported_files = list(modified_files)

        drift_res = self._goal_drift_monitor.evaluate_drift(
            step_id=step_id,
            modified_files=reported_files,
            allowed_paths=allowed_paths,
            original_goal=step_title,
        )
        if drift_res.drift_detected:
            # Propagate drift signal into the implementation result summary
            imp_res = ImplementationResult(
                step_id=imp_res.step_id,
                summary=f"{imp_res.summary} | DRIFT: {drift_res.summary}",
                success=False,
                is_mocked=getattr(imp_res, "is_mocked", False),
                execution_evidence=getattr(imp_res, "execution_evidence", {}),
                modified_files=getattr(imp_res, "modified_files", ()),
                created_files=getattr(imp_res, "created_files", ()),
                deleted_files=getattr(imp_res, "deleted_files", ()),
            )

        # ── 3. Test & security gates with Intelligent Targeted Selection ──────
        target_selection = self._targeted_test_selector.select_tests(
            workspace_path,
            reported_files,
            project_id=self._project_id,
        )
        try:
            test_res = await self._test_runner.run_tests(
                workspace_path,
                step_id,
                target_files=target_selection.selected_tests if target_selection.is_targeted else None,
                timeout_seconds=60.0,
            )
        except TypeError:
            test_res = await self._test_runner.run_tests(workspace_path, step_id)

        sec_targets = list(reported_files) if reported_files else None
        sec_res = self._security_scanner.scan_workspace(
            workspace_path,
            step_id,
            target_files=sec_targets,
        )

        # ── 4. Final gate evaluation ──────────────────────────────────────────
        try:
            return self._gate_coordinator.evaluate(
                step_id=step_id,
                implementation_result=imp_res,
                test_result=test_res,
                security_result=sec_res,
                require_real_execution=True,
            )
        except TypeError:
            return self._gate_coordinator.evaluate(
                step_id=step_id,
                implementation_result=imp_res,
                test_result=test_res,
                security_result=sec_res,
            )

    async def execute_whole_plan(
        self,
        plan_id: str,
        workspace_path: Path | str,
    ) -> WholePlanExecutionSummary:
        """Execute an entire plan end-to-end with autonomous self-healing, Git worktree isolation, and atomic rollback."""
        root_path = Path(workspace_path).resolve()
        plan = await self._plan_service.activate_plan(plan_id)
        steps = await self._plan_store.list_steps(plan_id)

        history: list[dict[str, Any]] = []
        telemetry: list[dict[str, Any]] = []
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        auto_repaired_count = 0
        rollbacks_count = 0
        worktrees_merged_count = 0

        for step in steps:
            if step.state in (PlanStepState.COMPLETED, PlanStepState.SKIPPED):
                if step.state == PlanStepState.COMPLETED:
                    completed_count += 1
                else:
                    skipped_count += 1
                continue

            # ── 1. Create isolated Git worktree if repository & enabled ──────
            worktree_ctx: WorktreeContext | None = None
            if self.enable_worktree_isolation and self._worktree_manager.is_git_repository(root_path):
                worktree_ctx = self._worktree_manager.create_worktree(root_path, step.step_id)

            step_workspace = worktree_ctx.worktree_path if (worktree_ctx and worktree_ctx.is_active) else root_path

            # ── 2. Capture transactional pre-step snapshot for atomic rollback ──
            snapshot = self._snapshot_manager.capture_snapshot(step_workspace, step.step_id)

            # ── 3. Start step ──────────────────────────────────────────────────
            running_step = await self._plan_service.start_step(step.step_id)
            step_start = time.perf_counter()
            attempt = 0
            repair_hint = ""
            review = None
            journal = StepRepairJournal(running_step.step_id)
            rollback_report: RollbackReport | None = None
            merge_report: WorktreeMergeReport | None = None
            abort_report: WorktreeAbortReport | None = None
            drift_detected = False

            # ── 4. Bounded Self-Correction Loop (Upstream Aider / SWE-agent / MetaGPT) ──
            # Initial run: attempt = 0.
            # Up to max_auto_repair_attempts retries: attempt = 1, 2, ..., max_auto_repair_attempts.
            # Total attempts <= max_auto_repair_attempts + 1.
            while attempt <= self.max_auto_repair_attempts:
                review = await self._run_gates_for_step(
                    workspace_path=step_workspace,
                    step_id=running_step.step_id,
                    step_title=running_step.title,
                    step_role=running_step.assigned_role,
                    repair_hint=repair_hint,
                    step_instruction=getattr(running_step, "instruction", ""),
                    allowed_paths=getattr(running_step, "allowed_paths", None),
                )
                history.append(review.to_dict())

                if review.status == ReviewStatus.WAITING_FOR_HOST:
                    session_id = None
                    if self._agent_run_service is not None:
                        step_runs = await self._agent_run_service.list_runs_for_step(running_step.step_id)
                        if step_runs:
                            session_id = step_runs[-1].session_id
                    return WholePlanExecutionSummary(
                        task_id=plan.task_id,
                        plan_id=plan_id,
                        steps_completed=completed_count,
                        steps_failed=failed_count,
                        steps_skipped=skipped_count,
                        plan_completed=False,
                        steps_auto_repaired=auto_repaired_count,
                        rollbacks_executed=rollbacks_count,
                        worktrees_merged=worktrees_merged_count,
                        review_history=history,
                        step_telemetry=telemetry,
                        execution_state="WAITING_FOR_HOST",
                        current_step_id=running_step.step_id,
                        current_session_id=session_id,
                        action_required=(
                            f"Step '{running_step.title}' is waiting for host execution. "
                            f"Consume commands using next_coder_command('{session_id}'), apply edits, "
                            f"then call advance_plan_step('{running_step.step_id}') to verify and proceed."
                        ),
                    )

                if review.status == ReviewStatus.APPROVED:
                    break  # Step passed — exit repair loop

                attempt += 1
                if attempt <= self.max_auto_repair_attempts:
                    # Gate rejected — prepare Reflexion diagnosis & journal for next repair attempt
                    diagnoses = self._extract_diagnoses(review)
                    drift_detected = "DRIFT:" in getattr(review, "summary", "") or any(
                        "drift" in str(r).lower() for r in (getattr(review, "reasons", ()) or ())
                    )
                    journal.record_attempt(
                        attempt_number=attempt,
                        diagnosis=diagnoses[0] if diagnoses else None,
                        rejection_reasons=tuple(getattr(review, "reasons", ()) or ()),
                        comments=tuple(getattr(review, "comments", ()) or ()),
                        drift_detected=drift_detected,
                    )
                    repair_hint = self._build_reflexion_repair_hint(
                        review=review,
                        attempt=attempt,
                        max_attempts=self.max_auto_repair_attempts,
                        journal=journal,
                        diagnoses=diagnoses,
                        workspace_root=step_workspace,
                        drift_detected=drift_detected,
                    )

            # ── 5. Evaluate outcome & commit or rollback ───────────────────────
            duration_ms = round((time.perf_counter() - step_start) * 1000, 1)
            is_approved = review is not None and review.status == ReviewStatus.APPROVED

            if is_approved:
                # Changes verified and accepted — merge worktree if active
                if worktree_ctx and worktree_ctx.is_active:
                    merge_report = self._worktree_manager.merge_and_cleanup(worktree_ctx)
                    if merge_report.success:
                        worktrees_merged_count += 1

                await self._plan_service.complete_step(step.step_id)
                completed_count += 1
                if attempt > 0:
                    auto_repaired_count += 1
                outcome = "approved"
                telemetry.append({
                    "step_id": running_step.step_id,
                    "step_title": running_step.title,
                    "duration_ms": duration_ms,
                    "attempts": attempt,
                    "outcome": outcome,
                    "repair_exhausted": False,
                    "auto_repaired": attempt > 0,
                    "repair_attempts_run": attempt,
                    "worktree_isolated": worktree_ctx is not None and worktree_ctx.is_active,
                    "worktree_merged": merge_report is not None and merge_report.success,
                    "worktree_merge_report": merge_report.to_dict() if merge_report else None,
                })
            else:
                # ── ATOMIC ROLLBACK / WORKTREE ABORT ON EXHAUSTION ─────────────
                if worktree_ctx and worktree_ctx.is_active:
                    abort_report = self._worktree_manager.abort_and_cleanup(worktree_ctx)

                rollback_report = self._snapshot_manager.rollback_snapshot(snapshot)
                rollbacks_count += 1
                await self._plan_service.fail_step(step.step_id)
                failed_count += 1
                outcome = "failed"
                telemetry.append({
                    "step_id": running_step.step_id,
                    "step_title": running_step.title,
                    "duration_ms": duration_ms,
                    "attempts": attempt,
                    "outcome": outcome,
                    "repair_exhausted": True,
                    "repair_attempts_run": attempt - 1 if attempt > 0 else 0,
                    "worktree_isolated": worktree_ctx is not None and worktree_ctx.is_active,
                    "worktree_aborted": abort_report is not None and abort_report.success,
                    "rollback_executed": rollback_report is not None and rollback_report.success,
                    "rollback_report": rollback_report.to_dict() if rollback_report else None,
                })

                # Escalation via DecisionService if configured
                if self._decision_service is not None:
                    try:
                        from core.decision import DecisionOption, DecisionSeverity

                        await self._decision_service.create_decision(
                            task_id=plan.task_id,
                            step_id=running_step.step_id,
                            plan_id=plan_id,
                            severity=DecisionSeverity.HIGH,
                            prompt=(
                                f"Step '{running_step.title}' failed verification after "
                                f"{self.max_auto_repair_attempts} automated repair attempts. "
                                f"Workspace was atomically rolled back to pre-step baseline."
                            ),
                            options=(
                                DecisionOption(
                                    option_id="opt_inspect",
                                    title="Inspect Diagnostics and Retry Manually",
                                    description="Review failure logs and reflexion diagnoses to manually patch the issue.",
                                ),
                                DecisionOption(
                                    option_id="opt_skip",
                                    title="Skip Failed Step",
                                    description="Mark this step skipped and proceed with downstream steps.",
                                ),
                                DecisionOption(
                                    option_id="opt_abort",
                                    title="Abort Plan",
                                    description="Cancel remaining plan execution and preserve rolled-back state.",
                                ),
                            ),
                        )
                    except Exception:  # noqa: BLE001, S110
                        pass

                break  # Stop execution on failed step

        # Complete plan if all steps finished
        final_steps = await self._plan_store.list_steps(plan_id)
        all_finished = all(s.state in (PlanStepState.COMPLETED, PlanStepState.SKIPPED) for s in final_steps)

        if all_finished:
            # Final Verification Gate: Full Regression suite
            try:
                final_test_res = await self._test_runner.run_tests(
                    root_path,
                    step_id="final_plan_verification",
                    target_files=None,
                    timeout_seconds=240.0,
                )
            except TypeError:
                final_test_res = await self._test_runner.run_tests(
                    root_path,
                    step_id="final_plan_verification",
                )
            final_sec_res = self._security_scanner.scan_workspace(
                root_path,
                step_id="final_plan_verification",
            )
            final_gate_passed = (
                final_test_res.success
                and (final_test_res.failed_tests == 0)
                and getattr(final_test_res, "execution_success", True)
                and final_sec_res.passed_gate
            )
            if final_gate_passed:
                await self._plan_service.complete_plan(plan_id)
                await self._task_service.complete_task(plan.task_id)
            else:
                return WholePlanExecutionSummary(
                    task_id=plan.task_id,
                    plan_id=plan_id,
                    steps_completed=completed_count,
                    steps_failed=failed_count + 1,
                    steps_skipped=skipped_count,
                    plan_completed=False,
                    steps_auto_repaired=auto_repaired_count,
                    rollbacks_executed=rollbacks_count,
                    worktrees_merged=worktrees_merged_count,
                    review_history=history,
                    step_telemetry=telemetry,
                    execution_state="FINAL_GATE_FAILED",
                    action_required=f"Final Full Regression failed: {final_test_res.failure_details or final_sec_res.summary}",
                )

        return WholePlanExecutionSummary(
            task_id=plan.task_id,
            plan_id=plan_id,
            steps_completed=completed_count,
            steps_failed=failed_count,
            steps_skipped=skipped_count,
            plan_completed=all_finished,
            steps_auto_repaired=auto_repaired_count,
            rollbacks_executed=rollbacks_count,
            worktrees_merged=worktrees_merged_count,
            review_history=history,
            step_telemetry=telemetry,
            execution_state="completed" if all_finished else "in_progress",
        )

    async def advance_step(
        self,
        step_id: str,
        workspace_path: Path | str,
        execution_evidence: dict[str, Any] | None = None,
    ) -> StepAdvanceResult:
        """Evaluate verification gate on host-submitted evidence, advance step, or trigger auto-repair."""
        step = await self._plan_store.get_step(step_id)
        if step is None:
            raise KeyError(f"unknown plan step: {step_id}")

        plan = await self._plan_store.get_plan(step.plan_id)
        if plan is None:
            raise KeyError(f"unknown plan: {step.plan_id}")

        if step.state == PlanStepState.COMPLETED:
            return StepAdvanceResult(
                step_id=step_id,
                status="step_already_completed",
                execution_state="ALREADY_COMPLETED",
                plan_completed=False,
                review={"status": "approved", "comments": ["Step already completed."]},
                action_required="Step is already completed. No replay allowed.",
            )

        # 2. Reject inactive steps (PENDING, DRAFT, FAILED)
        if step.state != PlanStepState.RUNNING:
            return StepAdvanceResult(
                step_id=step_id,
                status="step_rejected",
                execution_state="INVALID_STEP_STATE",
                plan_completed=False,
                review={
                    "status": "rejected",
                    "comments": [f"Step '{step_id}' is in state '{step.state.value}', not RUNNING. Cannot advance an inactive step."],
                    "required_repairs": [f"Start step '{step_id}' before submitting execution evidence."],
                },
                action_required=f"Step '{step_id}' is in '{step.state.value}' state. Only active RUNNING steps can be advanced.",
            )

        root_path = Path(workspace_path).resolve()
        evidence = dict(execution_evidence) if execution_evidence else {}

        # 3. Reject evidence submitted for a different step_id
        evidence_step_id = evidence.get("step_id")
        if evidence_step_id and str(evidence_step_id) != str(step_id):
            return StepAdvanceResult(
                step_id=step_id,
                status="step_rejected",
                execution_state="EVIDENCE_STEP_MISMATCH",
                plan_completed=False,
                review={
                    "status": "rejected",
                    "comments": [f"Evidence step_id mismatch: evidence is for '{evidence_step_id}', expected '{step_id}'."],
                    "required_repairs": [f"Submit evidence belonging to active step '{step_id}'."],
                },
                action_required=f"Evidence step_id '{evidence_step_id}' does not match target step '{step_id}'.",
            )

        # 3b. Reject evidence submitted for a different project_id
        evidence_project_id = evidence.get("project_id")
        target_project_id = self._project_id
        if not target_project_id and hasattr(self, "_task_service") and self._task_service:
            task = await self._task_service.get_task(plan.task_id)
            if task:
                target_project_id = task.project_id

        if evidence_project_id and target_project_id and str(evidence_project_id) != str(target_project_id):
            return StepAdvanceResult(
                step_id=step_id,
                status="step_rejected",
                execution_state="PROJECT_MISMATCH",
                plan_completed=False,
                review={
                    "status": "rejected",
                    "comments": [
                        f"Evidence project_id mismatch: evidence is for project '{evidence_project_id}', expected '{target_project_id}'."
                    ],
                    "required_repairs": [
                        f"Submit evidence belonging to project '{target_project_id}'."
                    ],
                },
                action_required=f"Evidence project_id '{evidence_project_id}' does not match target project '{target_project_id}'.",
            )

        # 4. Enforce session binding: evidence must match active authorized session
        if self._agent_run_service is not None:
            runs = await self._agent_run_service.list_runs_for_step(step_id)
            active_runs = [r for r in runs if not r.is_terminal]
            authorized_sessions = {r.session_id for r in active_runs if r.session_id}

            if not authorized_sessions:
                return StepAdvanceResult(
                    step_id=step_id,
                    status="step_rejected",
                    execution_state="NO_AUTHORIZED_SESSION",
                    plan_completed=False,
                    review={
                        "status": "rejected",
                        "comments": [
                            f"Session binding failure: step '{step_id}' has no active authorized execution session. Cannot accept host-submitted evidence."
                        ],
                        "required_repairs": [
                            f"Initialize an authorized execution session for step '{step_id}' before advancing."
                        ],
                    },
                    action_required=f"No active authorized execution session exists for step '{step_id}'.",
                )

            submitted_session = evidence.get("session_id")
            if not submitted_session or submitted_session not in authorized_sessions:
                return StepAdvanceResult(
                    step_id=step_id,
                    status="step_rejected",
                    execution_state="SESSION_MISMATCH",
                    plan_completed=False,
                    review={
                        "status": "rejected",
                        "comments": [
                            (
                                f"Session binding failure: evidence session '{submitted_session}' "
                                f"does not match authorized active session(s) {sorted(authorized_sessions)} for step '{step_id}'."
                            )
                        ],
                        "required_repairs": [
                            f"Submit execution evidence with valid active session_id in {sorted(authorized_sessions)}."
                        ],
                    },
                    action_required=f"Submitted session '{submitted_session}' is unauthorized or stale for step '{step_id}'.",
                )

        # 1. Resolve modified files from evidence or agent run service
        modified_files: list[str] = list(
            evidence.get("files_changed") or evidence.get("modified_files") or []
        )
        if not modified_files and self._agent_run_service is not None:
            runs = await self._agent_run_service.list_runs_for_step(step_id)
            for r in runs:
                if r.result and r.result.changed_files:
                    modified_files.extend(f.path for f in r.result.changed_files)
                elif r.metadata and "files_changed" in r.metadata:
                    modified_files.extend(r.metadata["files_changed"])

        # 2. Check Goal Drift
        drift_res = self._goal_drift_monitor.evaluate_drift(
            step_id=step_id,
            modified_files=modified_files,
            allowed_paths=getattr(step, "allowed_paths", None),
            original_goal=step.title,
        )

        # 3. Targeted test selection
        target_selection = self._targeted_test_selector.select_tests(
            root_path,
            modified_files,
            project_id=self._project_id or plan.task_id,
        )

        # 4. Run targeted tests and security scanner
        try:
            test_res = await self._test_runner.run_tests(
                root_path,
                step_id,
                target_files=target_selection.selected_tests if target_selection.is_targeted else None,
                timeout_seconds=60.0,
            )
        except TypeError:
            test_res = await self._test_runner.run_tests(root_path, step_id)

        sec_targets = list(modified_files) if modified_files else None
        sec_res = self._security_scanner.scan_workspace(
            root_path,
            step_id,
            target_files=sec_targets,
        )

        # 5. Evaluate Verification Gate
        is_real = bool(evidence.get("is_real", False))
        imp_res = ImplementationResult(
            step_id=step_id,
            summary=f"Host execution for step '{step.title}'"
            + (f" | DRIFT: {drift_res.summary}" if drift_res.drift_detected else ""),
            modified_files=tuple(modified_files),
            success=not drift_res.drift_detected,
            is_mocked=not is_real,
            execution_evidence=evidence,
        )
        review = self._gate_coordinator.evaluate(
            step_id=step_id,
            implementation_result=imp_res,
            test_result=test_res,
            security_result=sec_res,
            require_real_execution=True,
        )

        # 6. Evaluate Outcome
        if review.status == ReviewStatus.APPROVED:
            # Mark step complete in PlanService
            await self._plan_service.complete_step(step_id)
            if self._agent_run_service is not None:
                from .agent_work_result import AgentWorkResult
                runs = await self._agent_run_service.list_runs_for_step(step_id)
                for r in runs:
                    if not r.is_terminal:
                        work_res = AgentWorkResult(
                            run_id=r.run_id,
                            status="completed",
                            summary=f"Approved step execution for {step_id}",
                            metadata=evidence,
                        )
                        await self._agent_run_service.complete_run(r.run_id, work_res)

            # Check if there is a next pending step
            steps = await self._plan_store.list_steps(plan.plan_id)
            pending_steps = [s for s in steps if s.state == PlanStepState.PENDING]
            if pending_steps:
                next_step = pending_steps[0]
                running_next = await self._plan_service.start_step(next_step.step_id)
                next_imp = await self._step_executor.execute_step(
                    step_id=running_next.step_id,
                    step_title=running_next.title,
                    step_role=running_next.assigned_role,
                    step_instruction=getattr(running_next, "instruction", ""),
                    workspace_path=root_path,
                )
                next_evidence = getattr(next_imp, "execution_evidence", {}) or {}
                next_session_id = next_evidence.get("session_id")
                return StepAdvanceResult(
                    step_id=step_id,
                    status="step_approved",
                    execution_state="WAITING_FOR_HOST",
                    plan_completed=False,
                    review=review.to_dict(),
                    next_step_id=running_next.step_id,
                    next_session_id=next_session_id,
                    action_required=(
                        f"Step '{step.title}' approved. Step '{running_next.title}' waiting for host execution. "
                        f"Consume commands using next_coder_command('{next_session_id}')."
                    ),
                )

            # All steps completed — Final Verification Gate (Full Regression)
            try:
                final_test_res = await self._test_runner.run_tests(
                    root_path,
                    step_id="final_plan_verification",
                    target_files=None,
                    timeout_seconds=240.0,
                )
            except TypeError:
                final_test_res = await self._test_runner.run_tests(
                    root_path,
                    step_id="final_plan_verification",
                )
            final_sec_res = self._security_scanner.scan_workspace(
                root_path,
                step_id="final_plan_verification",
            )
            if (
                final_test_res.success
                and final_test_res.failed_tests == 0
                and getattr(final_test_res, "execution_success", True)
                and final_sec_res.passed_gate
            ):
                await self._plan_service.complete_plan(plan.plan_id)
                await self._task_service.complete_task(plan.task_id)
                return StepAdvanceResult(
                    step_id=step_id,
                    status="plan_completed",
                    execution_state="COMPLETED",
                    plan_completed=True,
                    review=review.to_dict(),
                    action_required="All steps approved and Final Regression verified. Plan complete.",
                )
            else:
                return StepAdvanceResult(
                    step_id=step_id,
                    status="final_verification_failed",
                    execution_state="FINAL_GATE_FAILED",
                    plan_completed=False,
                    review=review.to_dict(),
                    failure_details=final_test_res.failure_details,
                    action_required=f"Final Full Regression failed: {final_test_res.failure_details or final_sec_res.summary}",
                )

        else:
            # Gate rejected — Check auto-repair attempts
            repair_hint = self._build_repair_hint(review)
            # Send repair hint to active session if available
            if hasattr(self._step_executor, "_coder_stack") and evidence.get("session_id"):
                session_id = evidence["session_id"]
                from .context import ExecutionContext
                ctx = ExecutionContext(
                    workspace_id=str(root_path),
                    agent_id="coder",
                    task_id=plan.task_id,
                    project_id=self._project_id or plan.task_id,
                )
                try:
                    await self._step_executor._coder_stack.orchestrator.send(
                        session_id,
                        f"Step '{step.title}' rejected. Repair required:\n{repair_hint}",
                        ctx,
                    )
                except Exception:
                    pass

            return StepAdvanceResult(
                step_id=step_id,
                status="step_rejected",
                execution_state="WAITING_FOR_HOST",
                attempt=1,
                repair_hint=repair_hint,
                review=review.to_dict(),
                action_required=f"Step rejected by verification gate. Please apply fixes and call advance_plan_step again. Hint: {repair_hint}",
            )
