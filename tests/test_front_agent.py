from __future__ import annotations

import pytest

from core.agent_role import AgentRole
from core.decision import DecisionOption, DecisionRecord, DecisionSeverity, DecisionState
from core.front_agent import (
    FrontAgent,
    FrontAgentApprovalView,
    FrontAgentDecisionOptionView,
    FrontAgentDecisionView,
    FrontAgentProgress,
    FrontAgentProgressStep,
    FrontAgentRequest,
    FrontAgentResponse,
    FrontAgentResultSummary,
    PresentationLevel,
    ResponseKind,
)


class MockControlPlane:
    def __init__(self) -> None:
        self.resolved_decisions: list[tuple[str, str, str]] = []
        self.resolved_approvals: list[tuple[str, bool, str]] = []

    async def submit_user_request(self, request: FrontAgentRequest):
        if request.user_input == "need_decision":
            opt_a = DecisionOption(option_id="a", title="Approach A", description="Desc A", recommended=True)
            opt_b = DecisionOption(option_id="b", title="Approach B", description="Desc B")
            dec = DecisionRecord(
                decision_id="dec-mock-1",
                task_id="task-123",
                step_id="step-1",
                severity=DecisionSeverity.HIGH,
                state=DecisionState.OPEN,
                prompt="Select migration path",
                options=(opt_a, opt_b),
            )
            prog = FrontAgentProgress(
                task_id="task-123",
                objective="Migrate DB",
                task_state="in_progress",
                current_phase="chờ quyết định kiến trúc",
            )
            return ResponseKind.DECISION_REQUIRED, "Cần bạn chọn hướng tiếp cận cho migration.", prog, dec, None, None

        elif request.user_input == "need_approval":
            prog = FrontAgentProgress(
                task_id="task-123",
                objective="Deploy database",
                task_state="in_progress",
            )
            pending_app = {
                "tool_call_id": "call-del-1",
                "tool_name": "delete_file",
                "arguments": {"path": "legacy.sql"},
                "reason": "Cleanup obsolete schema file.",
                "prompt": "May MyAgent perform this sensitive action?",
            }
            return ResponseKind.APPROVAL_REQUIRED, "Thao tác xóa file cần bạn phê duyệt.", prog, None, pending_app, None

        elif request.user_input == "task_completed":
            res_summary = FrontAgentResultSummary(
                task_id="task-123",
                status="completed",
                summary="All backend APIs and UI components were tested and validated.",
                modified_files=("api.py", "App.tsx"),
                tests_passed=True,
                security_passed=True,
            )
            prog = FrontAgentProgress(
                task_id="task-123",
                objective="Add Google Login",
                task_state="completed",
                total_steps=3,
                completed_steps=3,
            )
            return ResponseKind.COMPLETED, "Hoàn thành tác vụ Add Google Login.", prog, None, None, res_summary

        elif request.selected_decision:
            dec_id = request.selected_decision["decision_id"]
            opt_id = request.selected_decision["option_id"]
            self.resolved_decisions.append((dec_id, opt_id, request.user_input))
            prog = FrontAgentProgress(
                task_id="task-123",
                objective="Migrate DB",
                task_state="in_progress",
            )
            return ResponseKind.PROGRESS, f"Đã ghi nhận lựa chọn {opt_id}.", prog, None, None, None

        elif request.approval_response:
            call_id = request.approval_response["tool_call_id"]
            approved = request.approval_response["approved"]
            self.resolved_approvals.append((call_id, approved, request.user_input))
            prog = FrontAgentProgress(
                task_id="task-123",
                objective="Deploy database",
                task_state="in_progress",
            )
            return ResponseKind.PROGRESS, f"Đã xử lý phê duyệt {call_id}.", prog, None, None, None

        else:
            prog = FrontAgentProgress(
                task_id="task-123",
                objective=request.user_input,
                task_state="running",
                current_phase="triển khai backend",
                steps=(
                    FrontAgentProgressStep(step_id="s1", title="Thiết kế API", role="backend_coder", state="completed"),
                    FrontAgentProgressStep(step_id="s2", title="Viết UI", role="ui_coder", state="running"),
                ),
            )
            return ResponseKind.MESSAGE, f"Đã tiếp nhận yêu cầu: {request.user_input}", prog, None, None, None

    async def get_current_task_state(self, task_id: str):
        return {"task_id": task_id, "state": "running"}

    async def get_progress(self, task_id: str):
        return FrontAgentProgress(task_id=task_id, objective="Test", task_state="running")

    async def get_pending_interaction(self, task_id: str):
        return None

    async def resolve_user_decision(self, decision_id: str, option_id: str, rationale: str = ""):
        return DecisionRecord(
            decision_id=decision_id,
            task_id="task-123",
            prompt="",
            options=(),
            selected_option_id=option_id,
            state=DecisionState.RESOLVED,
        )

    async def resolve_user_approval(self, tool_call_id: str, approved: bool, rationale: str = ""):
        return {"tool_call_id": tool_call_id, "approved": approved}

    async def get_result(self, task_id: str):
        return None

    async def reconstruct_work_context(self, task_id: str):
        return {
            "task": {"task_id": task_id, "objective": "Durable task"},
            "progress": {"task_id": task_id, "task_state": "in_progress"},
            "pending_interaction": None,
            "result_summary": None,
        }


@pytest.mark.asyncio
async def test_front_agent_stable_identity():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    assert agent.AGENT_ID == "myagent-front"
    assert agent.ROLE == AgentRole.USER_INTERFACE
    assert agent.NAME == "MyAgent"


@pytest.mark.asyncio
async def test_front_agent_normal_mode_hides_topology():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    req = FrontAgentRequest(
        user_input="Implement login feature",
        project_id="proj-1",
        level=PresentationLevel.NORMAL,
    )
    res = await agent.handle_user_request(req)

    assert res.kind == ResponseKind.MESSAGE
    assert "Đã tiếp nhận yêu cầu: Implement login feature" in res.message

    display = res.format_user_display()
    # NORMAL mode does not expose internal agent names or debug headers
    assert "[DEBUG]" not in display
    assert "backend_coder" not in display


@pytest.mark.asyncio
async def test_front_agent_detailed_and_debug_modes():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    req_detailed = FrontAgentRequest(
        user_input="Implement login feature",
        project_id="proj-1",
        level=PresentationLevel.DETAILED,
    )
    res_detailed = await agent.handle_user_request(req_detailed)
    display_det = res_detailed.format_user_display()
    assert "Tiến Độ Chi Tiết:" in display_det
    assert "**Thiết kế API** (backend_coder)" in display_det

    req_debug = FrontAgentRequest(
        user_input="Implement login feature",
        project_id="proj-1",
        level=PresentationLevel.DEBUG,
    )
    res_debug = await agent.handle_user_request(req_debug)
    display_dbg = res_debug.format_user_display()
    assert "### [DEBUG] MyAgent Response" in display_dbg
    assert "Task ID" in display_dbg


@pytest.mark.asyncio
async def test_front_agent_decision_presentation():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    req = FrontAgentRequest(
        user_input="need_decision",
        project_id="proj-1",
    )
    res = await agent.handle_user_request(req)
    assert res.kind == ResponseKind.DECISION_REQUIRED
    assert res.decision_view is not None
    assert res.decision_view.decision_id == "dec-mock-1"

    display = res.format_user_display()
    assert "### Cần Quyết Định" in display
    assert "Approach A (Khuyên Dùng)" in display
    assert "Approach B" in display
    assert "**Bạn muốn chọn phương án nào?**" in display


@pytest.mark.asyncio
async def test_front_agent_approval_presentation():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    req = FrontAgentRequest(
        user_input="need_approval",
        project_id="proj-1",
    )
    res = await agent.handle_user_request(req)
    assert res.kind == ResponseKind.APPROVAL_REQUIRED
    assert res.approval_view is not None
    assert res.approval_view.tool_name == "delete_file"

    display = res.format_user_display()
    assert "### Yêu Cầu Phê Duyệt Thao Tác" in display
    assert "delete_file" in display
    assert "**Bạn có đồng ý phê duyệt thao tác này không?**" in display


@pytest.mark.asyncio
async def test_front_agent_anti_hallucination_completed_grounding():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    req = FrontAgentRequest(
        user_input="task_completed",
        project_id="proj-1",
    )
    res = await agent.handle_user_request(req)
    assert res.kind == ResponseKind.COMPLETED
    assert res.result_summary is not None
    assert res.result_summary.tests_passed is True
    assert "Hoàn thành tác vụ" in res.message


@pytest.mark.asyncio
async def test_front_agent_decision_and_approval_routing():
    cp = MockControlPlane()
    agent = FrontAgent(control_plane=cp)

    # Resolve decision
    req_dec = FrontAgentRequest(
        user_input="Tôi chọn phương án A",
        project_id="proj-1",
        selected_decision={"decision_id": "dec-mock-1", "option_id": "a"},
    )
    res_dec = await agent.handle_user_request(req_dec)
    assert res_dec.kind == ResponseKind.PROGRESS
    assert len(cp.resolved_decisions) == 1
    assert cp.resolved_decisions[0] == ("dec-mock-1", "a", "Tôi chọn phương án A")

    # Resolve approval
    req_app = FrontAgentRequest(
        user_input="Đồng ý xóa file",
        project_id="proj-1",
        approval_response={"tool_call_id": "call-del-1", "approved": True},
    )
    res_app = await agent.handle_user_request(req_app)
    assert res_app.kind == ResponseKind.PROGRESS
    assert len(cp.resolved_approvals) == 1
    assert cp.resolved_approvals[0] == ("call-del-1", True, "Đồng ý xóa file")
