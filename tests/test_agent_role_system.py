from __future__ import annotations

from types import MappingProxyType

import pytest

from core.agent_role import (
    AgentDefinition,
    AgentRegistry,
    AgentRole,
    RolePolicyEngine,
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


def _make_tool(name: str, permission: ToolPermission) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool {name}",
        input_schema={},
        permissions=frozenset({permission}),
        handler=lambda ctx, args: None,
    )


@pytest.mark.parametrize(
    "unconfigured_role",
    [
        AgentRole.DB_MIGRATION,
        AgentRole.PERFORMANCE,
        AgentRole.DOCUMENTATION,
        AgentRole.RELEASE,
        AgentRole.USER_INTERFACE,
    ],
)
@pytest.mark.parametrize(
    ("tool_name", "permission"),
    [
        ("view_file", ToolPermission.READ),
        ("write_to_file", ToolPermission.WRITE),
        ("run_command", ToolPermission.EXECUTE),
        ("search_web", ToolPermission.NETWORK),
        ("delete_path", ToolPermission.DESTRUCTIVE),
    ],
)
def test_unconfigured_roles_deny_all_tool_permissions_fail_closed(
    unconfigured_role: AgentRole,
    tool_name: str,
    permission: ToolPermission,
) -> None:
    engine = RolePolicyEngine()
    tool = _make_tool(tool_name, permission)

    result = engine.evaluate(unconfigured_role, tool)

    assert result.decision == ToolDecision.DENY
    assert unconfigured_role.value in result.reason
    assert "no configured policy rules" in result.reason


def _engine_with_role_rule(role: AgentRole, rule: object) -> RolePolicyEngine:
    class OverriddenRolePolicyEngine(RolePolicyEngine):
        def __init__(self) -> None:
            self.ROLE_RULES = {  # type: ignore[misc]
                **RolePolicyEngine.ROLE_RULES,
                role: rule,
            }

    return OverriddenRolePolicyEngine()


@pytest.mark.parametrize(
    ("override_rule", "expected_reason_fragment"),
    [
        ({}, "no configured policy rules"),
        ({"forbidden": ("delete_path",)}, "valid nonempty allowed tool list"),
        ({"allowed": (), "forbidden": ()}, "valid nonempty allowed tool list"),
        ({"allowed": ("", "   "), "forbidden": ()}, "valid nonempty allowed tool list"),
        ({"allowed": "view_file", "forbidden": ()}, "valid nonempty allowed tool list"),
    ],
)
def test_missing_or_empty_allowlist_denies_implicitly(
    override_rule: dict[str, object],
    expected_reason_fragment: str,
) -> None:
    engine = _engine_with_role_rule(AgentRole.DB_MIGRATION, override_rule)
    read_tool = _make_tool("view_file", ToolPermission.READ)

    result = engine.evaluate(AgentRole.DB_MIGRATION, read_tool)

    assert result.decision == ToolDecision.DENY
    assert AgentRole.DB_MIGRATION.value in result.reason
    assert expected_reason_fragment in result.reason


def test_specialist_capability_filter_excludes_unconfigured_role_tools() -> None:
    from core.specialist_execution import SpecialistCapabilityFilter

    engine = RolePolicyEngine()
    cap_filter = SpecialistCapabilityFilter(engine)
    registry = AgentRegistry()

    all_tools = [
        _make_tool("view_file", ToolPermission.READ),
        _make_tool("write_to_file", ToolPermission.WRITE),
        _make_tool("run_command", ToolPermission.EXECUTE),
        _make_tool("search_web", ToolPermission.NETWORK),
        _make_tool("delete_path", ToolPermission.DESTRUCTIVE),
    ]

    for role in (
        AgentRole.DB_MIGRATION,
        AgentRole.PERFORMANCE,
        AgentRole.DOCUMENTATION,
        AgentRole.RELEASE,
        AgentRole.USER_INTERFACE,
    ):
        agent_def = registry.get_default_for_role(role)
        assert cap_filter.get_effective_tools(agent_def, all_tools) == ()

    # Even if an unconfigured role's AgentDefinition specifies allowed_tools,
    # RolePolicyEngine must still deny and filter them out.
    elevated_user_interface_def = AgentDefinition(
        agent_id="ui-elevated-test",
        role=AgentRole.USER_INTERFACE,
        name="Elevated Front Test",
        description="Should still be blocked by RolePolicyEngine",
        allowed_tools=("view_file", "search_web"),
    )
    assert cap_filter.get_effective_tools(elevated_user_interface_def, all_tools) == ()


def test_configured_roles_regression_behavior_preserved() -> None:
    engine = RolePolicyEngine()

    read_tool = _make_tool("view_file", ToolPermission.READ)
    code_context_tool = _make_tool("code_context", ToolPermission.READ)
    write_tool = _make_tool("write_to_file", ToolPermission.WRITE)
    run_tool = _make_tool("run_command", ToolPermission.EXECUTE)
    delete_tool = _make_tool("delete_path", ToolPermission.DESTRUCTIVE)
    unknown_read_tool = _make_tool("custom_unlisted_read", ToolPermission.READ)

    # Configured read-only roles keep access to explicitly allowed read tools
    for role in (AgentRole.PLANNER, AgentRole.ARCHITECT, AgentRole.RESEARCHER, AgentRole.REVIEWER):
        assert engine.evaluate(role, read_tool).decision == ToolDecision.ALLOW
        assert engine.evaluate(role, code_context_tool).decision == ToolDecision.ALLOW
        assert engine.evaluate(role, unknown_read_tool).decision == ToolDecision.DENY

    # Non-coder roles remain denied on workspace writes
    for role in (
        AgentRole.PLANNER,
        AgentRole.ARCHITECT,
        AgentRole.RESEARCHER,
        AgentRole.TESTER,
        AgentRole.SECURITY_REVIEWER,
        AgentRole.REVIEWER,
    ):
        res = engine.evaluate(role, write_tool)
        assert res.decision == ToolDecision.DENY
        assert "prohibited from modifying workspace files" in res.reason

    # Explicitly forbidden tools remain denied with specific forbidden reason
    assert "explicitly forbidden" in engine.evaluate(AgentRole.PLANNER, run_tool).reason
    assert engine.evaluate(AgentRole.PLANNER, run_tool).decision == ToolDecision.DENY
    assert "explicitly forbidden" in engine.evaluate(AgentRole.ARCHITECT, run_tool).reason

    # Configured coder roles retain allowed coding tools while destructive tools stay forbidden
    for coder_role in (AgentRole.BACKEND_CODER, AgentRole.UI_CODER):
        assert engine.evaluate(coder_role, read_tool).decision == ToolDecision.ALLOW
        assert engine.evaluate(coder_role, write_tool).decision == ToolDecision.ALLOW
        assert engine.evaluate(coder_role, run_tool).decision == ToolDecision.ALLOW
        deny_res = engine.evaluate(coder_role, delete_tool)
        assert deny_res.decision == ToolDecision.DENY
        assert "explicitly forbidden" in deny_res.reason
        assert engine.evaluate(coder_role, unknown_read_tool).decision == ToolDecision.DENY


@pytest.mark.parametrize(
    "malformed_forbidden",
    [
        "run_command",
        None,
        123,
        {"run_command": True},
        ("run_command", None),
        ("run_command", ""),
        ("   ",),
        ("run_command", 42),
    ],
)
def test_malformed_forbidden_configuration_denies_fail_closed(
    malformed_forbidden: object,
) -> None:
    engine = _engine_with_role_rule(
        AgentRole.DB_MIGRATION,
        {
            "allowed": ("view_file",),
            "forbidden": malformed_forbidden,
        },
    )
    allowed_read_tool = _make_tool("view_file", ToolPermission.READ)

    result = engine.evaluate(AgentRole.DB_MIGRATION, allowed_read_tool)

    assert result.decision == ToolDecision.DENY
    assert AgentRole.DB_MIGRATION.value in result.reason
    assert "invalid forbidden tool configuration" in result.reason


def test_omitted_forbidden_field_behaves_as_empty_forbidden_list() -> None:
    engine = _engine_with_role_rule(
        AgentRole.DB_MIGRATION,
        {"allowed": ("view_file",)},
    )
    assert engine.evaluate(AgentRole.DB_MIGRATION, _make_tool("view_file", ToolPermission.READ)).decision == ToolDecision.ALLOW
    assert engine.evaluate(AgentRole.DB_MIGRATION, _make_tool("write_to_file", ToolPermission.WRITE)).decision == ToolDecision.DENY


@pytest.mark.parametrize(
    "mixed_allowed",
    [
        ("view_file", ""),
        ("view_file", "   "),
        ("view_file", None),
        ("view_file", 123),
        ["", "view_file"],
    ],
)
def test_mixed_valid_and_invalid_allowlist_entries_deny_entire_allowlist(
    mixed_allowed: object,
) -> None:
    engine = _engine_with_role_rule(
        AgentRole.DB_MIGRATION,
        {
            "allowed": mixed_allowed,
            "forbidden": ("delete_path",),
        },
    )
    valid_read_tool = _make_tool("view_file", ToolPermission.READ)

    result = engine.evaluate(AgentRole.DB_MIGRATION, valid_read_tool)

    assert result.decision == ToolDecision.DENY
    assert AgentRole.DB_MIGRATION.value in result.reason
    assert "valid nonempty allowed tool list" in result.reason


@pytest.mark.parametrize(
    "malformed_rule_entry",
    [
        "allowed: view_file",
        ["view_file"],
        ("view_file",),
        {"view_file"},
        123,
        True,
    ],
)
def test_non_mapping_role_policy_entry_denies_fail_closed(
    malformed_rule_entry: object,
) -> None:
    engine = _engine_with_role_rule(AgentRole.DB_MIGRATION, malformed_rule_entry)
    read_tool = _make_tool("view_file", ToolPermission.READ)

    result = engine.evaluate(AgentRole.DB_MIGRATION, read_tool)

    assert result.decision == ToolDecision.DENY
    assert AgentRole.DB_MIGRATION.value in result.reason
    assert "invalid policy rule configuration" in result.reason


def test_mapping_proxy_role_policy_entry_remains_supported() -> None:
    engine = _engine_with_role_rule(
        AgentRole.DB_MIGRATION,
        MappingProxyType({"allowed": ("view_file",), "forbidden": ("delete_path",)}),
    )
    assert engine.evaluate(AgentRole.DB_MIGRATION, _make_tool("view_file", ToolPermission.READ)).decision == ToolDecision.ALLOW
    assert engine.evaluate(AgentRole.DB_MIGRATION, _make_tool("delete_path", ToolPermission.DESTRUCTIVE)).decision == ToolDecision.DENY
