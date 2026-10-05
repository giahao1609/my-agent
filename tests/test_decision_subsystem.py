from __future__ import annotations

import tempfile
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


@pytest.mark.asyncio
async def test_decision_record_lifecycle():
    opt_a = DecisionOption(
        option_id="opt-a",
        title="Option A",
        description="Standard approach",
        trade_offs="Takes standard time",
        recommended=True,
    )
    opt_b = DecisionOption(
        option_id="opt-b",
        title="Option B",
        description="Fast approach",
        trade_offs="Less thorough",
    )

    rec = DecisionRecord(
        decision_id="dec-1",
        task_id="task-1",
        step_id="step-1",
        severity=DecisionSeverity.HIGH,
        state=DecisionState.OPEN,
        prompt="Choose an architectural pattern",
        options=(opt_a, opt_b),
    )

    assert rec.is_open()
    assert rec.get_option("opt-a") == opt_a
    assert rec.get_option("unknown") is None

    # Resolve decision
    resolved = rec.resolve(selected_option_id="opt-a", rationale="Safer architecture")
    assert resolved.state == DecisionState.RESOLVED
    assert resolved.selected_option_id == "opt-a"
    assert resolved.rationale == "Safer architecture"
    assert resolved.resolved_at is not None

    # Cannot resolve already resolved
    with pytest.raises(ValueError, match="cannot resolve"):
        resolved.resolve("opt-b")

    # Cannot resolve with invalid option
    rec2 = DecisionRecord(
        decision_id="dec-2",
        task_id="task-1",
        step_id=None,
        severity=DecisionSeverity.LOW,
        state=DecisionState.OPEN,
        prompt="Prompt 2",
        options=(opt_a, opt_b),
    )
    with pytest.raises(ValueError, match="invalid option_id"):
        rec2.resolve("opt-non-existent")


@pytest.mark.asyncio
async def test_decision_policy():
    opt_a = DecisionOption(option_id="a", title="A", description="Desc A")
    opt_b = DecisionOption(option_id="b", title="B", description="Desc B")

    # Less than 2 options
    with pytest.raises(ValueError, match="at least 2 distinct options"):
        DecisionPolicy.validate_creation(severity=DecisionSeverity.MEDIUM, options=[opt_a])

    # Duplicate option IDs
    opt_a_dup = DecisionOption(option_id="a", title="A dup", description="Desc A dup")
    with pytest.raises(ValueError, match="must be unique"):
        DecisionPolicy.validate_creation(severity=DecisionSeverity.MEDIUM, options=[opt_a, opt_a_dup])

    # Severity enforcement
    assert DecisionPolicy.enforce_minimum_severity(DecisionSeverity.HIGH, DecisionSeverity.LOW) == DecisionSeverity.HIGH
    assert DecisionPolicy.enforce_minimum_severity(DecisionSeverity.LOW, DecisionSeverity.HIGH) == DecisionSeverity.HIGH
    assert DecisionPolicy.enforce_minimum_severity(DecisionSeverity.MEDIUM, DecisionSeverity.MEDIUM) == DecisionSeverity.MEDIUM


@pytest.mark.asyncio
async def test_sqlite_decision_store_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_decisions.db"
        store = SQLiteDecisionStore(db_path)
        await store.initialize()

        opt_a = DecisionOption(option_id="opt-1", title="Plan A", description="Desc 1", recommended=True)
        opt_b = DecisionOption(option_id="opt-2", title="Plan B", description="Desc 2")

        rec = DecisionRecord(
            decision_id="dec-persist-1",
            task_id="task-100",
            step_id="step-200",
            severity=DecisionSeverity.MEDIUM,
            state=DecisionState.OPEN,
            prompt="Which database migration strategy?",
            options=(opt_a, opt_b),
        )

        await store.save(rec)

        # Retrieve and verify
        loaded = await store.get("dec-persist-1")
        assert loaded is not None
        assert loaded.decision_id == "dec-persist-1"
        assert loaded.task_id == "task-100"
        assert loaded.step_id == "step-200"
        assert len(loaded.options) == 2
        assert loaded.options[0].recommended is True
        assert loaded.is_open()

        # Check list open
        open_list = await store.list_open(task_id="task-100")
        assert len(open_list) == 1

        # Resolve and reload in fresh store instance (survive restart)
        service = DecisionService(store)
        resolved = await service.resolve_decision("dec-persist-1", selected_option_id="opt-1")
        assert resolved.state == DecisionState.RESOLVED

        fresh_store = SQLiteDecisionStore(db_path)
        await fresh_store.initialize()
        reloaded = await fresh_store.get("dec-persist-1")
        assert reloaded is not None
        assert reloaded.state == DecisionState.RESOLVED
        assert reloaded.selected_option_id == "opt-1"

        open_after = await fresh_store.list_open(task_id="task-100")
        assert len(open_after) == 0
