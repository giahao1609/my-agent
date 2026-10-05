from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from .error_reflexion import ErrorReflexionEngine
from .goal_drift_monitor import GoalDriftMonitor
from .handoff_contracts import ImplementationResult, ReviewStatus
from .plan import PlanRecord, PlanState, PlanStepRecord, PlanStepState
from .plan_service import PlanService
from .plan_store import PlanStore
from .security_scanner import SecurityScannerRegistry
from .task_service import TaskService
from .test_runner import TestRunnerRegistry
from .verification_gate_coordinator import VerificationGateCoordinator


class StepExecutor(Protocol):
    """Protocol for executing a single PlanStep via a specialist agent runtime.

    Implement this protocol to connect WholePlanCoordinator to a real agent
    runtime (e.g. CoderRuntimeWorker, SpecialistRouter).  The coordinator
    calls ``execute_step`` before running verification gates so that actual
    workspace changes can be verified rather than a synthetic summary.

    Parameters
    ----------
    step_id:       Unique identifier of the PlanStep being executed.
    step_title:    Human-readable description of the step.
    step_role:     AgentRole assigned to this step.
    repair_hint:   Non-empty string when this is a repair attempt with context
                   from the previous failed review.

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
    ) -> ImplementationResult: ...


class _NoOpStepExecutor:
    """Fallback executor used when no real agent runtime is wired in.

    Produces a synthetic ImplementationResult that carries the step metadata
    so that the gate pipeline (tests, security, drift monitor) can still run.
    This matches the previous behaviour and allows unit tests to work without
    a real agent runtime.  Production usage should always inject a concrete
    StepExecutor.
    """

    async def execute_step(
        self,
        step_id: str,
        step_title: str,
        step_role: AgentRole,
        repair_hint: str = "",
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
    review_history: list[dict[str, Any]] = field(default_factory=list)
    # Per-step telemetry: list of {"step_id", "duration_ms", "attempts", "outcome"}
    step_telemetry: list[dict[str, Any]] = field(default_factory=list)


class WholePlanCoordinator:
    """Coordinates automated multi-step plan execution across specialist agent roles,

    testing gates, security scanners, and verification reviews until the entire plan
    completes. Includes a self-correction loop that automatically retries failed steps
    before escalating to a human reviewer.

    Agent Wiring
    ------------
    Inject a concrete ``StepExecutor`` implementation to connect real agent
    runtimes.  When no executor is provided, ``_NoOpStepExecutor`` is used as
    a safe fallback that produces synthetic ImplementationResults — suitable
    for tests but NOT for production use.

    Example::

        coordinator = WholePlanCoordinator(
            task_service=task_svc,
            plan_service=plan_svc,
            plan_store=store,
            step_executor=MyCoderRuntimeExecutor(session_manager),
        )
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
        max_step_retries: int = 3,
        max_auto_repair_attempts: int = 2,
    ) -> None:
        self._task_service = task_service
        self._plan_service = plan_service
        self._plan_store = plan_store
        # Wire a real agent executor when available; fall back to no-op for tests.
        self._step_executor: StepExecutor = step_executor or _NoOpStepExecutor()
        self._test_runner = test_runner_registry or TestRunnerRegistry()
        self._security_scanner = security_scanner_registry or SecurityScannerRegistry()
        self._gate_coordinator = verification_coordinator or VerificationGateCoordinator()
        self._goal_drift_monitor = goal_drift_monitor or GoalDriftMonitor()
        self.max_step_retries = max_step_retries
        self.max_auto_repair_attempts = max_auto_repair_attempts

    def _build_repair_hint(self, review: Any) -> str:
        """Construct an actionable, diagnostic repair hint from a gate review result using ErrorReflexionEngine."""
        hints: list[str] = []
        diagnoses = []

        if hasattr(review, "test_result") and review.test_result is not None:
            tr = review.test_result
            if hasattr(tr, "failures") and tr.failures:
                for f in list(tr.failures)[:3]:
                    diagnoses.append(ErrorReflexionEngine.diagnose_test_failure(str(f)))
                hints.append(f"Test failures: {'; '.join(str(f) for f in list(tr.failures)[:3])}")
            if hasattr(tr, "summary") and tr.summary:
                hints.append(tr.summary)

        if hasattr(review, "security_result") and review.security_result is not None:
            sr = review.security_result
            if hasattr(sr, "findings") and sr.findings:
                for f in list(sr.findings)[:2]:
                    diagnoses.append(ErrorReflexionEngine.diagnose_security_finding(f))
                hints.append(
                    f"Security findings ({len(sr.findings)}): "
                    + "; ".join(f.description for f in list(sr.findings)[:2])
                )

        if hasattr(review, "reasons") and review.reasons:
            hints.extend(list(review.reasons)[:2])

        base_hint = " | ".join(hints) if hints else "Review rejected without specific details"
        if diagnoses:
            reflexion_summary = " | ".join(d.format_brief() for d in diagnoses[:2])
            return f"{base_hint} || Reflexion: {reflexion_summary}"

        return base_hint

    async def _run_gates_for_step(
        self,
        workspace_path: Path,
        step_id: str,
        step_title: str,
        step_role: Any,
        repair_hint: str = "",
        modified_files: Sequence[str] = (),
        allowed_paths: Sequence[str] | None = None,
    ) -> Any:
        """Invoke the step executor then run all verification gates.

        Flow:
            1. Call ``self._step_executor.execute_step()`` — this is where a real
               agent runtime performs workspace changes.
            2. Run ``GoalDriftMonitor`` against modified files reported by the
               executor.
            3. Run ``TestRunnerRegistry`` and ``SecurityScannerRegistry``.
            4. Evaluate all results through ``VerificationGateCoordinator``.
        """
        # ── 1. Execute via wired agent runtime ───────────────────────────────
        imp_res = await self._step_executor.execute_step(
            step_id=step_id,
            step_title=step_title,
            step_role=step_role,
            repair_hint=repair_hint,
        )

        # ── 2. Goal drift check using files reported by executor ──────────────
        reported_files: Sequence[str] = (
            [f.path for f in imp_res.changed_files]
            if hasattr(imp_res, "changed_files") and imp_res.changed_files
            else list(modified_files)
        )
        drift_res = self._goal_drift_monitor.evaluate_drift(
            step_id=step_id,
            modified_files=reported_files,
            allowed_paths=allowed_paths,
            original_goal=step_title,
        )
        if drift_res.drift_detected:
            # Propagate drift signal into the implementation result summary
            # without discarding the executor's success flag outright —
            # the gate coordinator will weigh all signals together.
            imp_res = ImplementationResult(
                step_id=imp_res.step_id,
                summary=f"{imp_res.summary} | DRIFT: {drift_res.summary}",
                success=False,
                changed_files=imp_res.changed_files if hasattr(imp_res, "changed_files") else (),
            )

        # ── 3. Test & security gates ──────────────────────────────────────────
        test_res = await self._test_runner.run_tests(workspace_path, step_id)
        sec_res = self._security_scanner.scan_workspace(workspace_path, step_id)

        # ── 4. Final gate evaluation ──────────────────────────────────────────
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
        root_path = Path(workspace_path)
        plan = await self._plan_service.activate_plan(plan_id)
        steps = await self._plan_store.list_steps(plan_id)

        history: list[dict[str, Any]] = []
        telemetry: list[dict[str, Any]] = []
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        auto_repaired_count = 0

        for step in steps:
            if step.state in (PlanStepState.COMPLETED, PlanStepState.SKIPPED):
                if step.state == PlanStepState.COMPLETED:
                    completed_count += 1
                else:
                    skipped_count += 1
                continue

            # ── Start step ───────────────────────────────────────────────────
            running_step = await self._plan_service.start_step(step.step_id)
            step_start = time.perf_counter()
            attempt = 0
            repair_hint = ""
            review = None

            # ── Self-correction loop ──────────────────────────────────────────
            # Run up to 1 (initial) + max_auto_repair_attempts times before
            # escalating the failure to a human reviewer.
            while attempt <= self.max_auto_repair_attempts:
                review = await self._run_gates_for_step(
                    workspace_path=root_path,
                    step_id=running_step.step_id,
                    step_title=running_step.title,
                    step_role=running_step.assigned_role,
                    repair_hint=repair_hint,
                )
                history.append(review.to_dict())

                if review.status == ReviewStatus.APPROVED:
                    break  # Step passed — exit repair loop

                # Gate rejected — prepare repair hint for next attempt
                repair_hint = self._build_repair_hint(review)
                attempt += 1

            # ── Record telemetry ──────────────────────────────────────────────
            duration_ms = round((time.perf_counter() - step_start) * 1000, 1)
            outcome = "approved" if (review and review.status == ReviewStatus.APPROVED) else "failed"
            telemetry.append({
                "step_id": running_step.step_id,
                "step_title": running_step.title,
                "duration_ms": duration_ms,
                "attempts": attempt,
                "outcome": outcome,
            })

            # ── Commit outcome ────────────────────────────────────────────────
            if review and review.status == ReviewStatus.APPROVED:
                await self._plan_service.complete_step(step.step_id)
                completed_count += 1
                if attempt > 0:
                    auto_repaired_count += 1
            else:
                await self._plan_service.fail_step(step.step_id)
                failed_count += 1
                break  # Stop execution on unapproved step failure

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
            review_history=history,
            step_telemetry=telemetry,
        )
