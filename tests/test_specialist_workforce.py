from __future__ import annotations

import pytest

from core.agent_role import AgentDefinition, AgentRegistry, AgentRole, RolePolicyEngine
from core.context import ExecutionContext
from core.handoff_contracts import DecisionOptionDraft, DecisionRequiredResult
from core.specialist_execution import (
    SpecialistCapabilityFilter,
    SpecialistExecutionRequest,
    SpecialistExecutionResult,
    SpecialistRouter,
)
from core.tools import ToolDefinition, ToolPermission


def test_agent_definition_serialization():
    agent = AgentDefinition(
        agent_id="custom-researcher",
        role=AgentRole.RESEARCHER,
        name="Custom Research Agent",
        description="Analyzes docs and papers",
        allowed_tools=("view_file", "search_web"),
        forbidden_tools=("write_to_file",),
        input_contract="Task",
        output_contract="ResearchResult",
    )
    d = agent.to_dict()
    assert d["agent_id"] == "custom-researcher"
    assert d["role"] == "researcher"

    restored = AgentDefinition.from_dict(d)
    assert restored.agent_id == "custom-researcher"
    assert restored.role == AgentRole.RESEARCHER
    assert "view_file" in restored.allowed_tools


def test_all_specialist_roles_exist():
    expected_roles = [
        AgentRole.PLANNER,
        AgentRole.ARCHITECT,
        AgentRole.RESEARCHER,
        AgentRole.BACKEND_CODER,
        AgentRole.UI_CODER,
        AgentRole.TESTER,
        AgentRole.SECURITY_REVIEWER,
        AgentRole.REVIEWER,
    ]
    registry = AgentRegistry()
    for role in expected_roles:
        agent_def = registry.get_default_for_role(role)
        assert agent_def.role == role
        assert agent_def.name is not None


def test_least_privilege_tool_filtering():
    policy_engine = RolePolicyEngine()
    cap_filter = SpecialistCapabilityFilter(policy_engine)

    async def dummy_handler(ctx, params):
        return {}

    read_tool = ToolDefinition(
        name="view_file",
        description="Reads file",
        input_schema={},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.READ}),
    )
    write_tool = ToolDefinition(
        name="write_to_file",
        description="Writes file",
        input_schema={},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.WRITE}),
    )
    cmd_tool = ToolDefinition(
        name="run_command",
        description="Runs shell command",
        input_schema={},
        handler=dummy_handler,
        permissions=frozenset({ToolPermission.EXECUTE}),
    )

    all_tools = [read_tool, write_tool, cmd_tool]

    # Architect cannot have write tools
    architect_def = AgentRegistry().get_default_for_role(AgentRole.ARCHITECT)
    effective_architect = cap_filter.get_effective_tools(architect_def, all_tools)
    effective_names = [t.name for t in effective_architect]
    assert "view_file" in effective_names
    assert "write_to_file" not in effective_names

    # Reviewer cannot have write tools
    reviewer_def = AgentRegistry().get_default_for_role(AgentRole.REVIEWER)
    effective_reviewer = cap_filter.get_effective_tools(reviewer_def, all_tools)
    rev_names = [t.name for t in effective_reviewer]
    assert "write_to_file" not in rev_names

    # Backend coder can have write tools
    backend_def = AgentRegistry().get_default_for_role(AgentRole.BACKEND_CODER)
    effective_backend = cap_filter.get_effective_tools(backend_def, all_tools)
    backend_names = [t.name for t in effective_backend]
    assert "write_to_file" in backend_names


def test_specialist_execution_contracts():
    ctx = ExecutionContext(workspace_id="/workspace", project_id="proj-1")
    req = SpecialistExecutionRequest(
        task_id="task-1",
        plan_id="plan-1",
        step_id="step-1",
        agent_id="architect-default",
        role=AgentRole.ARCHITECT,
        instruction="Design auth architecture",
        context=ctx,
    )

    assert req.task_id == "task-1"
    assert req.role == AgentRole.ARCHITECT

    opt_draft = DecisionOptionDraft(
        option_id="opt-jwt",
        title="JWT Auth",
        description="Stateless token",
        recommended=True,
    )
    dec_req = DecisionRequiredResult(
        step_id="step-1",
        prompt="Choose token scheme",
        severity="medium",
        options=(opt_draft,),
    )

    res = SpecialistExecutionResult(
        status="decision_required",
        summary="Awaiting auth scheme decision",
        output_contract="DecisionRequiredResult",
        decision=dec_req,
    )
    assert res.is_decision_required
    assert not res.is_success
    assert res.decision is not None
    assert res.decision.options[0].option_id == "opt-jwt"
