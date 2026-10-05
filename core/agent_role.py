from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .tool_policy import ToolDecision, ToolPolicyResult
from .tools import ToolDefinition, ToolPermission


class AgentRole(StrEnum):
    PLANNER = 'planner'
    ARCHITECT = 'architect'
    RESEARCHER = 'researcher'
    BACKEND_CODER = 'backend_coder'
    UI_CODER = 'ui_coder'
    TESTER = 'tester'
    SECURITY_REVIEWER = 'security_reviewer'
    REVIEWER = 'reviewer'
    DB_MIGRATION = 'db_migration'
    PERFORMANCE = 'performance'
    DOCUMENTATION = 'documentation'
    RELEASE = 'release'
    USER_INTERFACE = 'user_interface'


class RolePolicyViolationError(Exception):
    """Raised when an agent attempts an action or tool forbidden by its role policy."""
    pass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    role: AgentRole
    name: str
    description: str
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    input_contract: str = 'Task'
    output_contract: str = 'ImplementationResult'
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            'agent_id': self.agent_id,
            'role': self.role.value,
            'name': self.name,
            'description': self.description,
            'allowed_tools': list(self.allowed_tools),
            'forbidden_tools': list(self.forbidden_tools),
            'input_contract': self.input_contract,
            'output_contract': self.output_contract,
            'approval_required': self.approval_required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDefinition:
        return cls(
            agent_id=data['agent_id'],
            role=AgentRole(data['role']),
            name=data['name'],
            description=data['description'],
            allowed_tools=tuple(data.get('allowed_tools', ())),
            forbidden_tools=tuple(data.get('forbidden_tools', ())),
            input_contract=data.get('input_contract', 'Task'),
            output_contract=data.get('output_contract', 'ImplementationResult'),
            approval_required=data.get('approval_required', False),
        )


class RolePolicyEngine:
    """Enforces tool access policy based on assigned AgentRole."""

    # Explicit tool permissions by role
    ROLE_RULES: dict[AgentRole, dict[str, tuple[str, ...]]] = {
        AgentRole.PLANNER: {
            'allowed': ('propose_task_plan', 'view_file', 'list_dir', 'grep_search', 'code_context', 'my_agent_status'),
            'forbidden': ('replace_file_content', 'multi_replace_file_content', 'write_to_file', 'run_command'),
        },
        AgentRole.ARCHITECT: {
            'allowed': ('code_context', 'code_dependencies', 'code_dependents', 'impact_analysis', 'find_code_symbol', 'view_file', 'list_dir', 'grep_search'),
            'forbidden': ('replace_file_content', 'multi_replace_file_content', 'write_to_file', 'run_command'),
        },
        AgentRole.RESEARCHER: {
            'allowed': ('view_file', 'list_dir', 'grep_search', 'read_url_content', 'search_web', 'code_context', 'recall_memory'),
            'forbidden': ('replace_file_content', 'multi_replace_file_content', 'write_to_file', 'run_command'),
        },
        AgentRole.TESTER: {
            'allowed': ('run_command', 'view_file', 'list_dir', 'grep_search', 'code_context'),
            'forbidden': ('replace_file_content', 'multi_replace_file_content', 'write_to_file'),
        },
        AgentRole.SECURITY_REVIEWER: {
            'allowed': ('run_command', 'view_file', 'list_dir', 'grep_search', 'code_context'),
            'forbidden': ('replace_file_content', 'multi_replace_file_content', 'write_to_file'),
        },
        AgentRole.REVIEWER: {
            'allowed': ('view_file', 'list_dir', 'grep_search', 'code_context', 'run_command'),
            'forbidden': ('replace_file_content', 'multi_replace_file_content', 'write_to_file'),
        },
        # Coder roles: can read, write, and run — but cannot perform destructive ops
        AgentRole.BACKEND_CODER: {
            'allowed': (
                'view_file', 'list_dir', 'grep_search', 'code_context',
                'replace_file_content', 'multi_replace_file_content', 'write_to_file',
                'run_command', 'find_code_symbol', 'code_dependencies', 'code_dependents',
                'impact_analysis',
            ),
            'forbidden': ('delete_path', 'drop_database', 'drop_table'),
        },
        AgentRole.UI_CODER: {
            'allowed': (
                'view_file', 'list_dir', 'grep_search', 'code_context',
                'replace_file_content', 'multi_replace_file_content', 'write_to_file',
                'run_command', 'find_code_symbol', 'generate_image',
            ),
            'forbidden': ('delete_path', 'drop_database', 'drop_table'),
        },
    }

    def evaluate(
        self,
        role: AgentRole,
        tool: ToolDefinition,
    ) -> ToolPolicyResult:
        rules = self.ROLE_RULES.get(role)

        # Non-coder roles attempting production code writes
        if role in (
            AgentRole.PLANNER,
            AgentRole.ARCHITECT,
            AgentRole.RESEARCHER,
            AgentRole.TESTER,
            AgentRole.SECURITY_REVIEWER,
            AgentRole.REVIEWER,
        ):
            if ToolPermission.WRITE in tool.permissions:
                return ToolPolicyResult(
                    ToolDecision.DENY,
                    f"role '{role.value}' is prohibited from modifying workspace files",
                )

        if rules:
            if tool.name in rules.get('forbidden', ()):
                return ToolPolicyResult(
                    ToolDecision.DENY,
                    f"tool '{tool.name}' is explicitly forbidden for role '{role.value}'",
                )
            if rules.get('allowed') and tool.name not in rules['allowed']:
                # Tool is not in the role's explicit allowlist — deny access.
                # This enforces least-privilege: roles may only use tools they
                # are explicitly permitted to use, not all non-forbidden tools.
                return ToolPolicyResult(
                    ToolDecision.DENY,
                    f"tool '{tool.name}' is not in the allowed list for role '{role.value}'",
                )

        return ToolPolicyResult(ToolDecision.ALLOW, f"role '{role.value}' is authorized for tool '{tool.name}'")


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._register_defaults()

    def register(self, agent_def: AgentDefinition) -> None:
        self._agents[agent_def.agent_id] = agent_def

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self._agents.get(agent_id)

    def get_default_for_role(self, role: AgentRole) -> AgentDefinition:
        for agent in self._agents.values():
            if agent.role == role:
                return agent
        return AgentDefinition(
            agent_id=f"default-{role.value}",
            role=role,
            name=f"Default {role.value.replace('_', ' ').title()}",
            description=f"Default agent for role {role.value}",
        )

    def list_agents(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def _register_defaults(self) -> None:
        defaults = [
            AgentDefinition(
                agent_id="planner-default",
                role=AgentRole.PLANNER,
                name="System Planner Agent",
                description="Decomposes objectives into structured execution plans.",
                allowed_tools=("propose_task_plan", "view_file", "list_dir", "grep_search", "code_context"),
                forbidden_tools=("replace_file_content", "write_to_file"),
                input_contract="Task",
                output_contract="PlanProposal",
            ),
            AgentDefinition(
                agent_id="architect-default",
                role=AgentRole.ARCHITECT,
                name="System Architect Agent",
                description="Analyzes dependencies, boundaries, and system interfaces.",
                allowed_tools=("code_context", "code_dependencies", "code_dependents", "impact_analysis", "find_code_symbol", "view_file"),
                forbidden_tools=("replace_file_content", "write_to_file"),
                input_contract="Task",
                output_contract="ArchitectureProposal",
            ),
            AgentDefinition(
                agent_id="researcher-default",
                role=AgentRole.RESEARCHER,
                name="Research Agent",
                description="Inspects documentation, web, upstreams, and code graph.",
                allowed_tools=("view_file", "list_dir", "grep_search", "read_url_content", "search_web", "code_context"),
                forbidden_tools=("replace_file_content", "write_to_file"),
                input_contract="Task",
                output_contract="ResearchResult",
            ),
            AgentDefinition(
                agent_id="backend-coder-default",
                role=AgentRole.BACKEND_CODER,
                name="Backend Coder Agent",
                description="Implements backend business logic, domain models, and APIs.",
                input_contract="PlanStep",
                output_contract="ImplementationResult",
            ),
            AgentDefinition(
                agent_id="ui-coder-default",
                role=AgentRole.UI_CODER,
                name="UI Coder Agent",
                description="Implements UI components and styling according to project design system.",
                input_contract="PlanStep",
                output_contract="UiImplementationResult",
            ),
            AgentDefinition(
                agent_id="tester-default",
                role=AgentRole.TESTER,
                name="Test Agent",
                description="Executes project tests and verifies regression state.",
                allowed_tools=("run_command", "view_file", "list_dir", "grep_search"),
                forbidden_tools=("replace_file_content", "write_to_file"),
                input_contract="PlanStep",
                output_contract="TestResult",
            ),
            AgentDefinition(
                agent_id="security-reviewer-default",
                role=AgentRole.SECURITY_REVIEWER,
                name="Security Reviewer Agent",
                description="Analyzes security scanner findings and assesses vulnerabilities.",
                allowed_tools=("run_command", "view_file", "list_dir", "grep_search"),
                forbidden_tools=("replace_file_content", "write_to_file"),
                input_contract="PlanStep",
                output_contract="SecurityReviewResult",
            ),
            AgentDefinition(
                agent_id="reviewer-default",
                role=AgentRole.REVIEWER,
                name="Code Reviewer Agent",
                description="Inspects diffs, test evidence, and security findings to approve or reject work.",
                allowed_tools=("view_file", "list_dir", "grep_search", "code_context"),
                forbidden_tools=("replace_file_content", "write_to_file"),
                input_contract="PlanStep",
                output_contract="ReviewResult",
            ),
            AgentDefinition(
                agent_id="myagent-front",
                role=AgentRole.USER_INTERFACE,
                name="MyAgent",
                description="Unified user-facing conversational interface and software engineering partner.",
                allowed_tools=(),
                forbidden_tools=("replace_file_content", "write_to_file", "run_command"),
                input_contract="FrontAgentRequest",
                output_contract="FrontAgentResponse",
            ),
        ]
        for d in defaults:
            self.register(d)


default_agent_registry = AgentRegistry()
