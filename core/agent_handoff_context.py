from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .agent_role import AgentRole
from .agent_run import AgentRunRecord, AgentRunStore
from .agent_work_result import AgentDecisionReference, AgentFinding, TestExecutionResult


class ExecutionTruthStatus(StrEnum):
    SYSTEM_VERIFIED_EXECUTION = "system_verified_execution"
    SYSTEM_FAILED_EXECUTION = "system_failed_execution"
    MOCKED_EXECUTION = "mocked_execution"
    MODEL_CLAIM = "model_claim"
    UNKNOWN_UNVERIFIED = "unknown_unverified"


@dataclass(frozen=True, slots=True)
class ExecutionTruthSummary:
    """Structured, bounded projection of execution truth preserved across handoffs."""

    source_run_id: str
    source_step_id: str | None
    execution_status: ExecutionTruthStatus
    is_real: bool | None
    is_mocked: bool
    verification_performed: bool
    verification_passed: bool | None
    execution_success: bool | None = None
    executor_identity: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_run_id": self.source_run_id,
            "source_step_id": self.source_step_id,
            "execution_status": self.execution_status.value,
            "is_real": self.is_real,
            "is_mocked": self.is_mocked,
            "verification_performed": self.verification_performed,
            "verification_passed": self.verification_passed,
            "execution_success": self.execution_success,
            "executor_identity": self.executor_identity,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionTruthSummary:
        raw_status = data.get("execution_status", ExecutionTruthStatus.UNKNOWN_UNVERIFIED)
        try:
            status = ExecutionTruthStatus(raw_status)
        except ValueError:
            status = ExecutionTruthStatus.UNKNOWN_UNVERIFIED

        return cls(
            source_run_id=data["source_run_id"],
            source_step_id=data.get("source_step_id"),
            execution_status=status,
            is_real=data.get("is_real"),
            is_mocked=bool(data.get("is_mocked", False)),
            verification_performed=bool(data.get("verification_performed", False)),
            verification_passed=data.get("verification_passed"),
            execution_success=data.get("execution_success"),
            executor_identity=data.get("executor_identity", ""),
            summary=data.get("summary", ""),
        )


TRUSTED_SYSTEM_EXECUTORS: frozenset[str] = frozenset({
    "CoderRuntimeStepExecutor",
    "PytestRunner",
    "SubprocessTestRunner",
    "TestRunner",
    "VerificationGateCoordinator",
    "WholePlanCoordinator",
})


def extract_execution_truth(run: AgentRunRecord) -> ExecutionTruthSummary:
    """Extracts truthful execution and verification facts directly from durable run metadata.

    Classification rules:
    - MOCKED_EXECUTION: is_mocked == True OR is_real == False
    - SYSTEM_FAILED_EXECUTION:
        (authoritative is_real == True AND execution_success == False)
        OR (authoritative verification_performed == True AND verification_passed == False)
    - SYSTEM_VERIFIED_EXECUTION:
        authoritative is_real == True
        AND trusted executor provenance (recognized system executor, not forged)
        AND execution_success == True
        AND verification_performed == True
        AND verification_passed == True
    - UNKNOWN_UNVERIFIED: any required positive evidence missing or provenance untrusted
    """
    result_meta = run.result.metadata if (run.result and isinstance(run.result.metadata, Mapping)) else {}
    evidence = result_meta.get("execution_evidence")
    if not isinstance(evidence, Mapping):
        evidence = run.metadata.get("execution_evidence", {}) if isinstance(run.metadata, Mapping) else {}
    if not isinstance(evidence, Mapping):
        evidence = {}

    is_real = evidence.get("is_real")
    if is_real is not None:
        is_real = bool(is_real)

    is_mocked = bool(result_meta.get("is_mocked", evidence.get("is_mocked", False)))
    if is_real is False:
        is_mocked = True

    executor_identity = str(evidence.get("executor_identity") or evidence.get("evidence_source") or "").strip()

    verification_performed = bool(
        evidence.get("verification_performed", result_meta.get("verification_performed", False))
    )
    verification_passed = evidence.get("verification_passed")
    if verification_passed is None and "verification_passed" in result_meta:
        verification_passed = result_meta.get("verification_passed")
    if verification_passed is not None:
        verification_passed = bool(verification_passed)

    execution_success = evidence.get("execution_success")
    if execution_success is None and "execution_success" in result_meta:
        execution_success = result_meta.get("execution_success")
    if execution_success is not None:
        execution_success = bool(execution_success)

    # Server-verified executor provenance check:
    # Executor identity must be recognized and not flagged as untrusted client submission
    is_trusted_provenance = (
        bool(executor_identity)
        and (
            executor_identity in TRUSTED_SYSTEM_EXECUTORS
            or evidence.get("evidence_source") in {
                "coder_agent_orchestrator",
                "verification_gate_coordinator",
                "subprocess_test_runner",
            }
        )
        and not bool(evidence.get("untrusted_client_submission", False))
    )

    # Classification logic
    if is_mocked or is_real is False:
        status = ExecutionTruthStatus.MOCKED_EXECUTION
    elif is_real is True and is_trusted_provenance:
        if execution_success is False or (verification_performed and verification_passed is False):
            status = ExecutionTruthStatus.SYSTEM_FAILED_EXECUTION
        elif (
            execution_success is True
            and verification_performed is True
            and verification_passed is True
        ):
            status = ExecutionTruthStatus.SYSTEM_VERIFIED_EXECUTION
        else:
            status = ExecutionTruthStatus.UNKNOWN_UNVERIFIED
    else:
        # Absence of required positive evidence or untrusted provenance
        status = ExecutionTruthStatus.UNKNOWN_UNVERIFIED

    summary_text = run.result.summary if run.result else ""

    return ExecutionTruthSummary(
        source_run_id=run.run_id,
        source_step_id=run.step_id,
        execution_status=status,
        is_real=is_real,
        is_mocked=is_mocked,
        verification_performed=verification_performed,
        verification_passed=verification_passed,
        execution_success=execution_success,
        executor_identity=executor_identity,
        summary=summary_text,
    )


@dataclass(frozen=True, slots=True)
class AgentHandoffContext:
    """Bounded, prioritized, and pruned context transferred from previous agent runs to the next agent."""

    task_id: str
    target_role: AgentRole
    project_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    prior_runs: tuple[AgentRunRecord, ...] = field(default_factory=tuple)
    execution_truth: tuple[ExecutionTruthSummary, ...] = field(default_factory=tuple)
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    created_files: tuple[str, ...] = field(default_factory=tuple)
    recent_tests: tuple[TestExecutionResult, ...] = field(default_factory=tuple)
    open_findings: tuple[AgentFinding, ...] = field(default_factory=tuple)
    remaining_work: tuple[str, ...] = field(default_factory=tuple)
    handoff_notes: tuple[str, ...] = field(default_factory=tuple)
    decision_references: tuple[AgentDecisionReference, ...] = field(default_factory=tuple)
    latest_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "target_role": self.target_role.value if isinstance(self.target_role, AgentRole) else str(self.target_role),
            "prior_runs": [r.to_dict() for r in self.prior_runs],
            "execution_truth": [e.to_dict() for e in self.execution_truth],
            "changed_files": list(self.changed_files),
            "created_files": list(self.created_files),
            "recent_tests": [t.to_dict() for t in self.recent_tests],
            "open_findings": [f.to_dict() for f in self.open_findings],
            "remaining_work": list(self.remaining_work),
            "handoff_notes": list(self.handoff_notes),
            "decision_references": [d.to_dict() for d in self.decision_references],
            "latest_summary": self.latest_summary,
        }

    def prune_context(
        self,
        *,
        max_notes: int = 3,
        max_findings: int = 5,
        max_tests: int = 3,
        max_files: int = 15,
        max_execution_truths: int = 5,
    ) -> AgentHandoffContext:
        """Prunes verbose entries to minimize prompt token load while preserving essential state and deduplicating entries."""
        unique_changed = tuple(dict.fromkeys(self.changed_files[-max_files:]))
        unique_created = tuple(dict.fromkeys(self.created_files[-max_files:]))
        unique_notes = tuple(dict.fromkeys(n.strip() for n in self.handoff_notes[-max_notes:] if n.strip()))

        return AgentHandoffContext(
            task_id=self.task_id,
            target_role=self.target_role,
            project_id=self.project_id,
            plan_id=self.plan_id,
            step_id=self.step_id,
            prior_runs=self.prior_runs[-3:] if len(self.prior_runs) > 3 else self.prior_runs,
            execution_truth=self.execution_truth[-max_execution_truths:],
            changed_files=unique_changed,
            created_files=unique_created,
            recent_tests=self.recent_tests[-max_tests:],
            open_findings=self.open_findings[-max_findings:],
            remaining_work=self.remaining_work[-5:],
            handoff_notes=unique_notes,
            decision_references=self.decision_references[-3:],
            latest_summary=self.latest_summary,
        )

    def format_prompt_context(self) -> str:
        """Renders an intuitive human & machine-readable handoff brief for the next specialist agent."""
        if not self.prior_runs:
            return "Chưa có lượt thực thi (AgentRun) nào trước đó cho tác vụ này."

        lines = [
            "### Ngữ Cảnh Bàn Giao Từ Lượt Thực Thi Trước (Durable Execution Ledger)",
            "",
        ]

        if self.execution_truth:
            lines.append("**Bằng chứng thực thi hệ thống (Execution Truth)**:")
            for et in self.execution_truth:
                step_ref = f" (bước {et.source_step_id})" if et.source_step_id else ""
                lines.append(
                    f"- Run `{et.source_run_id}`{step_ref}: `[{et.execution_status.value.upper()}]` "
                    f"(real={et.is_real}, mocked={et.is_mocked}, "
                    f"verified={et.verification_performed}/{et.verification_passed}, "
                    f"executor={et.executor_identity or 'n/a'})"
                )
            lines.append("")

        if self.latest_summary:
            lines.append(f"**Tóm tắt công việc gần nhất**: {self.latest_summary}")
            lines.append("")

        if self.created_files:
            lines.append(f"**File mới tạo**: {', '.join(self.created_files)}")
        if self.changed_files:
            lines.append(f"**File đã chỉnh sửa**: {', '.join(self.changed_files)}")
        lines.append("")

        if self.recent_tests:
            lines.append("**Kết quả kiểm thử đã chạy gần nhất**:")
            for t in self.recent_tests[-3:]:
                status_str = "PASS" if t.is_success else "FAIL"
                lines.append(f"- [{status_str}] `{t.framework or 'test'}`: {t.passed} passed, {t.failed} failed ({t.summary})")
            lines.append("")

        if self.open_findings:
            lines.append("**Các phát hiện / cảnh báo cần lưu ý**:")
            for f in self.open_findings[-5:]:
                lines.append(f"- `[{f.severity.upper()}]` {f.title}: {f.summary}")
            lines.append("")

        if self.remaining_work:
            lines.append("**Hạng mục còn lại cần xử lý tiếp**:")
            for item in self.remaining_work:
                lines.append(f"- {item}")
            lines.append("")

        if self.handoff_notes:
            lines.append("**Ghi chú bàn giao chuyên môn**:")
            for note in self.handoff_notes:
                if note:
                    lines.append(f"> {note}")
            lines.append("")

        return "\n".join(lines).strip()


class AgentHandoffContextBuilder:
    """Constructs prioritized and bounded handoff context for the incoming agent.

    Enforces strict project scoping. Unscoped handoff contexts are never advertised
    as safe for production-facing execution.
    """

    def __init__(
        self,
        run_store: AgentRunStore,
        *,
        require_project_id: bool = False,
    ) -> None:
        self._store = run_store
        self._require_project_id = require_project_id

    async def build_context(
        self,
        *,
        task_id: str,
        target_role: AgentRole,
        project_id: str | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
        max_prior_runs: int = 5,
        prune: bool = True,
    ) -> AgentHandoffContext:
        if self._require_project_id and not (project_id and project_id.strip()):
            raise ValueError("project_id is mandatory for safe scoped handoff context generation")
        prior_runs: list[AgentRunRecord] = []

        # 1. First prioritize same step runs
        if step_id:
            step_runs = await self._store.list_for_step(step_id)
            if project_id:
                step_runs = [r for r in step_runs if r.project_id == project_id]
            prior_runs.extend(step_runs)

        # 2. Then plan-level runs
        if len(prior_runs) < max_prior_runs and plan_id:
            plan_runs = await self._store.list_for_plan(plan_id)
            if project_id:
                plan_runs = [r for r in plan_runs if r.project_id == project_id]
            for r in plan_runs:
                if r.run_id not in {p.run_id for p in prior_runs}:
                    prior_runs.append(r)

        # 3. Then task-level runs
        if len(prior_runs) < max_prior_runs:
            task_runs = await self._store.list_for_task(task_id)
            if project_id:
                task_runs = [r for r in task_runs if r.project_id == project_id]
            for r in task_runs:
                if r.run_id not in {p.run_id for p in prior_runs}:
                    prior_runs.append(r)

        # Enforce strict project isolation and fail-closed against cross-project mixing
        effective_project_id = project_id
        if effective_project_id:
            prior_runs = [r for r in prior_runs if r.project_id == effective_project_id]
        else:
            projects_in_runs = {r.project_id for r in prior_runs if r.project_id}
            if len(projects_in_runs) > 1:
                raise ValueError("Cross-project agent run mixing detected across multiple projects")
            effective_project_id = next(iter(projects_in_runs)) if projects_in_runs else None

        # Limit to max_prior_runs (most recent runs)
        sorted_runs = sorted(prior_runs, key=lambda r: r.created_at, reverse=True)[:max_prior_runs]
        # Restore chronological order
        chronological_runs = sorted(sorted_runs, key=lambda r: r.created_at)

        changed_files: set[str] = set()
        created_files: set[str] = set()
        recent_tests: list[TestExecutionResult] = []
        open_findings: list[AgentFinding] = []
        remaining_work: list[str] = []
        handoff_notes: list[str] = []
        decision_refs: list[AgentDecisionReference] = []
        execution_truth: list[ExecutionTruthSummary] = []
        latest_summary = ""

        for r in chronological_runs:
            execution_truth.append(extract_execution_truth(r))
            if r.result:
                if r.result.summary:
                    latest_summary = r.result.summary

                for f in r.result.changed_files:
                    changed_files.add(f.path)
                for cf in r.result.created_files:
                    created_files.add(cf)
                for t in r.result.tests:
                    recent_tests.append(t)
                for fd in r.result.findings:
                    open_findings.append(fd)
                for rw in r.result.remaining_work:
                    if rw not in remaining_work:
                        remaining_work.append(rw)
                if r.result.handoff_notes:
                    handoff_notes.append(r.result.handoff_notes)
                for dec in r.result.decisions_made:
                    decision_refs.append(dec)

        ctx = AgentHandoffContext(
            task_id=task_id,
            target_role=target_role,
            project_id=effective_project_id,
            plan_id=plan_id,
            step_id=step_id,
            prior_runs=tuple(chronological_runs),
            execution_truth=tuple(execution_truth),
            changed_files=tuple(sorted(changed_files)),
            created_files=tuple(sorted(created_files)),
            recent_tests=tuple(recent_tests),
            open_findings=tuple(open_findings),
            remaining_work=tuple(remaining_work),
            handoff_notes=tuple(handoff_notes),
            decision_references=tuple(decision_refs),
            latest_summary=latest_summary,
        )

        return ctx.prune_context() if prune else ctx
