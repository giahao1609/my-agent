from __future__ import annotations

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from core.decision import (
    DecisionOption,
    DecisionPolicy,
    DecisionRecord,
    DecisionSeverity,
    DecisionState,
)
from core.decision_service import DecisionService
from persistence.sqlite_decision_store import SQLiteDecisionStore


@pytest.fixture
async def decision_service():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "decision_domain_test.db"
        store = SQLiteDecisionStore(db_path)
        await store.initialize()
        yield DecisionService(store)


@pytest.mark.asyncio
async def test_decision_service_validation_errors(decision_service: DecisionService):
    opt_a = DecisionOption(option_id="opt-a", title="A", description="Option A")
    opt_b = DecisionOption(option_id="opt-b", title="B", description="Option B")

    with pytest.raises(ValueError, match="task_id must not be empty"):
        await decision_service.create_decision(
            task_id="   ",
            prompt="Choose something",
            severity=DecisionSeverity.MEDIUM,
            options=[opt_a, opt_b],
        )

    with pytest.raises(ValueError, match="prompt must not be empty"):
        await decision_service.create_decision(
            task_id="task-1",
            prompt="   ",
            severity=DecisionSeverity.MEDIUM,
            options=[opt_a, opt_b],
        )


@pytest.mark.asyncio
async def test_decision_service_resolve_and_cancel_errors(decision_service: DecisionService):
    with pytest.raises(KeyError, match="decision not found"):
        await decision_service.resolve_decision("non-existent-id", selected_option_id="opt-a")

    with pytest.raises(KeyError, match="decision not found"):
        await decision_service.cancel_decision("non-existent-id", rationale="cancelled")


@pytest.mark.asyncio
async def test_decision_service_cancel_flow(decision_service: DecisionService):
    opt_a = DecisionOption(option_id="opt-1", title="A", description="Option 1")
    opt_b = DecisionOption(option_id="opt-2", title="B", description="Option 2")

    rec = await decision_service.create_decision(
        task_id="task-cancel",
        prompt="Cancel me",
        severity=DecisionSeverity.LOW,
        options=[opt_a, opt_b],
    )

    assert rec.is_open()
    cancelled = await decision_service.cancel_decision(rec.decision_id, rationale="No longer required")
    assert cancelled.state == DecisionState.CANCELLED
    assert cancelled.rationale == "No longer required"

    # Verify retrieved from store is cancelled
    fetched = await decision_service.get_decision(rec.decision_id)
    assert fetched is not None
    assert fetched.state == DecisionState.CANCELLED


@pytest.mark.asyncio
async def test_decision_service_expiry_and_listing(decision_service: DecisionService):
    opt_a = DecisionOption(option_id="opt-1", title="A", description="Option 1")
    opt_b = DecisionOption(option_id="opt-2", title="B", description="Option 2")

    # Create expired LOW severity decision manually via store
    past_expiry = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    stale_rec = DecisionRecord(
        decision_id="dec-stale",
        task_id="task-expiry",
        step_id=None,
        severity=DecisionSeverity.LOW,
        state=DecisionState.OPEN,
        prompt="Expiring decision",
        options=(opt_a, opt_b),
        expires_at=past_expiry,
    )
    await decision_service._store.save(stale_rec)

    # Expire stale decisions
    expired_list = await decision_service.expire_stale_decisions()
    assert len(expired_list) == 1
    assert expired_list[0].decision_id == "dec-stale"
    assert expired_list[0].state == DecisionState.EXPIRED

    # Check list by task
    by_task = await decision_service.list_decisions_by_task("task-expiry")
    assert len(by_task) == 1
    assert by_task[0].decision_id == "dec-stale"
    assert by_task[0].state == DecisionState.EXPIRED

    # Check list open decisions
    open_decs = await decision_service.list_open_decisions()
    assert len(open_decs) == 0
