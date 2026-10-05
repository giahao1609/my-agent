from __future__ import annotations

import uuid
from typing import Any, Protocol

from .agent_role import AgentRole
from .agent_run_service import AgentRunService
from .agent_handoff_context import AgentHandoffContext, AgentHandoffContextBuilder
from .agent_work_result import AgentWorkResult
from .context import ExecutionContext
from .decision import DecisionOption, DecisionRecord, DecisionSeverity, DecisionState
from .decision_service import DecisionService
from .front_agent import (
    FrontAgentProgress,
    FrontAgentProgressStep,
    FrontAgentRequest,
    FrontAgentResultSummary,
    PresentationLevel,
    ResponseKind,
)
from .handoff_contracts import DecisionRequiredResult
from .plan import PlanRecord, PlanState, PlanStepRecord, PlanStepState
from .plan_service import PlanService
from .plan_step_execution_coordinator import PlanStepExecutionCoordinator
from .plan_store import PlanStore
from .planning_coordinator import PlanningCoordinator
from .specialist_execution import (
    SpecialistExecutionRequest,
    SpecialistExecutionResult,
    SpecialistRouter,
)
from .task import TaskRecord, TaskState
from .task_service import TaskService
from .task_store import TaskStore


class PendingApprovalStore(Protocol):
    """Async store interface for durable pending tool approval requests."""

    async def save(self, approval: dict[str, Any]) -> None: ...
    async def get(self, tool_call_id: str) -> dict[str, Any] | None: ...
    async def list_by_task(self, task_id: str) -> list[dict[str, Any]]: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def delete(self, tool_call_id: str) -> dict[str, Any] | None: ...


class InMemoryPendingApprovalStore:
    """Default in-memory fallback (non-durable) for backwards compatibility.

    Production deployments should inject a SqlitePendingApprovalStore so that
    pending approvals survive process restarts and host switches.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def save(self, approval: dict[str, Any]) -> None:
        self._store[approval["tool_call_id"]] = approval

    async def get(self, tool_call_id: str) -> dict[str, Any] | None:
        return self._store.get(tool_call_id)

    async def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        return [
            app for app in self._store.values()
            if app.get("task_id") == task_id or not app.get("task_id")
        ]

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self._store.values())

    async def delete(self, tool_call_id: str) -> dict[str, Any] | None:
        return self._store.pop(tool_call_id, None)


class MyAgentControlPlane:
    """Layer 2 Deterministic Control Plane & Facade for MyAgent.

    Owns internal agent topology, state mutations, policy enforcement, and lifecycle state.
    Provides a normalized, model-agnostic facade for the Front Agent.
    """

    def __init__(
        self,
        *,
        task_store: TaskStore,
        task_service: TaskService,
        plan_store: PlanStore,
        plan_service: PlanService,
        decision_service: DecisionService,
        agent_run_service: AgentRunService | None = None,
        agent_handoff_builder: AgentHandoffContextBuilder | None = None,
        planning_coordinator: PlanningCoordinator | None = None,
        execution_coordinator: PlanStepExecutionCoordinator | None = None,
        specialist_router: SpecialistRouter | None = None,
        approval_store: PendingApprovalStore | None = None,
    ) -> None:
        self._task_store = task_store
        self._task_service = task_service
        self._plan_store = plan_store
        self._plan_service = plan_service
        self._decision_service = decision_service
        self._agent_run_service = agent_run_service
        self._agent_handoff_builder = agent_handoff_builder
        self._planning_coordinator = planning_coordinator
        self._execution_coordinator = execution_coordinator
        self._specialist_router = specialist_router or SpecialistRouter()
        # Durable approval store — defaults to in-memory for backwards compat.
        # Inject SqlitePendingApprovalStore in production for restart-survival.
        self._approval_store: PendingApprovalStore = approval_store or InMemoryPendingApprovalStore()

    async def submit_user_request(
        self,
        request: FrontAgentRequest,
    ) -> tuple[ResponseKind, str, FrontAgentProgress | None, DecisionRecord | None, dict[str, Any] | None, FrontAgentResultSummary | None]:
        """Unified entry point for user requests from the Front Agent."""
        # 1. Handle user decision resolution if present
        if request.selected_decision:
            dec_id = request.selected_decision.get("decision_id")
            opt_id = request.selected_decision.get("option_id")
            if dec_id and opt_id:
                resolved_dec = await self.resolve_user_decision(
                    decision_id=dec_id,
                    option_id=opt_id,
                    rationale=request.user_input,
                )
                progress = await self.get_progress(resolved_dec.task_id)
                msg = f"Đã ghi nhận lựa chọn '{opt_id}' cho quyết định '{dec_id}'. Tiếp tục tiến trình."
                return ResponseKind.PROGRESS, msg, progress, None, None, None

        # 2. Handle user approval resolution if present
        if request.approval_response:
            call_id = request.approval_response.get("tool_call_id")
            approved = bool(request.approval_response.get("approved", False))
            if call_id:
                app_res = await self.resolve_user_approval(
                    tool_call_id=call_id,
                    approved=approved,
                    rationale=request.user_input,
                )
                status_txt = "phê duyệt" if approved else "từ chối"
                msg = f"Đã {status_txt} thao tác '{call_id}'."
                progress = await self.get_progress(request.task_id) if request.task_id else None
                return ResponseKind.PROGRESS, msg, progress, None, None, None

        # 3. Retrieve or create Task
        task_id = request.task_id
        task: TaskRecord | None = None
        if task_id:
            task = await self._task_store.get(task_id)

        if task is None:
            new_task_id = f"task-{uuid.uuid4().hex[:8]}"
            task = await self._task_service.create_task(
                project_id=request.project_id,
                task_id=new_task_id,
                objective=request.user_input,
            )
            task_id = task.task_id

        # 4. Check for any OPEN decisions on this task
        open_decisions = await self._decision_service.list_open_decisions(task_id)
        if open_decisions:
            open_dec = open_decisions[0]
            progress = await self.get_progress(task_id)
            msg = f"Tác vụ đang tạm dừng chờ bạn chọn phương án giải quyết cho: '{open_dec.prompt}'"
            return ResponseKind.DECISION_REQUIRED, msg, progress, open_dec, None, None

        # 4b. Check for any pending approvals on this task
        task_approvals = await self._approval_store.list_by_task(task_id)
        if task_approvals:
            app = task_approvals[0]
            progress = await self.get_progress(task_id)
            msg = f"Tác vụ đang tạm dừng chờ bạn phê duyệt cho thao tác: '{app.get('tool_name')}'"
            return ResponseKind.APPROVAL_REQUIRED, msg, progress, None, app, None

        # 5. Check if task is completed
        progress = await self.get_progress(task_id)
        if task.state == TaskState.COMPLETED:
            res_summary = await self.get_result(task_id)
            msg = f"Tác vụ '{task.objective}' đã hoàn tất."
            return ResponseKind.COMPLETED, msg, progress, None, None, res_summary

        if task.state == TaskState.FAILED:
            res_summary = await self.get_result(task_id)
            msg = f"Tác vụ '{task.objective}' gặp sự cố và đã dừng."
            return ResponseKind.FAILED, msg, progress, None, None, res_summary

        # 6. Default in-progress or newly accepted task
        msg = f"Đã tiếp nhận yêu cầu cho tác vụ '{task.objective}'."
        return ResponseKind.MESSAGE, msg, progress, None, None, None

    # Backward-compatible alias for existing tests
    async def process_user_intent(
        self,
        request: FrontAgentRequest,
    ) -> tuple[str, FrontAgentProgress | None, DecisionRecord | None, FrontAgentResultSummary | None]:
        kind, msg, prog, dec, _app, res = await self.submit_user_request(request)
        return msg, prog, dec, res

    async def get_current_task_state(self, task_id: str) -> dict[str, Any]:
        """Returns structured dictionary of task state."""
        task = await self._task_store.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        progress = await self.get_progress(task_id)
        return {
            "task_id": task.task_id,
            "project_id": task.project_id,
            "objective": task.objective,
            "state": task.state.value if hasattr(task.state, "value") else str(task.state),
            "active_plan_id": task.active_plan_id,
            "progress": progress.to_dict(),
        }

    async def get_progress(self, task_id: str) -> FrontAgentProgress:
        """Constructs detailed progress snapshot from durable Task/Plan/Step stores."""
        task = await self._task_store.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        plan: PlanRecord | None = None
        if task.active_plan_id:
            plan = await self._plan_store.get_plan(task.active_plan_id)

        steps: list[FrontAgentProgressStep] = []
        active_role: str | None = None
        current_phase = "lập kế hoạch" if not plan else "thực thi"
        total_steps = 0
        completed_steps = 0

        if plan:
            plan_steps = await self._plan_store.list_steps(plan.plan_id)
            total_steps = len(plan_steps)
            for s in plan_steps:
                state_str = s.state.value if hasattr(s.state, "value") else str(s.state)
                if s.state == PlanStepState.COMPLETED:
                    completed_steps += 1
                elif s.state == PlanStepState.RUNNING:
                    active_role = s.assigned_role.value if hasattr(s.assigned_role, "value") else str(s.assigned_role)
                    current_phase = f"bước '{s.title}' ({active_role})"

                steps.append(
                    FrontAgentProgressStep(
                        step_id=s.step_id,
                        title=s.title,
                        role=s.assigned_role.value if hasattr(s.assigned_role, "value") else str(s.assigned_role),
                        state=state_str,
                    )
                )

        return FrontAgentProgress(
            task_id=task.task_id,
            objective=task.objective,
            task_state=task.state.value if hasattr(task.state, "value") else str(task.state),
            plan_id=plan.plan_id if plan else None,
            steps=tuple(steps),
            active_role=active_role,
            current_phase=current_phase,
            total_steps=total_steps,
            completed_steps=completed_steps,
        )

    async def register_pending_approval(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        reason: str = "",
        risk_explanation: str = "",
        prompt: str = "May MyAgent perform this sensitive action?",
        task_id: str | None = None,
    ) -> None:
        """Registers a pending tool approval requirement durably."""
        await self._approval_store.save({
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "reason": reason,
            "risk_explanation": risk_explanation,
            "prompt": prompt,
            "task_id": task_id,
        })

    async def get_pending_interaction(self, task_id: str) -> dict[str, Any] | None:
        """Returns any pending Decision or Approval required from user."""
        open_decisions = await self._decision_service.list_open_decisions(task_id)
        if open_decisions:
            dec = open_decisions[0]
            return {
                "type": "decision",
                "decision": dec.to_dict(),
            }
        for app in await self._approval_store.list_by_task(task_id):
            return {
                "type": "approval",
                "approval": app,
            }
        return None

    async def resolve_user_decision(
        self,
        decision_id: str,
        option_id: str,
        rationale: str = "",
    ) -> DecisionRecord:
        """Resolves an open Decision with explicit user selection."""
        return await self._decision_service.resolve_decision(
            decision_id,
            selected_option_id=option_id,
            rationale=rationale,
        )

    async def resolve_user_approval(
        self,
        tool_call_id: str,
        approved: bool,
        rationale: str = "",
    ) -> dict[str, Any]:
        """Resolves tool execution approval and removes it from the durable store."""
        popped = await self._approval_store.delete(tool_call_id)
        return {
            "tool_call_id": tool_call_id,
            "approved": approved,
            "rationale": rationale,
            "status": "resolved",
            "previous_approval": popped,
        }

    async def get_result(self, task_id: str) -> FrontAgentResultSummary | None:
        """Summarizes completed work from durable agent runs and steps."""
        task = await self._task_store.get(task_id)
        if task is None:
            return None

        changed_files: set[str] = set()
        artifacts: list[str] = []
        summary = f"Kết quả cho tác vụ {task.objective}"

        if self._agent_run_service is not None:
            runs = await self._agent_run_service.list_runs_for_task(task_id)
            for r in runs:
                if r.result:
                    if r.result.summary:
                        summary = r.result.summary
                    for f in r.result.changed_files:
                        changed_files.add(f.path)
                    for a in r.result.artifacts:
                        artifacts.append(a)

        return FrontAgentResultSummary(
            task_id=task_id,
            status=task.state.value if hasattr(task.state, "value") else str(task.state),
            summary=summary,
            modified_files=tuple(sorted(changed_files)),
            artifacts=tuple(artifacts),
            tests_passed=True if task.state == TaskState.COMPLETED else None,
            security_passed=True if task.state == TaskState.COMPLETED else None,
        )

    async def reconstruct_work_context(self, task_id: str) -> dict[str, Any]:
        """Reconstructs full durable context after restart or model switch."""
        task = await self._task_store.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        progress = await self.get_progress(task_id)
        pending = await self.get_pending_interaction(task_id)
        result = await self.get_result(task_id)

        return {
            "task": task.to_dict(),
            "progress": progress.to_dict(),
            "pending_interaction": pending,
            "result_summary": result.to_dict() if result else None,
        }

    async def handle_specialist_result(
        self,
        task_id: str,
        step_id: str,
        result: SpecialistExecutionResult,
    ) -> DecisionRecord | None:
        """Processes typed output from a specialist agent, transitioning lifecycle deterministically."""
        if result.is_decision_required and result.decision:
            sec_req = result.decision
            sev = DecisionSeverity.MEDIUM
            try:
                sev = DecisionSeverity(sec_req.severity.lower())
            except Exception:
                sev = DecisionSeverity.MEDIUM

            converted_opts = [
                DecisionOption(
                    option_id=opt.option_id,
                    title=opt.title,
                    description=opt.description,
                    trade_offs=opt.trade_offs,
                    impact=opt.impact,
                    recommended=opt.recommended,
                )
                for opt in sec_req.options
            ]

            decision = await self._decision_service.create_decision(
                task_id=task_id,
                step_id=step_id,
                prompt=sec_req.prompt,
                severity=sev,
                options=converted_opts,
            )
            return decision

        if result.is_success:
            await self._plan_service.complete_step(step_id)
        else:
            await self._plan_service.fail_step(step_id)

        return None
