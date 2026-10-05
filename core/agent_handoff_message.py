from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent_role import AgentRole
from .agent_run import AgentRunRecord


@dataclass(frozen=True, slots=True)
class AgentHandoffMessage:
    """Compatibility & presentation projection rendered from an AgentRunRecord."""

    run_id: str
    agent_id: str
    role: AgentRole
    task_id: str
    plan_id: str | None = None
    step_id: str | None = None
    status: str = "completed"
    summary: str = ""
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    created_files: tuple[str, ...] = field(default_factory=tuple)
    tests_summary: str = ""
    findings_count: int = 0
    remaining_work: tuple[str, ...] = field(default_factory=tuple)
    handoff_notes: str = ""

    @classmethod
    def from_run_record(cls, run: AgentRunRecord) -> AgentHandoffMessage:
        res = run.result
        changed = []
        created = []
        tests_sum = ""
        findings_cnt = 0
        rem_work = []
        notes = ""
        summary_text = ""

        if res:
            summary_text = res.summary
            changed = [f.path for f in res.changed_files]
            created = list(res.created_files)
            if res.tests:
                passed_cnt = sum(t.passed for t in res.tests)
                failed_cnt = sum(t.failed for t in res.tests)
                tests_sum = f"{passed_cnt} passed, {failed_cnt} failed ({len(res.tests)} suites)"
            findings_cnt = len(res.findings)
            rem_work = list(res.remaining_work)
            notes = res.handoff_notes

        return cls(
            run_id=run.run_id,
            agent_id=run.agent_id,
            role=run.execution_role,
            task_id=run.task_id,
            plan_id=run.plan_id,
            step_id=run.step_id,
            status=run.state.value,
            summary=summary_text,
            changed_files=tuple(changed),
            created_files=tuple(created),
            tests_summary=tests_sum,
            findings_count=findings_cnt,
            remaining_work=tuple(rem_work),
            handoff_notes=notes,
        )

    def to_conversation_metadata(self) -> dict[str, Any]:
        return {
            "event_type": "agent_handoff",
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "role": self.role.value if isinstance(self.role, AgentRole) else str(self.role),
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "status": self.status,
            "changed_files": list(self.changed_files),
            "created_files": list(self.created_files),
        }

    def render_markdown(self) -> str:
        role_name = self.role.value.replace("_", " ").title() if isinstance(self.role, AgentRole) else str(self.role)
        lines = [
            f"### 🤝 Bàn Giao Nhiệm Vụ: **{self.agent_id}** (`{role_name}`)",
            f"- **Trạng thái**: `{self.status.upper()}`",
            f"- **Tóm tắt**: {self.summary or 'Không có tóm tắt'}",
        ]
        if self.created_files:
            lines.append(f"- **Tạo mới**: {', '.join(self.created_files)}")
        if self.changed_files:
            lines.append(f"- **Chỉnh sửa**: {', '.join(self.changed_files)}")
        if self.tests_summary:
            lines.append(f"- **Kiểm thử**: {self.tests_summary}")
        if self.findings_count > 0:
            lines.append(f"- **Phát hiện**: {self.findings_count} cảnh báo")
        if self.remaining_work:
            lines.append(f"- **Việc còn lại**: {', '.join(self.remaining_work)}")
        if self.handoff_notes:
            lines.append(f"- **Ghi chú**: {self.handoff_notes}")

        return "\n".join(lines)
