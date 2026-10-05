from __future__ import annotations

import pytest
from core.handoff_contracts import (
    ArchitectureProposal,
    BenchmarkMetric,
    CodeHygieneFinding,
    CodeHygieneReviewResult,
    CoverageReviewResult,
    DecisionOptionDraft,
    DecisionRequiredResult,
    GoalDriftResult,
    ImplementationResult,
    PerformanceReviewResult,
    PiiEntity,
    PiiSanitizationResult,
    ResearchResult,
    ReviewResult,
    ReviewStatus,
    SecurityFinding,
    SecurityReviewResult,
    SecuritySeverity,
    TestResult,
    VisualDiffResult,
)


def test_review_status_and_security_severity_enums():
    assert ReviewStatus.APPROVED.value == "approved"
    assert ReviewStatus.REJECTED.value == "rejected"
    assert ReviewStatus.REQUEST_REWORK.value == "request_rework"

    assert SecuritySeverity.CRITICAL.value == "critical"
    assert SecuritySeverity.HIGH.value == "high"
    assert SecuritySeverity.MEDIUM.value == "medium"
    assert SecuritySeverity.LOW.value == "low"
    assert SecuritySeverity.INFO.value == "info"


def test_architecture_proposal_roundtrip():
    orig = ArchitectureProposal(
        task_id="task-1",
        summary="Architecture change",
        affected_components=("core", "agents"),
        interfaces_to_add=("IStore",),
        constraints=("no breaking changes",),
    )
    d = orig.to_dict()
    restored = ArchitectureProposal.from_dict(d)
    assert restored.task_id == orig.task_id
    assert restored.summary == orig.summary
    assert restored.affected_components == orig.affected_components
    assert restored.interfaces_to_add == orig.interfaces_to_add
    assert restored.constraints == orig.constraints


def test_research_result_roundtrip():
    orig = ResearchResult(
        topic="Caching Strategies",
        summary="Redis vs Memcached",
        findings=("Redis supports persistence", "Memcached is simpler"),
        sources=("https://redis.io",),
        recommendations=("Use Redis for durable cache",),
    )
    d = orig.to_dict()
    restored = ResearchResult.from_dict(d)
    assert restored == orig


def test_implementation_result_roundtrip():
    orig = ImplementationResult(
        step_id="step-impl",
        summary="Created user module",
        modified_files=("app.py",),
        created_files=("user.py",),
        deleted_files=("old_user.py",),
        success=True,
    )
    d = orig.to_dict()
    restored = ImplementationResult.from_dict(d)
    assert restored == orig


def test_review_result_roundtrip():
    orig = ReviewResult(
        step_id="step-rev",
        status=ReviewStatus.REQUEST_REWORK,
        summary="Needs more unit tests",
        required_repairs=("Add tests for edge cases",),
        comments=("Overall good structure",),
    )
    d = orig.to_dict()
    restored = ReviewResult.from_dict(d)
    assert restored.status == ReviewStatus.REQUEST_REWORK
    assert restored.summary == orig.summary
    assert restored.required_repairs == orig.required_repairs


def test_security_finding_and_review_result_roundtrip():
    finding = SecurityFinding(
        severity=SecuritySeverity.HIGH,
        rule_id="SEC-001",
        file_path="auth.py",
        line_number=42,
        description="Hardcoded credential",
        remediation="Use env var",
    )
    f_dict = finding.to_dict()
    restored_f = SecurityFinding.from_dict(f_dict)
    assert restored_f.severity == SecuritySeverity.HIGH
    assert restored_f.rule_id == "SEC-001"

    sec_review = SecurityReviewResult(
        step_id="step-sec",
        passed_gate=False,
        summary="Security scan failed",
        findings=(finding,),
    )
    d = sec_review.to_dict()
    restored_rev = SecurityReviewResult.from_dict(d)
    assert restored_rev.passed_gate is False
    assert len(restored_rev.findings) == 1
    assert restored_rev.findings[0].rule_id == "SEC-001"


def test_test_result_roundtrip():
    orig = TestResult(
        step_id="step-test",
        total_tests=10,
        passed_tests=8,
        failed_tests=2,
        duration_ms=1250,
        failure_details=("test_a failed", "test_b failed"),
        coverage_percentage=82.5,
    )
    d = orig.to_dict()
    assert d["success"] is False
    restored = TestResult.from_dict(d)
    assert restored == orig
    assert restored.success is False


def test_decision_option_draft_and_required_result_roundtrip():
    opt = DecisionOptionDraft(
        option_id="opt-sql",
        title="SQLite",
        description="Local embedded DB",
        trade_offs="No remote clustering",
        impact="Fast and simple",
        recommended=True,
    )
    o_dict = opt.to_dict()
    restored_opt = DecisionOptionDraft.from_dict(o_dict)
    assert restored_opt.option_id == "opt-sql"
    assert restored_opt.recommended is True

    dec_req = DecisionRequiredResult(
        step_id="step-dec",
        prompt="Need storage decision",
        severity="medium",
        options=(opt,),
        rationale="Embedded vs Client-Server",
    )
    d = dec_req.to_dict()
    restored_req = DecisionRequiredResult.from_dict(d)
    assert restored_req.prompt == "Need storage decision"
    assert len(restored_req.options) == 1
    assert restored_req.rationale == "Embedded vs Client-Server"


def test_benchmark_metric_and_performance_review_roundtrip():
    metric = BenchmarkMetric(
        name="crypto_hash",
        iterations=1000,
        total_duration_ms=50.0,
        avg_duration_ms=0.05,
        min_duration_ms=0.03,
        max_duration_ms=0.12,
        ops_per_sec=20000.0,
        memory_bytes=1024,
        passed_slo=True,
        slo_threshold_ms=1.0,
    )
    m_dict = metric.to_dict()
    restored_m = BenchmarkMetric.from_dict(m_dict)
    assert restored_m == metric

    perf = PerformanceReviewResult(
        step_id="step-perf",
        passed_gate=True,
        summary="All SLOs met",
        metrics=(metric,),
        slo_violations=(),
    )
    p_dict = perf.to_dict()
    restored_perf = PerformanceReviewResult.from_dict(p_dict)
    assert restored_perf.passed_gate is True
    assert len(restored_perf.metrics) == 1
    assert restored_perf.metrics[0].name == "crypto_hash"


def test_coverage_review_result_roundtrip():
    orig = CoverageReviewResult(
        step_id="step-cov",
        coverage_percentage=88.5,
        threshold_percentage=80.0,
        passed_gate=True,
        summary="Coverage 88.5% exceeds threshold 80.0%",
    )
    d = orig.to_dict()
    restored = CoverageReviewResult.from_dict(d)
    assert restored == orig


def test_visual_diff_result_roundtrip():
    orig = VisualDiffResult(
        baseline_path="/tmp/base.png",
        current_path="/tmp/curr.png",
        total_pixels=10000,
        differing_pixels=10,
        diff_percentage=0.1,
        tolerance_percentage=1.0,
        passed=True,
        summary="Diff within tolerance",
    )
    d = orig.to_dict()
    restored = VisualDiffResult.from_dict(d)
    assert restored == orig


def test_goal_drift_result_roundtrip():
    orig = GoalDriftResult(
        step_id="step-drift",
        passed=False,
        drift_detected=True,
        out_of_scope_files=("forbidden.py",),
        violations=("Touched file outside plan scope",),
        summary="Drift detected",
    )
    d = orig.to_dict()
    restored = GoalDriftResult.from_dict(d)
    assert restored == orig


def test_code_hygiene_finding_and_review_roundtrip():
    finding = CodeHygieneFinding(
        category="dead_code",
        severity="warning",
        file_path="unused.py",
        symbol_name="obsolete_func",
        details="Function is never called",
        suggested_action="Remove function",
    )
    f_dict = finding.to_dict()
    restored_f = CodeHygieneFinding.from_dict(f_dict)
    assert restored_f == finding

    review = CodeHygieneReviewResult(
        step_id="step-hygiene",
        passed_gate=True,
        summary="No circular dependencies",
        cycles=(),
        dead_code_count=1,
        findings=(finding,),
    )
    d = review.to_dict()
    restored_rev = CodeHygieneReviewResult.from_dict(d)
    assert restored_rev.dead_code_count == 1
    assert len(restored_rev.findings) == 1
    assert restored_rev.findings[0].symbol_name == "obsolete_func"


def test_pii_entity_and_sanitization_result_roundtrip():
    entity = PiiEntity(
        entity_type="EMAIL",
        matched_text="user@example.com",
        start_index=10,
        end_index=26,
        replacement="[REDACTED_EMAIL]",
    )
    e_dict = entity.to_dict()
    restored_e = PiiEntity.from_dict(e_dict)
    assert restored_e == entity

    san_res = PiiSanitizationResult(
        original_length=50,
        sanitized_text="Contact: [REDACTED_EMAIL] for info",
        entities_found=(entity,),
        pii_detected=True,
        summary="1 email sanitized",
    )
    d = san_res.to_dict()
    restored_san = PiiSanitizationResult.from_dict(d)
    assert restored_san.original_length == 50
    assert restored_san.pii_detected is True
    assert len(restored_san.entities_found) == 1
    assert restored_san.entities_found[0].entity_type == "EMAIL"
