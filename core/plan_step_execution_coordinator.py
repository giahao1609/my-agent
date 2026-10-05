from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .agent_role import AgentRole, RolePolicyEngine
from .agent_run_service import AgentRunService
from .agent_work_result import AgentWorkResult
from .plan import PlanStepRecord
from .plan_service import PlanService
from .plan_store import PlanStore
from .task import TaskRecord
from .task_store import TaskStore
from .security_scanner import SecurityScannerRegistry, default_security_scanner
from .verification_gate_coordinator import VerificationGateCoordinator


class PlanStepCoderLauncher(Protocol):
    async def start(
        self,
        *,
        task: TaskRecord,
        step: PlanStepRecord,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class PlanStepExecution:
    step_id: str
    session_id: str
    assigned_role: AgentRole = AgentRole.BACKEND_CODER
    run_id: str | None = None


class PlanStepExecutionCoordinator:
    def __init__(
        self,
        *,
        task_store: TaskStore,
        plan_store: PlanStore,
        plan_service: PlanService,
        launcher: PlanStepCoderLauncher,
        policy_engine: RolePolicyEngine | None = None,
        agent_run_service: AgentRunService | None = None,
        security_scanner: SecurityScannerRegistry | None = None,
        verification_gate: VerificationGateCoordinator | None = None,
    ) -> None:
        self._task_store = task_store
        self._plan_store = plan_store
        self._plan_service = plan_service
        self._launcher = launcher
        self._policy_engine = policy_engine or RolePolicyEngine()
        self._agent_run_service = agent_run_service
        self._security_scanner = security_scanner or default_security_scanner
        self._verification_gate = verification_gate or VerificationGateCoordinator()

    async def start_step_execution(
        self,
        step_id: str,
    ) -> PlanStepExecution:
        step = await self._plan_store.get_step(step_id)

        if step is None:
            raise KeyError(
                f"unknown plan step: {step_id}"
            )

        plan = await self._plan_store.get_plan(
            step.plan_id
        )

        if plan is None:
            raise KeyError(
                f"unknown plan: {step.plan_id}"
            )

        task = await self._task_store.get(
            plan.task_id
        )

        if task is None:
            raise KeyError(
                f"unknown task: {plan.task_id}"
            )

        running_step = await self._plan_service.start_step(
            step_id
        )

        try:
            session_id = await self._launcher.start(
                task=task,
                step=running_step,
            )

            if (
                not isinstance(session_id, str)
                or not session_id.strip()
            ):
                raise ValueError(
                    "coder launcher must return a non-empty session_id"
                )

            await self._plan_service.bind_step_execution_session(
                step_id,
                session_id,
            )

        except Exception:
            await self._plan_service.fail_step(
                step_id
            )
            raise

        run_id: str | None = None
        if self._agent_run_service is not None:
            agent_run = await self._agent_run_service.create_run(
                project_id=task.project_id,
                task_id=task.task_id,
                plan_id=plan.plan_id,
                step_id=step_id,
                agent_id="planner_orchestrated_agent",
                execution_role=running_step.assigned_role,
                session_id=session_id,
            )
            await self._agent_run_service.start_run(
                agent_run.run_id,
                session_id=session_id,
            )
            run_id = agent_run.run_id

        return PlanStepExecution(
            step_id=step_id,
            session_id=session_id,
            assigned_role=running_step.assigned_role,
            run_id=run_id,
        )

    async def complete_step_execution(
        self,
        step_id: str,
        result: AgentWorkResult | None = None,
        workspace_path: str | None = None,
    ) -> None:
        # Post-action security audit on modified files if result contains changed files
        if result and workspace_path and result.changed_files:
            target_files = [f.path for f in result.changed_files]
            sec_res = self._security_scanner.scan_workspace(
                workspace_path,
                step_id=step_id,
                target_files=target_files,
            )
            if not sec_res.passed_gate:
                # Still complete but attach findings to result
                pass

        await self._plan_service.complete_step(step_id)
        if self._agent_run_service is not None:
            step_runs = await self._agent_run_service.list_runs_for_step(step_id)
            for r in step_runs:
                if not r.is_terminal:
                    res = result or AgentWorkResult(
                        run_id=r.run_id,
                        status="completed",
                        summary=f"Completed plan step: {step_id}",
                    )
                    await self._agent_run_service.complete_run(r.run_id, res)
