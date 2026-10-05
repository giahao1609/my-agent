from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .agent_role import AgentDefinition, AgentRegistry, AgentRole, RolePolicyEngine, default_agent_registry
from .context import ExecutionContext
from .handoff_contracts import (
    ArchitectureProposal,
    DecisionRequiredResult,
    ImplementationResult,
    ResearchResult,
    ReviewResult,
    SecurityReviewResult,
    TestResult,
    UiImplementationResult,
)
from .tools import ToolDefinition


@dataclass(frozen=True, slots=True)
class SpecialistExecutionRequest:
    task_id: str
    agent_id: str
    role: AgentRole
    instruction: str
    context: ExecutionContext
    plan_id: str | None = None
    step_id: str | None = None
    scoped_context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "role": self.role.value,
            "instruction": self.instruction,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "scoped_context": dict(self.scoped_context),
        }


@dataclass(frozen=True, slots=True)
class SpecialistExecutionResult:
    status: str  # 'completed', 'failed', 'decision_required', 'rework_required'
    summary: str
    output_contract: str
    payload: dict[str, Any] = field(default_factory=dict)
    decision: DecisionRequiredResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_decision_required(self) -> bool:
        return self.status == "decision_required" or self.decision is not None

    @property
    def is_success(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "output_contract": self.output_contract,
            "payload": self.payload,
            "decision": self.decision.to_dict() if self.decision else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpecialistExecutionResult:
        decision_data = data.get("decision")
        decision = DecisionRequiredResult.from_dict(decision_data) if decision_data else None
        return cls(
            status=data["status"],
            summary=data["summary"],
            output_contract=data.get("output_contract", "GenericResult"),
            payload=data.get("payload", {}),
            decision=decision,
            metadata=data.get("metadata", {}),
        )


class SpecialistCapabilityFilter:
    """Computes effective tools for a specialist agent under strict least-privilege policy."""

    def __init__(self, policy_engine: RolePolicyEngine | None = None) -> None:
        self._policy_engine = policy_engine or RolePolicyEngine()

    def get_effective_tools(
        self,
        agent_def: AgentDefinition,
        all_tools: Sequence[ToolDefinition],
    ) -> tuple[ToolDefinition, ...]:
        effective: list[ToolDefinition] = []
        for tool in all_tools:
            # 1. Agent definition forbidden list
            if tool.name in agent_def.forbidden_tools:
                continue

            # 2. Agent definition explicit allowed list (if defined)
            if agent_def.allowed_tools and tool.name not in agent_def.allowed_tools:
                continue

            # 3. Role Policy Engine evaluation
            eval_res = self._policy_engine.evaluate(agent_def.role, tool)
            from .tool_policy import ToolDecision
            if eval_res.decision == ToolDecision.DENY:
                continue

            effective.append(tool)

        return tuple(effective)


class SpecialistRouter:
    """Deterministic routing and resolution of specialist agent definitions."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        policy_engine: RolePolicyEngine | None = None,
    ) -> None:
        self._registry = registry or default_agent_registry
        self._policy_engine = policy_engine or RolePolicyEngine()
        self._filter = SpecialistCapabilityFilter(self._policy_engine)

    def resolve_agent(self, role: AgentRole, agent_id: str | None = None) -> AgentDefinition:
        if agent_id:
            agent = self._registry.get(agent_id)
            if agent and agent.role == role:
                return agent
        return self._registry.get_default_for_role(role)

    def get_tools_for_role(
        self,
        role: AgentRole,
        all_tools: Sequence[ToolDefinition],
        agent_id: str | None = None,
    ) -> tuple[ToolDefinition, ...]:
        agent_def = self.resolve_agent(role, agent_id)
        return self._filter.get_effective_tools(agent_def, all_tools)
