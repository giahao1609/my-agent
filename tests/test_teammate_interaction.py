import tempfile
from pathlib import Path

import pytest
from core.context import ExecutionContext
from core.control_plane import MyAgentControlPlane
from core.decision_service import DecisionService
from core.front_agent import (
    ConversationStyle,
    FrontAgent,
    FrontAgentApprovalView,
    FrontAgentRequest,
    PresentationLevel,
    ProgressCoordinator,
    ResponseKind,
)
from core.plan_service import PlanService
from core.task_service import TaskService
from core.tool_executor import ToolExecutor
from core.tool_policy import ToolDecision, ToolPolicy
from core.tool_registry import ToolRegistry
from core.tools import ToolDefinition, ToolPermission
from core.project import ProjectRecord
from persistence.sqlite_decision_store import SQLiteDecisionStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore


@pytest.mark.asyncio
async def test_tool_policy_auto_allows_safe_tools() -> None:
    policy = ToolPolicy()
    context = ExecutionContext(workspace_id="ws-1", user_id="user-1")

    # 1. Safe command in run_command
    run_tool = ToolDefinition(
        name="run_command",
        description="Run terminal command",
        input_schema={"type": "object"},
        handler=lambda ctx, args: {"output": "ok"},
        permissions=frozenset({ToolPermission.EXECUTE}),
    )

    safe_commands = [
        "pytest tests/ -q",
        "python -m pytest",
        "npm test",
        "git diff",
        "git status",
        "git log -n 5",
        "ls -la",
        "grep 'hello' main.py",
        "python -m py_compile app.py",
        "ruff check .",
    ]

    for cmd in safe_commands:
        res = policy.evaluate(run_tool, context, arguments={"command": cmd})
        assert res.decision == ToolDecision.ALLOW, f"Command should be auto-allowed: {cmd}"

    # 2. Routine read tool
    read_tool = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object"},
        handler=lambda ctx, args: {"content": "data"},
        permissions=frozenset({ToolPermission.READ}),
    )
    res_read = policy.evaluate(read_tool, context, arguments={"path": "src/main.py"})
    assert res_read.decision == ToolDecision.ALLOW


@pytest.mark.asyncio
async def test_tool_policy_intercepts_destructive_operations_with_risk_explanation() -> None:
    policy = ToolPolicy()
    context = ExecutionContext(workspace_id="ws-1", user_id="user-1")

    run_tool = ToolDefinition(
        name="run_command",
        description="Run terminal command",
        input_schema={"type": "object"},
        handler=lambda ctx, args: {"output": "ok"},
        permissions=frozenset({ToolPermission.EXECUTE}),
    )

    destructive_cmds = [
        ("rm -rf dist/", "Xóa đệ quy tệp hoặc thư mục mà không qua thùng rác"),
        ("git reset --hard", "Hủy bỏ toàn bộ các thay đổi chưa commit"),
        ("git push --force origin main", "Ghi đè lịch sử commit trên remote repository"),
        ("DROP TABLE users", "Xóa cấu trúc bảng và toàn bộ dữ liệu trong bảng"),
    ]

    for cmd, expected_risk in destructive_cmds:
        res = policy.evaluate(run_tool, context, arguments={"command": cmd})
        assert res.decision == ToolDecision.REQUIRE_APPROVAL
        assert expected_risk in res.risk_explanation

    # Recursive directory deletion
    delete_tool = ToolDefinition(
        name="delete_path",
        description="Delete path",
        input_schema={"type": "object"},
        handler=lambda ctx, args: {"deleted": True},
        permissions=frozenset({ToolPermission.WRITE}),
    )
    res_delete = policy.evaluate(
        delete_tool,
        context,
        arguments={"path": "dist", "recursive": True},
    )
    assert res_delete.decision == ToolDecision.REQUIRE_APPROVAL
    assert "xóa vĩnh viễn dữ liệu" in res_delete.risk_explanation


@pytest.mark.asyncio
async def test_tool_executor_approval_flow_with_risk_explanation() -> None:
    registry = ToolRegistry()
    policy = ToolPolicy()
    executor = ToolExecutor(registry, policy)
    context = ExecutionContext(workspace_id="ws-1", user_id="user-1")

    async def rm_handler(ctx, args):
        return {"deleted": True}

    tool = ToolDefinition(
        name="run_command",
        description="Run command",
        input_schema={"type": "object"},
        handler=rm_handler,
        permissions=frozenset({ToolPermission.EXECUTE}),
    )
    registry.register(tool)

    # 1. Unapproved call yields approval_required with risk explanation
    res = await executor.execute(
        "run_command",
        {"command": "rm -rf build/"},
        context,
        approved=False,
    )
    assert res["status"] == "approval_required"
    assert res["tool"] == "run_command"
    assert "Xóa đệ quy" in str(res.get("risk_explanation", ""))

    # 2. Approved call executes successfully
    res_approved = await executor.execute(
        "run_command",
        {"command": "rm -rf build/"},
        context,
        approved=True,
    )
    assert res_approved["deleted"] is True


def test_progress_coordinator_aggregates_tool_bursts() -> None:
    coordinator = ProgressCoordinator(min_interval=5.0)

    # Empty
    assert coordinator.summarize_burst() is None

    # Search and read burst
    coordinator.record_tool_event("read_file")
    coordinator.record_tool_event("search_text")
    coordinator.record_tool_event("code_symbol")
    msg_search = coordinator.summarize_burst()
    assert msg_search == "Tôi đang tra cứu và đối chiếu các tệp tin trong mã nguồn."

    # Test burst
    coordinator.record_tool_event("run_command")
    coordinator.record_tool_event("test")
    msg_test = coordinator.summarize_burst()
    assert "kiểm thử" in msg_test

    # Edit burst
    coordinator.record_tool_event("write_file")
    coordinator.record_tool_event("edit_file")
    msg_edit = coordinator.summarize_burst()
    assert "cập nhật" in msg_edit


def test_presentation_levels_and_approval_view() -> None:
    approval = FrontAgentApprovalView(
        tool_call_id="call-42",
        tool_name="run_command",
        arguments={"command": "rm -rf build/"},
        reason="Lệnh nguy hiểm: rm -rf",
        risk_explanation="Lệnh rm -rf sẽ xóa vĩnh viễn thư mục build mà không qua thùng rác.",
        prompt="Cho phép thực hiện thao tác xóa này?",
    )

    # 1. NORMAL mode: clean teammate phrasing, risk explanation, no raw JSON arguments
    normal_display = FrontAgent.format_approval_presentation(
        approval,
        level=PresentationLevel.NORMAL,
    )
    assert "### Yêu Cầu Phê Duyệt Thao Tác" in normal_display
    assert "rm -rf build/" in normal_display
    assert "xóa vĩnh viễn thư mục build" in normal_display
    assert "**Bạn có đồng ý phê duyệt thao tác này không?**" in normal_display
    # Raw JSON string like '{"command":' should not appear in normal display
    assert '{"command":' not in normal_display

    # 2. DEBUG mode: includes [DEBUG] header and raw parameters JSON
    debug_display = FrontAgent.format_approval_presentation(
        approval,
        level=PresentationLevel.DEBUG,
    )
    assert "### [DEBUG]" in debug_display
    assert '{"command": "rm -rf build/"}' in debug_display
    assert "call-42" in debug_display

    # 3. SILENT mode: renders clean approval request
    silent_display = FrontAgent.format_approval_presentation(
        approval,
        level=PresentationLevel.SILENT,
    )
    assert "### Yêu Cầu Phê Duyệt Thao Tác" in silent_display


@pytest.mark.asyncio
async def test_control_plane_and_front_agent_event_approval_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "teammate_test.db"
        proj_store = SQLiteProjectStore(db_path)
        task_store = SQLiteTaskStore(db_path)
        plan_store = SQLitePlanStore(db_path)
        decision_store = SQLiteDecisionStore(db_path)

        await proj_store.initialize()
        await task_store.initialize()
        await plan_store.initialize()
        await decision_store.initialize()

        project = ProjectRecord(
            project_id="proj-teammate",
            name="Teammate Project",
            workspace_path=str(tmpdir),
        )
        await proj_store.create(project)
        await proj_store.set_active("proj-teammate")

        task_service = TaskService(project_store=proj_store, task_store=task_store)
        plan_service = PlanService(plan_store=plan_store, task_store=task_store)
        decision_service = DecisionService(decision_store=decision_store)

        cp = MyAgentControlPlane(
            task_store=task_store,
            task_service=task_service,
            plan_store=plan_store,
            plan_service=plan_service,
            decision_service=decision_service,
        )

        front_agent = FrontAgent(control_plane=cp)

        # 1. Create a task
        req1 = FrontAgentRequest(
            user_input="Xây dựng tính năng dọn dẹp cache",
            project_id="proj-teammate",
        )
        res1 = await front_agent.handle_user_request(req1)
        assert res1.kind == ResponseKind.MESSAGE
        task_id = res1.task_id
        assert task_id is not None

        # 2. Register a pending approval in control plane
        await cp.register_pending_approval(
            tool_call_id="call-99",
            tool_name="delete_path",
            arguments={"path": "cache/", "recursive": True},
            reason="Xóa thư mục đệ quy",
            risk_explanation="Thao tác này sẽ xóa toàn bộ tệp cache đã lưu trữ.",
            prompt="Tôi cần bạn xác nhận trước khi xóa cache.",
            task_id=task_id,
        )

        # 3. User checks status or asks what's happening
        req2 = FrontAgentRequest(
            user_input="Tình hình thế nào rồi?",
            project_id="proj-teammate",
            task_id=task_id,
        )
        res2 = await front_agent.handle_user_request(req2)
        assert res2.kind == ResponseKind.APPROVAL_REQUIRED
        assert res2.approval_view is not None
        assert res2.approval_view.tool_call_id == "call-99"
        assert "xóa toàn bộ tệp cache" in res2.approval_view.risk_explanation

        # 4. User approves the action
        req3 = FrontAgentRequest(
            user_input="Đồng ý, bạn dọn dẹp đi",
            project_id="proj-teammate",
            task_id=task_id,
            approval_response={"tool_call_id": "call-99", "approved": True},
        )
        res3 = await front_agent.handle_user_request(req3)
        assert res3.kind == ResponseKind.PROGRESS
        assert "phê duyệt" in res3.message

        # Pending interaction in control plane is now cleared
        pending = await cp.get_pending_interaction(task_id)
        assert pending is None

