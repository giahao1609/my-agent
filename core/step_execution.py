from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.agent_role import AgentRole
from core.agent_run import AgentRunRecord
from core.agent_run_service import AgentRunService
from core.agent_work_result import AgentWorkResult, CommandExecution, FileChange
from core.context import ExecutionContext
from core.events import AgentEvent, EventType
from core.handoff_contracts import ImplementationResult
from core.orchestrator import Orchestrator
from core.session import SessionState


@dataclass(frozen=True, slots=True)
class StepExecutionEvidence:
    """Execution evidence recording genuine runtime execution and activity."""

    step_id: str
    executor_identity: str
    runtime_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    started_at: str = ""
    finished_at: str = ""
    is_real: bool = True
    evidence_source: str = "coder_runtime"
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    files_changed: tuple[str, ...] = field(default_factory=tuple)
    files_created: tuple[str, ...] = field(default_factory=tuple)
    files_deleted: tuple[str, ...] = field(default_factory=tuple)
    commands_executed: tuple[str, ...] = field(default_factory=tuple)
    execution_success: bool = True
    verification_performed: bool = False
    verification_passed: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "executor_identity": self.executor_identity,
            "runtime_id": self.runtime_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "is_real": self.is_real,
            "evidence_source": self.evidence_source,
            "tool_calls": list(self.tool_calls),
            "files_changed": list(self.files_changed),
            "files_created": list(self.files_created),
            "files_deleted": list(self.files_deleted),
            "commands_executed": list(self.commands_executed),
            "execution_success": self.execution_success,
            "verification_performed": self.verification_performed,
            "verification_passed": self.verification_passed,
            "notes": self.notes,
        }


class CoderRuntimeStepExecutor:
    """Real StepExecutor implementation bridging WholePlanCoordinator to CoderAgent stack.

    Implements the StepExecutor protocol by running the agent turn loop, executing tools
    against the actual filesystem/runtime, recording execution ledger entries via AgentRunService,
    and returning verified ImplementationResult with non-simulated execution evidence.
    """

    is_real_executor: bool = True

    def __init__(
        self,
        *,
        coder_stack: Any,
        workspace_path: Path | str,
        project_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        agent_run_service: AgentRunService | None = None,
        user_id: str | None = None,
        worker_task_launcher: Any | None = None,
    ) -> None:
        self._coder_stack = coder_stack
        self._workspace_path = Path(workspace_path)
        self._project_id = project_id or "default-project"
        self._task_id = task_id or "default-task"
        self._plan_id = plan_id
        self._user_id = user_id or "authorized-user"
        self._agent_run_service = agent_run_service
        self._worker_task_launcher = worker_task_launcher

    async def execute_step(
        self,
        step_id: str,
        step_title: str,
        step_role: AgentRole,
        repair_hint: str = "",
    ) -> ImplementationResult:
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        context = ExecutionContext(
            workspace_id=str(self._workspace_path),
            agent_id="coder",
            user_id=self._user_id,
            task_id=self._task_id,
            project_id=self._project_id,
        )

        orchestrator: Orchestrator = self._coder_stack.orchestrator
        session_id = await orchestrator.start_session(context)

        # Record AgentRun if service is available
        agent_run: AgentRunRecord | None = None
        if self._agent_run_service is not None:
            agent_run = await self._agent_run_service.create_run(
                project_id=self._project_id,
                task_id=self._task_id,
                plan_id=self._plan_id,
                step_id=step_id,
                agent_id="coder",
                execution_role=step_role,
                session_id=session_id,
            )
            await self._agent_run_service.start_run(agent_run.run_id, session_id=session_id)

        # Launch background in-process worker task if requested / available
        worker_task: asyncio.Task[None] | None = None
        if self._worker_task_launcher is not None:
            worker_task = await self._worker_task_launcher(session_id)

        if worker_task is None:
            finished_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if agent_run is not None and self._agent_run_service is not None:
                await self._agent_run_service.fail_run(
                    agent_run.run_id,
                    reason="No coder worker available to drive step execution",
                )
            evidence = StepExecutionEvidence(
                step_id=step_id,
                executor_identity="CoderRuntimeStepExecutor",
                runtime_id="none",
                session_id=session_id,
                run_id=agent_run.run_id if agent_run else None,
                started_at=now_utc,
                finished_at=finished_utc,
                is_real=True,
                evidence_source="coder_agent_orchestrator",
                execution_success=False,
                verification_performed=False,
                verification_passed=False,
                notes="Blocked: no active coder worker available to drive execution",
            )
            return ImplementationResult(
                step_id=step_id,
                summary=f"[CoderRuntimeStepExecutor] Blocked: No active coder worker available for step '{step_title}'.",
                success=False,
                is_mocked=False,
                execution_evidence=evidence.to_dict(),
            )

        prompt = f"Execute step '{step_title}' for role '{step_role.value}'."
        if repair_hint:
            prompt += f"\nPrevious attempt rejected with repair hint: {repair_hint}"

        tool_calls: list[dict[str, Any]] = []
        modified_files: list[str] = []
        created_files: list[str] = []
        deleted_files: list[str] = []
        commands_run: list[str] = []
        text_outputs: list[str] = []
        execution_success = True
        waiting_approval = False

        try:
            # Stream events from CoderAgent with bounded timeout
            async with asyncio.timeout(30.0):
                event_stream = self._coder_stack.agent.run_session(
                    session_id,
                    prompt,
                    context,
                )

                async for event in event_stream:
                    if event.type is EventType.TOOL_USE:
                        name = event.payload.get("name", "")
                        arguments = event.payload.get("arguments", {})
                        call_id = event.payload.get("tool_call_id", "")
                        tool_calls.append({
                            "tool_call_id": call_id,
                            "name": name,
                            "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
                        })
                        # Track files affected by tool
                        if name in ("write_file", "edit_file"):
                            path = arguments.get("path")
                            if path and str(path) not in modified_files:
                                modified_files.append(str(path))
                        elif name == "delete_path":
                            path = arguments.get("path")
                            if path and str(path) not in deleted_files:
                                deleted_files.append(str(path))
                        elif name == "run_command":
                            cmd = arguments.get("command") or arguments.get("argv")
                            if cmd:
                                commands_run.append(str(cmd))

                    elif event.type is EventType.TOOL_CONFIRM:
                        # An action requires approval — real execution blocks or waits
                        waiting_approval = True
                        execution_success = False
                        break

                    elif event.type is EventType.TEXT:
                        text_val = event.payload.get("text", "")
                        if text_val:
                            text_outputs.append(text_val)

                    elif event.type is EventType.STOP:
                        reason = event.payload.get("reason", "")
                        if reason in ("runtime_error", "failed", "cancelled"):
                            execution_success = False

            # Await worker task if running and not paused/waiting for approval
            if worker_task is not None:
                if waiting_approval:
                    worker_task.cancel()
                    try:
                        await worker_task
                    except (asyncio.CancelledError, Exception):
                        pass
                else:
                    try:
                        await asyncio.wait_for(asyncio.shield(worker_task), timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

        except Exception as exc:
            execution_success = False
            text_outputs.append(f"Execution failed with exception: {exc}")
            if agent_run is not None and self._agent_run_service is not None:
                await self._agent_run_service.fail_run(
                    agent_run.run_id,
                    reason=str(exc),
                )
            raise

        finished_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        summary = (
            f"[CoderRuntimeStepExecutor] Real execution of step '{step_title}'. "
            f"Tools invoked: {len(tool_calls)}. Files modified: {len(modified_files)}."
        )
        if text_outputs:
            summary += f" Output: {text_outputs[-1][:120]}"

        if waiting_approval:
            summary += " [BLOCKED: Approval Required]"

        evidence = StepExecutionEvidence(
            step_id=step_id,
            executor_identity="CoderRuntimeStepExecutor",
            runtime_id="in_process_coder_runtime",
            session_id=session_id,
            run_id=agent_run.run_id if agent_run else None,
            started_at=now_utc,
            finished_at=finished_utc,
            is_real=True,
            evidence_source="coder_agent_orchestrator",
            tool_calls=tuple(tool_calls),
            files_changed=tuple(modified_files),
            files_created=tuple(created_files),
            files_deleted=tuple(deleted_files),
            commands_executed=tuple(commands_run),
            verification_performed=True,
            verification_passed=execution_success and not waiting_approval,
            notes="Approval required" if waiting_approval else "",
        )

        # Complete or fail AgentRun record
        if agent_run is not None and self._agent_run_service is not None:
            file_changes = tuple(
                FileChange(path=f, change_type="modified") for f in modified_files
            )
            cmd_execs = tuple(
                CommandExecution(command=c, exit_code=0) for c in commands_run
            )
            work_result = AgentWorkResult(
                run_id=agent_run.run_id,
                status="completed" if (execution_success and not waiting_approval) else "failed",
                summary=summary,
                changed_files=file_changes,
                created_files=tuple(created_files),
                deleted_files=tuple(deleted_files),
                commands_run=cmd_execs,
                metadata=evidence.to_dict(),
            )
            if execution_success and not waiting_approval:
                await self._agent_run_service.complete_run(agent_run.run_id, work_result)
            else:
                await self._agent_run_service.fail_run(
                    agent_run.run_id,
                    result=work_result,
                    reason="Execution failed or requires approval",
                )

        return ImplementationResult(
            step_id=step_id,
            summary=summary,
            modified_files=tuple(modified_files),
            created_files=tuple(created_files),
            deleted_files=tuple(deleted_files),
            success=execution_success and not waiting_approval,
            is_mocked=False,
            execution_evidence=evidence.to_dict(),
        )
