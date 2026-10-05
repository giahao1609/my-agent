from __future__ import annotations

import pytest

from core.agent_role import AgentRole
from core.budget_router import (
    BudgetTracker,
    CostPolicyEngine,
    DEFAULT_MODEL_CAPABILITIES,
    ExecutionBudget,
    ModelCapability,
    ReasoningLevel,
)


def test_execution_budget_usage_and_threshold():
    budget = ExecutionBudget(max_cost_usd=2.0, max_tokens=10_000, max_duration_seconds=100.0)
    assert budget.is_exceeded is False
    assert budget.remaining_cost_usd == 2.0
    assert budget.remaining_tokens == 10_000

    cap = ModelCapability(
        model_id="test-model",
        provider="test",
        context_window=8000,
        prompt_cost_per_1k=0.01,
        completion_cost_per_1k=0.02,
    )
    # Record 50,000 prompt tokens (0.50 USD) + 50,000 completion tokens (1.00 USD) = 1.50 USD
    budget.record_usage(prompt_tokens=50_000, completion_tokens=50_000, duration_seconds=30.0, model_cap=cap)
    assert budget.cost_spent_usd == pytest.approx(1.50, rel=1e-2)
    assert budget.tokens_used == 100_000
    assert budget.is_exceeded is True  # Tokens exceeded max_tokens


def test_budget_tracker_per_task():
    tracker = BudgetTracker()
    b1 = tracker.get_or_create_task_budget("task-1", max_cost_usd=10.0)
    assert b1.max_cost_usd == 10.0

    b2 = tracker.record_task_usage(
        "task-1",
        prompt_tokens=1000,
        completion_tokens=500,
        duration_seconds=5.0,
        model_cap=DEFAULT_MODEL_CAPABILITIES["gpt-4o"],
    )
    assert b2.tokens_used == 1500
    assert b2.cost_spent_usd > 0


def test_cost_policy_engine_role_routing():
    engine = CostPolicyEngine()

    # Planner should route to high reasoning model (Claude or GPT-4o)
    planner_model = engine.route_model_for_role(AgentRole.PLANNER)
    assert planner_model.model_id in ("claude-3-7-sonnet", "gpt-4o")

    # Researcher / Tester should route to balanced model
    tester_model = engine.route_model_for_role(AgentRole.TESTER)
    assert tester_model.model_id in ("gemini-2.0-flash", "gpt-4o-mini")

    # If budget is frugal (< $0.50 remaining), route to economical model
    tight_budget = ExecutionBudget(max_cost_usd=5.0, cost_spent_usd=4.8)
    frugal_model = engine.route_model_for_role(AgentRole.PLANNER, budget=tight_budget)
    assert frugal_model.model_id in ("gemini-2.0-flash", "gpt-4o-mini")
