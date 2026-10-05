from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from .agent_role import AgentRole


class ReasoningLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ModelCapability:
    model_id: str
    provider: str
    context_window: int
    supports_tool_calling: bool = True
    supports_vision: bool = False
    reasoning_level: ReasoningLevel = ReasoningLevel.MEDIUM
    prompt_cost_per_1k: float = 0.0015  # USD per 1,000 prompt tokens
    completion_cost_per_1k: float = 0.0060  # USD per 1,000 completion tokens


@dataclass(slots=True)
class ExecutionBudget:
    max_cost_usd: float = 5.0
    max_tokens: int = 500_000
    max_duration_seconds: float = 600.0
    cost_spent_usd: float = 0.0
    tokens_used: int = 0
    duration_used_seconds: float = 0.0

    @property
    def is_exceeded(self) -> bool:
        return (
            self.cost_spent_usd >= self.max_cost_usd
            or self.tokens_used >= self.max_tokens
            or self.duration_used_seconds >= self.max_duration_seconds
        )

    @property
    def remaining_cost_usd(self) -> float:
        return max(0.0, self.max_cost_usd - self.cost_spent_usd)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens_used)

    def record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float | None = None,
        duration_seconds: float = 0.0,
        model_cap: ModelCapability | None = None,
    ) -> None:
        total_tokens = prompt_tokens + completion_tokens
        self.tokens_used += total_tokens
        self.duration_used_seconds += duration_seconds

        if cost_usd is not None:
            self.cost_spent_usd += cost_usd
        elif model_cap is not None:
            calc_cost = (
                (prompt_tokens / 1000.0) * model_cap.prompt_cost_per_1k
                + (completion_tokens / 1000.0) * model_cap.completion_cost_per_1k
            )
            self.cost_spent_usd += round(calc_cost, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost_usd,
            "max_tokens": self.max_tokens,
            "max_duration_seconds": self.max_duration_seconds,
            "cost_spent_usd": round(self.cost_spent_usd, 6),
            "tokens_used": self.tokens_used,
            "duration_used_seconds": round(self.duration_used_seconds, 2),
            "is_exceeded": self.is_exceeded,
            "remaining_cost_usd": round(self.remaining_cost_usd, 6),
            "remaining_tokens": self.remaining_tokens,
        }


class BudgetTracker:
    """Tracks and enforces execution budgets per task and plan step."""

    def __init__(self) -> None:
        self._task_budgets: dict[str, ExecutionBudget] = {}
        self._step_budgets: dict[str, ExecutionBudget] = {}

    def get_or_create_task_budget(
        self,
        task_id: str,
        max_cost_usd: float = 5.0,
        max_tokens: int = 500_000,
        max_duration_seconds: float = 600.0,
    ) -> ExecutionBudget:
        if task_id not in self._task_budgets:
            self._task_budgets[task_id] = ExecutionBudget(
                max_cost_usd=max_cost_usd,
                max_tokens=max_tokens,
                max_duration_seconds=max_duration_seconds,
            )
        return self._task_budgets[task_id]

    def set_task_budget(
        self,
        task_id: str,
        max_cost_usd: float,
        max_tokens: int = 500_000,
        max_duration_seconds: float = 600.0,
    ) -> ExecutionBudget:
        budget = ExecutionBudget(
            max_cost_usd=max_cost_usd,
            max_tokens=max_tokens,
            max_duration_seconds=max_duration_seconds,
        )
        self._task_budgets[task_id] = budget
        return budget

    def record_task_usage(
        self,
        task_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float = 0.0,
        model_cap: ModelCapability | None = None,
    ) -> ExecutionBudget:
        budget = self.get_or_create_task_budget(task_id)
        budget.record_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
            model_cap=model_cap,
        )
        return budget


# Catalog of standard models and their capabilities
DEFAULT_MODEL_CAPABILITIES: dict[str, ModelCapability] = {
    "gpt-4o": ModelCapability(
        model_id="gpt-4o",
        provider="openai",
        context_window=128_000,
        supports_tool_calling=True,
        supports_vision=True,
        reasoning_level=ReasoningLevel.HIGH,
        prompt_cost_per_1k=0.0025,
        completion_cost_per_1k=0.010,
    ),
    "gpt-4o-mini": ModelCapability(
        model_id="gpt-4o-mini",
        provider="openai",
        context_window=128_000,
        supports_tool_calling=True,
        reasoning_level=ReasoningLevel.LOW,
        prompt_cost_per_1k=0.00015,
        completion_cost_per_1k=0.0006,
    ),
    "claude-3-7-sonnet": ModelCapability(
        model_id="claude-3-7-sonnet",
        provider="anthropic",
        context_window=200_000,
        supports_tool_calling=True,
        supports_vision=True,
        reasoning_level=ReasoningLevel.HIGH,
        prompt_cost_per_1k=0.003,
        completion_cost_per_1k=0.015,
    ),
    "gemini-2.0-flash": ModelCapability(
        model_id="gemini-2.0-flash",
        provider="google",
        context_window=1_000_000,
        supports_tool_calling=True,
        supports_vision=True,
        reasoning_level=ReasoningLevel.MEDIUM,
        prompt_cost_per_1k=0.0001,
        completion_cost_per_1k=0.0004,
    ),
    "qwen-2.5-coder-32b": ModelCapability(
        model_id="qwen-2.5-coder-32b",
        provider="local",
        context_window=32_000,
        supports_tool_calling=True,
        reasoning_level=ReasoningLevel.MEDIUM,
        prompt_cost_per_1k=0.0,
        completion_cost_per_1k=0.0,
    ),
}


class CostPolicyEngine:
    """Selects the optimal model capability for an AgentRole based on role requirements and remaining budget."""

    def __init__(
        self,
        capabilities: dict[str, ModelCapability] | None = None,
    ) -> None:
        self._capabilities = capabilities or dict(DEFAULT_MODEL_CAPABILITIES)

    def route_model_for_role(
        self,
        role: AgentRole,
        budget: ExecutionBudget | None = None,
        preferred_provider: str | None = None,
    ) -> ModelCapability:
        # If budget is tight (< $0.50 remaining or > 80% used), downgrade to economical models
        is_frugal = budget is not None and (
            budget.remaining_cost_usd < 0.50 or budget.cost_spent_usd / max(1.0, budget.max_cost_usd) > 0.80
        )

        if is_frugal:
            # Prefer low-cost / mini model
            if "gemini-2.0-flash" in self._capabilities:
                return self._capabilities["gemini-2.0-flash"]
            if "gpt-4o-mini" in self._capabilities:
                return self._capabilities["gpt-4o-mini"]

        # Role-based optimal selection
        if role in (AgentRole.PLANNER, AgentRole.ARCHITECT, AgentRole.REVIEWER):
            # High-reasoning model
            if "claude-3-7-sonnet" in self._capabilities and preferred_provider in (None, "anthropic"):
                return self._capabilities["claude-3-7-sonnet"]
            if "gpt-4o" in self._capabilities:
                return self._capabilities["gpt-4o"]

        elif role in (AgentRole.BACKEND_CODER, AgentRole.UI_CODER):
            # Coding-specialized models
            if "claude-3-7-sonnet" in self._capabilities:
                return self._capabilities["claude-3-7-sonnet"]
            if "gpt-4o" in self._capabilities:
                return self._capabilities["gpt-4o"]

        elif role in (AgentRole.RESEARCHER, AgentRole.TESTER, AgentRole.SECURITY_REVIEWER):
            # High context or balanced model
            if "gemini-2.0-flash" in self._capabilities and preferred_provider in (None, "google"):
                return self._capabilities["gemini-2.0-flash"]
            if "gpt-4o-mini" in self._capabilities:
                return self._capabilities["gpt-4o-mini"]

        # Default fallback to any registered capability
        return next(iter(self._capabilities.values()))
