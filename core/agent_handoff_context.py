from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent_role import AgentRole
from .agent_run import AgentRunRecord, AgentRunStore
from .agent_work_result import AgentDecisionReference, AgentFinding, TestExecutionResult


@dataclass(frozen=True, slots=True)
class AgentHandoffContext:
    """Bounded, prioritized, and pruned context transferred from previous agent runs to the next agent."""

    task_id: str
    target_role: AgentRole
    plan_id: str | None = None
    step_id: str | None = None
    prior_runs: tuple[AgentRunRecord, ...] = field(default_factory=tuple)
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
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "target_role": self.target_role.value if isinstance(self.target_role, AgentRole) else str(self.target_role),
            "prior_runs": [r.to_dict() for r in self.prior_runs],
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
    ) -> AgentHandoffContext:
        """Prunes verbose entries to minimize prompt token load while preserving essential state and deduplicating entries."""
        # Deduplicate file changes while preserving recency order
        unique_changed = tuple(dict.fromkeys(self.changed_files[-max_files:]))
        unique_created = tuple(dict.fromkeys(self.created_files[-max_files:]))
        unique_notes = tuple(dict.fromkeys(n.strip() for n in self.handoff_notes[-max_notes:] if n.strip()))

        return AgentHandoffContext(
            task_id=self.task_id,
            target_role=self.target_role,
            plan_id=self.plan_id,
            step_id=self.step_id,
            prior_runs=self.prior_runs[-3:] if len(self.prior_runs) > 3 else self.prior_runs,
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
            "### 🔄 Ngữ Cảnh Bàn Giao Từ Lượt Thực Thi Trước (Durable Execution Ledger)",
            "",
        ]

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
                status_icon = "✅" if t.is_success else "❌"
                lines.append(f"- {status_icon} `{t.framework or 'test'}`: {t.passed} passed, {t.failed} failed ({t.summary})")
            lines.append("")

        if self.open_findings:
            lines.append("**Các phát hiện / cảnh báo cần lưu ý**:")
            for f in self.open_findings[-5:]:
                lines.append(f"- `[{f.severity.upper()}]` {f.title}: {f.summary}")
            lines.append("")

        if self.remaining_work:
            lines.append("**Hạng mục còn lại cần xử lý tiếp**:")
            for item in self.remaining_work:
                lines.append(f"- ⏳ {item}")
            lines.append("")

        if self.handoff_notes:
            lines.append("**Ghi chú bàn giao chuyên môn**:")
            for note in self.handoff_notes:
                if note:
                    lines.append(f"> {note}")
            lines.append("")

        return "\n".join(lines).strip()


class AgentHandoffContextBuilder:
    """Constructs prioritized and bounded handoff context for the incoming agent."""

    def __init__(self, run_store: AgentRunStore) -> None:
        self._store = run_store

    async def build_context(
        self,
        *,
        task_id: str,
        target_role: AgentRole,
        plan_id: str | None = None,
        step_id: str | None = None,
        max_prior_runs: int = 5,
        prune: bool = True,
    ) -> AgentHandoffContext:
        prior_runs: list[AgentRunRecord] = []

        # 1. First prioritize same step runs
        if step_id:
            step_runs = await self._store.list_for_step(step_id)
            prior_runs.extend(step_runs)

        # 2. Then plan-level runs
        if len(prior_runs) < max_prior_runs and plan_id:
            plan_runs = await self._store.list_for_plan(plan_id)
            for r in plan_runs:
                if r.run_id not in {p.run_id for p in prior_runs}:
                    prior_runs.append(r)

        # 3. Then task-level runs
        if len(prior_runs) < max_prior_runs:
            task_runs = await self._store.list_for_task(task_id)
            for r in task_runs:
                if r.run_id not in {p.run_id for p in prior_runs}:
                    prior_runs.append(r)

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
        latest_summary = ""

        for r in chronological_runs:
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
            plan_id=plan_id,
            step_id=step_id,
            prior_runs=tuple(chronological_runs),
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
