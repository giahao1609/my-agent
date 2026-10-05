from __future__ import annotations

from core.agent_work_result import (
    AgentArtifact,
    AgentDecisionReference,
    AgentFinding,
    AgentWorkResult,
    CommandExecution,
    FileChange,
    TestExecutionResult,
    redact_text,
)
from core.handoff_contracts import DecisionOptionDraft, DecisionRequiredResult


def test_agent_work_result_serialization():
    dec_req = DecisionRequiredResult(
        step_id="step-1",
        prompt="Choose database driver",
        severity="medium",
        options=(
            DecisionOptionDraft(
                option_id="opt-pg",
                title="PostgreSQL",
                description="Use Postgres async driver",
                trade_offs="Robust but heavier",
            ),
        ),
    )

    work_result = AgentWorkResult(
        run_id="run-001",
        status="completed",
        summary="Refactored authentication middleware",
        changed_files=(
            FileChange(path="src/auth.ts", change_type="modified", diff_summary="+10 -2 lines"),
        ),
        created_files=("src/auth.test.ts",),
        deleted_files=("src/legacy_auth.ts",),
        commands_run=(
            CommandExecution(command="npm run lint", exit_code=0, duration_ms=120),
        ),
        tests=(
            TestExecutionResult(
                command="npm test",
                framework="jest",
                status="passed",
                passed=5,
                failed=0,
                summary="5 tests passed in 0.4s",
            ),
        ),
        findings=(
            AgentFinding(
                finding_id="find-1",
                kind="security",
                severity="low",
                title="Missing Rate Limiting Header",
                summary="Auth endpoint lacks X-RateLimit header",
            ),
        ),
        warnings=("Node deprecation warning in dependency",),
        artifacts=(
            AgentArtifact(
                artifact_id="art-1",
                kind="diff",
                uri="artifacts/diff.patch",
                description="Full code patch",
            ),
        ),
        decisions_made=(
            AgentDecisionReference(
                decision_id="dec-1",
                prompt="JWT vs Session cookies",
                selected_option_id="opt-jwt",
                rationale="Stateless microservices architecture",
            ),
        ),
        decision_required=dec_req,
        remaining_work=("Add redis session storage",),
        handoff_notes="Ensure env variable JWT_SECRET is configured before deployment",
    )

    data = work_result.to_dict()
    assert data["run_id"] == "run-001"
    assert len(data["changed_files"]) == 1
    assert data["changed_files"][0]["path"] == "src/auth.ts"
    assert len(data["tests"]) == 1
    assert data["tests"][0]["passed"] == 5
    assert data["decision_required"]["prompt"] == "Choose database driver"

    # Restore from dict
    restored = AgentWorkResult.from_dict(data)
    assert restored.run_id == "run-001"
    assert restored.summary == "Refactored authentication middleware"
    assert restored.tests[0].is_success
    assert restored.decision_required is not None
    assert restored.decision_required.prompt == "Choose database driver"


def test_sensitive_data_redaction():
    text_with_secrets = "Configured api_key='sk-1234567890abcdef1234567890abcdef' and token='ghp_123456789012345678901234567890123456'"
    redacted = redact_text(text_with_secrets)
    assert "sk-" not in redacted
    assert "ghp_" not in redacted
    assert "[REDACTED_SECRET]" in redacted
