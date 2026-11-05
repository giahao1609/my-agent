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
from .goal_drift_monitor import GoalDriftMonitor
from .handoff_contracts import ImplementationResult, ReviewStatus
from .plan import PlanStepState
from .plan_service import PlanService
from .plan_store import PlanStore
from .security_scanner import SecurityScannerRegistry
from .task_service import TaskService
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
    ) -> ImplementationResult:
        summary = (
            f"[NoOpStepExecutor] Simulated execution of step '{step_title}' "
            f"under role '{step_role.value}'"
            + (f" [repair: {repair_hint}]" if repair_hint else "")
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
    review_history: list[dict[str, Any]] = field(default_factory=list)
    # Per-step telemetry: list of {"step_id", "duration_ms", "attempts", "outcome", ...}
    step_telemetry: list[dict[str, Any]] = field(default_factory=list)

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
            "review_history": self.review_history,
            "step_telemetry": self.step_telemetry,
        }


class WholePlanCoordinator:
    """Coordinates automated multi-step plan execution across specialist agent roles,

    testing gates, security scanners, and verification reviews until the entire plan
    completes.

    Upstream Architecture Alignment (Aider / SWE-agent / MetaGPT Reflexion):
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
        decision_service: DecisionService | None = None,
        max_step_retries: int = 3,
        max_auto_repair_attempts: int = 3,
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
        self._decision_service = decision_service
        self.max_step_retries = max_step_retries
        self.max_auto_repair_attempts = max_auto_repair_attempts

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
            )
        except TypeError:
            imp_res = await self._step_executor.execute_step(
                step_id=step_id,
                step_title=step_title,
                step_role=step_role,
                repair_hint=repair_hint,
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

        # ── 3. Test & security gates ──────────────────────────────────────────
        test_res = await self._test_runner.run_tests(workspace_path, step_id)
        sec_res = self._security_scanner.scan_workspace(workspace_path, step_id)

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
        """Execute an entire plan end-to-end with autonomous self-healing and atomic rollback."""
        root_path = Path(workspace_path)
        plan = await self._plan_service.activate_plan(plan_id)
        steps = await self._plan_store.list_steps(plan_id)

        history: list[dict[str, Any]] = []
        telemetry: list[dict[str, Any]] = []
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        auto_repaired_count = 0
        rollbacks_count = 0

        for step in steps:
            if step.state in (PlanStepState.COMPLETED, PlanStepState.SKIPPED):
                if step.state == PlanStepState.COMPLETED:
                    completed_count += 1
                else:
                    skipped_count += 1
                continue

            # ── 1. Capture transactional pre-step snapshot for atomic rollback ──
            snapshot = self._snapshot_manager.capture_snapshot(root_path, step.step_id)

            # ── 2. Start step ──────────────────────────────────────────────────
            running_step = await self._plan_service.start_step(step.step_id)
            step_start = time.perf_counter()
            attempt = 0
            repair_hint = ""
            review = None
            journal = StepRepairJournal(running_step.step_id)
            rollback_report: RollbackReport | None = None
            drift_detected = False

            # ── 3. Bounded Self-Correction Loop (Upstream Aider / SWE-agent / MetaGPT) ──
            # Initial run: attempt = 0.
            # Up to max_auto_repair_attempts retries: attempt = 1, 2, ..., max_auto_repair_attempts.
            # Total attempts <= max_auto_repair_attempts + 1.
            while attempt <= self.max_auto_repair_attempts:
                review = await self._run_gates_for_step(
                    workspace_path=root_path,
                    step_id=running_step.step_id,
                    step_title=running_step.title,
                    step_role=running_step.assigned_role,
                    repair_hint=repair_hint,
                    step_instruction=getattr(running_step, "instruction", ""),
                    allowed_paths=getattr(running_step, "allowed_paths", None),
                )
                history.append(review.to_dict())

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
                        workspace_root=root_path,
                        drift_detected=drift_detected,
                    )

            # ── 4. Evaluate outcome & commit or rollback ───────────────────────
            duration_ms = round((time.perf_counter() - step_start) * 1000, 1)
            is_approved = review is not None and review.status == ReviewStatus.APPROVED

            if is_approved:
                # Changes verified and accepted — discard snapshot
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
                })
            else:
                # ── ATOMIC ROLLBACK ON EXHAUSTION ─────────────────────────────
                # Revert all modified files, delete newly created files, restore deleted files
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
            await self._plan_service.complete_plan(plan_id)
            await self._task_service.complete_task(plan.task_id)

        return WholePlanExecutionSummary(
            task_id=plan.task_id,
            plan_id=plan_id,
            steps_completed=completed_count,
            steps_failed=failed_count,
            steps_skipped=skipped_count,
            plan_completed=all_finished,
            steps_auto_repaired=auto_repaired_count,
            rollbacks_executed=rollbacks_count,
            review_history=history,
            step_telemetry=telemetry,
        )
