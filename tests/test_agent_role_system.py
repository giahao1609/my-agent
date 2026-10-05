from __future__ import annotations

import pytest

from core.agent_role import (
    AgentDefinition,
    AgentRegistry,
    AgentRole,
    RolePolicyEngine,
    default_agent_registry,
)
from core.tool_policy import ToolDecision
from core.tools import ToolDefinition, ToolPermission


def test_agent_role_enum_values() -> None:
    assert AgentRole.PLANNER == "planner"
    assert AgentRole.ARCHITECT == "architect"
    assert AgentRole.RESEARCHER == "researcher"
    assert AgentRole.BACKEND_CODER == "backend_coder"
    assert AgentRole.UI_CODER == "ui_coder"
    assert AgentRole.TESTER == "tester"
    assert AgentRole.SECURITY_REVIEWER == "security_reviewer"
    assert AgentRole.REVIEWER == "reviewer"


def test_agent_definition_serialization() -> None:
    agent_def = AgentDefinition(
        agent_id="test-architect",
        role=AgentRole.ARCHITECT,
        name="Test Architect",
        description="Analyzes design",
        allowed_tools=("code_context", "view_file"),
        forbidden_tools=("write_to_file",),
        input_contract="Task",
        output_contract="ArchitectureProposal",
        approval_required=True,
    )

    data = agent_def.to_dict()
    assert data["agent_id"] == "test-architect"
    assert data["role"] == "architect"
    assert data["allowed_tools"] == ["code_context", "view_file"]
    assert data["approval_required"] is True

    restored = AgentDefinition.from_dict(data)
    assert restored.agent_id == agent_def.agent_id
    assert restored.role == agent_def.role
    assert restored.allowed_tools == agent_def.allowed_tools
    assert restored.approval_required is True


def test_agent_registry_defaults() -> None:
    registry = AgentRegistry()
    agents = registry.list_agents()
    assert len(agents) >= 8

    planner = registry.get("planner-default")
    assert planner is not None
    assert planner.role == AgentRole.PLANNER

    backend = registry.get_default_for_role(AgentRole.BACKEND_CODER)
    assert backend.role == AgentRole.BACKEND_CODER


def test_role_policy_engine_permissions() -> None:
    engine = RolePolicyEngine()

    write_tool = ToolDefinition(
        name="replace_file_content",
        description="Edit file",
        input_schema={},
        permissions=frozenset({ToolPermission.WRITE}),
        handler=lambda ctx, args: None,
    )

    read_tool = ToolDefinition(
        name="view_file",
        description="View file",
        input_schema={},
        permissions=frozenset({ToolPermission.READ}),
        handler=lambda ctx, args: None,
    )

    # Planner should be denied write tool
    result_planner_write = engine.evaluate(AgentRole.PLANNER, write_tool)
    assert result_planner_write.decision == ToolDecision.DENY
    assert "prohibited from modifying workspace files" in result_planner_write.reason

    # Reviewer should be denied write tool
    result_reviewer_write = engine.evaluate(AgentRole.REVIEWER, write_tool)
    assert result_reviewer_write.decision == ToolDecision.DENY

    # Backend coder allowed write tool
    result_coder_write = engine.evaluate(AgentRole.BACKEND_CODER, write_tool)
    assert result_coder_write.decision == ToolDecision.ALLOW

    # Planner allowed view_file
    result_planner_read = engine.evaluate(AgentRole.PLANNER, read_tool)
    assert result_planner_read.decision == ToolDecision.ALLOW
