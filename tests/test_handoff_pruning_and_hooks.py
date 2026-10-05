import pytest
from core.agent_handoff_context import AgentHandoffContext
from core.agent_role import AgentRole
from core.agent_work_result import AgentFinding, TestExecutionResult


def test_agent_handoff_context_pruning():
    findings = tuple(
        AgentFinding(
            finding_id=f"find-{i}",
            kind="bug",
            title=f"Finding {i}",
            severity="low",
            summary=f"Issue {i}",
        )
        for i in range(10)
    )
    tests = tuple(
        TestExecutionResult(status="passed", passed=5, failed=0, summary=f"Suite {i}")
        for i in range(10)
    )
    notes = tuple(f"Note {i}" for i in range(10))

    ctx = AgentHandoffContext(
        task_id="task-123",
        target_role=AgentRole.UI_CODER,
        open_findings=findings,
        recent_tests=tests,
        handoff_notes=notes,
        latest_summary="Completed step",
    )

    pruned = ctx.prune_context(max_notes=2, max_findings=3, max_tests=2)
    assert len(pruned.open_findings) == 3
    assert len(pruned.recent_tests) == 2
    assert len(pruned.handoff_notes) == 2
    assert pruned.latest_summary == "Completed step"
