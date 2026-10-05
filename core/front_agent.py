from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, Sequence

from .agent_role import AgentRole
from .decision import DecisionOption, DecisionRecord, DecisionState
from .model import ModelMessage
from .protocols import ModelBackend


class PresentationLevel(StrEnum):
    NORMAL = "normal"      # Default: natural conversation, hides internal topology & runtimes
    DETAILED = "detailed"  # Shows current phase, active specialist, tests, changed files, decisions
    DEBUG = "debug"        # Shows task_id, plan_id, step_id, AgentRun, session, runtime, model, raw state
    SILENT = "silent"      # Minimalist output: only crucial outcomes and final result


@dataclass(frozen=True, slots=True)
class ConversationStyle:
    tone: str = "teammate"
    expose_raw_commands: bool = False
    narrate_internal_steps: bool = False
    proactive_updates: bool = True


class ProgressCoordinator:
    """Coordinates and aggregates rapid bursts of machine events into teammate summaries."""

    def __init__(self, min_interval: float = 5.0) -> None:
        self._min_interval = min_interval
        self._recent_tools: list[str] = []

    def record_tool_event(self, tool_name: str) -> None:
        self._recent_tools.append(tool_name)

    def summarize_burst(self) -> str | None:
        if not self._recent_tools:
            return None
        tools = list(self._recent_tools)
        self._recent_tools.clear()

        # Categorize tools
        has_tests = any(t in ("run_command", "run_test", "test") for t in tools)
        has_edits = any(t in ("write_file", "edit_file", "patch", "delete_path") for t in tools)
        has_search_read = all(
            t in ("read_file", "search_text", "find_files", "list_directory", "code_symbol", "code_graph")
            for t in tools
        )

        if has_search_read:
            return "Tôi đang tra cứu và đối chiếu các tệp tin trong mã nguồn."
        if has_tests and has_edits:
            return "Tôi đã áp dụng các thay đổi mã nguồn và đang chạy kiểm thử để xác minh."
        if has_tests:
            return "Tôi đang chạy bộ kiểm thử tự động để kiểm tra tính ổn định."
        if has_edits:
            return "Tôi đang cập nhật các tệp mã nguồn tương ứng."

        return f"Tôi đang thực hiện {len(tools)} bước thao tác cần thiết trong quy trình."


class ResponseKind(StrEnum):
    MESSAGE = "message"
    PROGRESS = "progress"
    DECISION_REQUIRED = "decision_required"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FrontAgentDecisionOptionView:
    option_id: str
    title: str
    description: str
    trade_offs: str = ""
    impact: str = ""
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "title": self.title,
            "description": self.description,
            "trade_offs": self.trade_offs,
            "impact": self.impact,
            "recommended": self.recommended,
        }


@dataclass(frozen=True, slots=True)
class FrontAgentDecisionView:
    decision_id: str
    task_id: str
    severity: str
    prompt: str
    options: tuple[FrontAgentDecisionOptionView, ...] = field(default_factory=tuple)

    @classmethod
    def from_record(cls, record: DecisionRecord) -> FrontAgentDecisionView:
        return cls(
            decision_id=record.decision_id,
            task_id=record.task_id,
            severity=record.severity.value if hasattr(record.severity, "value") else str(record.severity),
            prompt=record.prompt,
            options=tuple(
                FrontAgentDecisionOptionView(
                    option_id=opt.option_id,
                    title=opt.title,
                    description=opt.description,
                    trade_offs=opt.trade_offs,
                    impact=opt.impact,
                    recommended=opt.recommended,
                )
                for opt in record.options
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "severity": self.severity,
            "prompt": self.prompt,
            "options": [opt.to_dict() for opt in self.options],
        }


@dataclass(frozen=True, slots=True)
class FrontAgentApprovalView:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    risk_explanation: str = ""
    prompt: str = "May MyAgent perform this sensitive action?"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
            "risk_explanation": self.risk_explanation,
            "prompt": self.prompt,
        }


@dataclass(frozen=True, slots=True)
class FrontAgentProgressStep:
    step_id: str
    title: str
    role: str
    state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "role": self.role,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class FrontAgentProgress:
    task_id: str
    objective: str
    task_state: str
    plan_id: str | None = None
    steps: tuple[FrontAgentProgressStep, ...] = field(default_factory=tuple)
    active_role: str | None = None
    current_phase: str = ""
    total_steps: int = 0
    completed_steps: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    modified_files_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "task_state": self.task_state,
            "plan_id": self.plan_id,
            "steps": [s.to_dict() for s in self.steps],
            "active_role": self.active_role,
            "current_phase": self.current_phase,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "modified_files_count": self.modified_files_count,
        }


@dataclass(frozen=True, slots=True)
class FrontAgentResultSummary:
    task_id: str
    status: str
    summary: str
    modified_files: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    tests_passed: bool | None = None
    security_passed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "modified_files": list(self.modified_files),
            "artifacts": list(self.artifacts),
            "tests_passed": self.tests_passed,
            "security_passed": self.security_passed,
        }


@dataclass(frozen=True, slots=True)
class FrontAgentRequest:
    user_input: str
    project_id: str
    task_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    level: PresentationLevel = PresentationLevel.NORMAL
    selected_decision: dict[str, str] | None = None  # e.g. {"decision_id": "...", "option_id": "..."}
    approval_response: dict[str, Any] | None = None  # e.g. {"tool_call_id": "...", "approved": True}
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_input": self.user_input,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "level": self.level.value if isinstance(self.level, PresentationLevel) else str(self.level),
            "selected_decision": self.selected_decision,
            "approval_response": self.approval_response,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class FrontAgentResponse:
    kind: ResponseKind
    message: str
    task_id: str | None = None
    presentation_level: PresentationLevel = PresentationLevel.NORMAL
    progress: FrontAgentProgress | None = None
    decision_view: FrontAgentDecisionView | None = None
    approval_view: FrontAgentApprovalView | None = None
    result_summary: FrontAgentResultSummary | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if isinstance(self.kind, ResponseKind) else str(self.kind),
            "message": self.message,
            "task_id": self.task_id,
            "presentation_level": self.presentation_level.value if isinstance(self.presentation_level, PresentationLevel) else str(self.presentation_level),
            "progress": self.progress.to_dict() if self.progress else None,
            "decision_view": self.decision_view.to_dict() if self.decision_view else None,
            "approval_view": self.approval_view.to_dict() if self.approval_view else None,
            "result_summary": self.result_summary.to_dict() if self.result_summary else None,
            "metadata": self.metadata,
        }

    def format_user_display(self) -> str:
        """Formats the response according to the requested presentation level."""
        if self.presentation_level == PresentationLevel.DEBUG:
            lines = [
                f"### [DEBUG] MyAgent Response ({self.kind.value.upper()})",
                f"- **Task ID**: `{self.task_id or 'None'}`",
                f"- **Message**: {self.message}",
            ]
            if self.progress:
                lines.append(f"- **Progress State**: `{self.progress.task_state}` | Phase: `{self.progress.current_phase}` | Steps: `{self.progress.completed_steps}/{self.progress.total_steps}`")
                lines.append(f"- **Active Role**: `{self.progress.active_role or 'None'}`")
            if self.decision_view:
                lines.append(f"- **Decision Required**: `{self.decision_view.decision_id}` (Severity: {self.decision_view.severity})")
                for opt in self.decision_view.options:
                    recom = " [RECOMMENDED]" if opt.recommended else ""
                    lines.append(f"  - Option `{opt.option_id}`: {opt.title}{recom}")
            if self.approval_view:
                lines.append(f"- **Approval Required**: `{self.approval_view.tool_call_id}` for tool `{self.approval_view.tool_name}` (Args: {self.approval_view.arguments})")
            if self.result_summary:
                lines.append(f"- **Result Summary**: Status=`{self.result_summary.status}`, ModifiedFiles={len(self.result_summary.modified_files)}")
            if self.metadata:
                lines.append(f"- **Metadata**: `{json.dumps(self.metadata)}`")
            return "\n".join(lines)

        elif self.presentation_level == PresentationLevel.DETAILED:
            lines = [self.message, ""]
            if self.progress and self.progress.steps:
                lines.append("#### Tiến Độ Chi Tiết:")
                for s in self.progress.steps:
                    icon = "[x]" if s.state == "completed" else ("[>]" if s.state == "running" else "[ ]")
                    lines.append(f"- {icon} **{s.title}** ({s.role}) — `{s.state}`")
                lines.append("")
            if self.decision_view:
                lines.append(FrontAgent.format_decision_presentation(self.decision_view))
            if self.approval_view:
                lines.append(FrontAgent.format_approval_presentation(self.approval_view, level=self.presentation_level))
            return "\n".join(lines).strip()

        elif self.presentation_level == PresentationLevel.SILENT:
            if self.decision_view:
                return FrontAgent.format_decision_presentation(self.decision_view)
            if self.approval_view:
                return FrontAgent.format_approval_presentation(self.approval_view, level=self.presentation_level)
            if self.result_summary:
                return f"Đã hoàn thành. {self.result_summary.summary}"
            return self.message

        # NORMAL mode: Natural conversational output
        if self.decision_view:
            return f"{self.message}\n\n{FrontAgent.format_decision_presentation(self.decision_view)}"
        if self.approval_view:
            return f"{self.message}\n\n{FrontAgent.format_approval_presentation(self.approval_view, level=self.presentation_level)}"
        return self.message


class ControlPlaneInterface(Protocol):
    """Facade interface exposed by MyAgent Control Plane to the Front Agent."""

    async def submit_user_request(
        self,
        request: FrontAgentRequest,
    ) -> tuple[ResponseKind, str, FrontAgentProgress | None, DecisionRecord | None, dict[str, Any] | None, FrontAgentResultSummary | None]:
        ...

    async def get_current_task_state(self, task_id: str) -> dict[str, Any]:
        ...

    async def get_progress(self, task_id: str) -> FrontAgentProgress:
        ...

    async def get_pending_interaction(self, task_id: str) -> dict[str, Any] | None:
        ...

    async def resolve_user_decision(self, decision_id: str, option_id: str, rationale: str = "") -> DecisionRecord:
        ...

    async def resolve_user_approval(self, tool_call_id: str, approved: bool, rationale: str = "") -> dict[str, Any]:
        ...

    async def get_result(self, task_id: str) -> FrontAgentResultSummary | None:
        ...

    async def reconstruct_work_context(self, task_id: str) -> dict[str, Any]:
        ...


class FrontAgent:
    """Layer 1 Unified User-Facing Agent for MyAgent.

    Stable Identity:
        agent_id = "myagent-front"
        role = AgentRole.USER_INTERFACE
        name = "MyAgent"

    Invariant:
        Front Agent identity != Model identity != Runtime identity.

    Owns:
        - Natural user conversation
        - Intent interpretation
        - Progress and result presentation
        - Decision and approval presentation
        - Grounded status reports

    Does NOT own:
        - Task, Plan, PlanStep, Session, AgentRun lifecycle mutations
        - Direct tool execution or security overrides
    """

    AGENT_ID = "myagent-front"
    ROLE = AgentRole.USER_INTERFACE
    NAME = "MyAgent"

    def __init__(
        self,
        control_plane: ControlPlaneInterface,
        model_backend: ModelBackend | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._model_backend = model_backend

    async def handle_user_request(self, request: FrontAgentRequest) -> FrontAgentResponse:
        """Processes incoming user input, delegates to Control Plane facade, and formats output."""
        kind, raw_msg, progress, open_decision, pending_approval, result_summary = (
            await self._control_plane.submit_user_request(request)
        )

        decision_view = None
        if open_decision is not None and open_decision.is_open():
            decision_view = FrontAgentDecisionView.from_record(open_decision)

        approval_view = None
        if pending_approval is not None:
            approval_view = FrontAgentApprovalView(
                tool_call_id=pending_approval.get("tool_call_id", ""),
                tool_name=pending_approval.get("tool_name", ""),
                arguments=pending_approval.get("arguments", {}),
                reason=pending_approval.get("reason", "Action requires user confirmation."),
                risk_explanation=pending_approval.get("risk_explanation", ""),
                prompt=pending_approval.get("prompt", "May MyAgent perform this sensitive action?"),
            )

        # Ensure grounded anti-hallucination message consistency
        message = self._ground_response_message(
            raw_message=raw_msg,
            kind=kind,
            progress=progress,
            result_summary=result_summary,
            decision_view=decision_view,
            approval_view=approval_view,
        )

        return FrontAgentResponse(
            kind=kind,
            message=message,
            task_id=request.task_id or (progress.task_id if progress else None),
            presentation_level=request.level,
            progress=progress,
            decision_view=decision_view,
            approval_view=approval_view,
            result_summary=result_summary,
            metadata={"agent_id": self.AGENT_ID, "role": self.ROLE.value},
        )

    def _ground_response_message(
        self,
        raw_message: str,
        kind: ResponseKind,
        progress: FrontAgentProgress | None,
        result_summary: FrontAgentResultSummary | None,
        decision_view: FrontAgentDecisionView | None,
        approval_view: FrontAgentApprovalView | None,
    ) -> str:
        """Guarantees natural language claims strictly adhere to durable evidence without fabricating completion."""
        if kind == ResponseKind.DECISION_REQUIRED and decision_view:
            return raw_message or f"Mình cần bạn lựa chọn hướng xử lý cho: {decision_view.prompt}"

        if kind == ResponseKind.APPROVAL_REQUIRED and approval_view:
            return raw_message or f"Thao tác '{approval_view.tool_name}' cần bạn phê duyệt trước khi tiếp tục."

        if kind == ResponseKind.COMPLETED and result_summary:
            # Verified grounded completion
            return raw_message or f"Đã hoàn thành tác vụ. {result_summary.summary}"

        if kind == ResponseKind.PROGRESS and progress:
            if progress.task_state in ("running", "in_progress"):
                if progress.current_phase:
                    return raw_message or f"Mình đang thực hiện {progress.current_phase}."
                return raw_message or "Đang tiếp tục thực thi các bước trong kế hoạch."

        return raw_message

    @classmethod
    def format_decision_presentation(cls, decision_view: FrontAgentDecisionView) -> str:
        """Renders natural decision options with trade-offs and recommendations."""
        lines = [
            f"### Cần Quyết Định (Mức Độ: {decision_view.severity.upper()})",
            "",
            decision_view.prompt,
            "",
        ]
        for idx, opt in enumerate(decision_view.options, start=1):
            recom = " (Khuyên Dùng)" if opt.recommended else ""
            lines.append(f"#### Lựa Chọn {idx}: {opt.title}{recom}")
            lines.append(f"- **Mô tả**: {opt.description}")
            if opt.trade_offs:
                lines.append(f"- **Đánh đổi**: {opt.trade_offs}")
            if opt.impact:
                lines.append(f"- **Tác động**: {opt.impact}")
            lines.append("")

        lines.append("**Bạn muốn chọn phương án nào?**")
        return "\n".join(lines)

    @classmethod
    def format_approval_presentation(
        cls,
        approval_view: FrontAgentApprovalView,
        level: PresentationLevel = PresentationLevel.NORMAL,
    ) -> str:
        """Renders tool permission approval request with clear risk explanation."""
        if level == PresentationLevel.DEBUG:
            lines = [
                "### [DEBUG] Yêu Cầu Phê Duyệt Thao Tác (Tool Approval Required)",
                "",
                approval_view.prompt,
                "",
                f"- **Công cụ**: `{approval_view.tool_name}`",
                f"- **Call ID**: `{approval_view.tool_call_id}`",
                f"- **Lý do**: {approval_view.reason}",
                f"- **Đánh giá rủi ro**: {approval_view.risk_explanation or 'N/A'}",
                f"- **Tham số**: `{json.dumps(approval_view.arguments)}`",
                "",
                "**Bạn có đồng ý phê duyệt thao tác này không?**",
            ]
            return "\n".join(lines)

        risk_text = (
            approval_view.risk_explanation
            or approval_view.reason
            or "Thao tác có tính chất ảnh hưởng đến dữ liệu hoặc hệ thống."
        )
        target_info = ""
        if "command" in approval_view.arguments:
            target_info = f"- **Lệnh**: `{approval_view.arguments['command']}`"
        elif "path" in approval_view.arguments:
            target_info = f"- **Đường dẫn**: `{approval_view.arguments['path']}`"

        lines = [
            "### Yêu Cầu Phê Duyệt Thao Tác (Tool Approval Required)",
            "",
            approval_view.prompt,
            "",
            f"- **Công cụ**: `{approval_view.tool_name}`",
        ]
        if target_info:
            lines.append(target_info)
        lines.extend([
            f"- **Đánh giá rủi ro**: {risk_text}",
            "",
            "**Bạn có đồng ý phê duyệt thao tác này không?**",
        ])
        return "\n".join(lines)
