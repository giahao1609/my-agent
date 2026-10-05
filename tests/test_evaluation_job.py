from __future__ import annotations

import pytest

from core.handoff_contracts import SecurityFinding, SecurityReviewResult, SecuritySeverity, TestResult
from eval.evaluation_job import EvaluationHarness, EvaluationJob


def test_evaluation_job_serialization() -> None:
    job = EvaluationJob(
        job_id="job-1",
        model_id="gpt-4o",
        task_id="task-1",
        total_tests=10,
        passed_tests=10,
        failed_tests=0,
        security_findings_count=0,
        duration_seconds=5.2,
        cost_estimate_usd=0.02,
        quality_score=100.0,
    )
    data = job.to_dict()
    assert data["job_id"] == "job-1"
    assert data["quality_score"] == 100.0


def test_evaluation_harness_scoring() -> None:
    harness = EvaluationHarness()

    test_res = TestResult(step_id="step-1", total_tests=10, passed_tests=10, failed_tests=0)
    sec_res = SecurityReviewResult(step_id="step-1", passed_gate=True, summary="Clean")

    job_perfect = harness.evaluate_execution(
        model_id="gpt-4o",
        task_id="task-1",
        test_result=test_res,
        security_result=sec_res,
    )
    assert job_perfect.quality_score == 100.0

    finding = SecurityFinding(
        rule_id="SECRET-001",
        severity=SecuritySeverity.HIGH,
        file_path="config.py",
        line_number=1,
        description="Exposed secret",
    )
    sec_fail = SecurityReviewResult(step_id="step-1", passed_gate=False, summary="Secret found", findings=(finding,))

    job_penalized = harness.evaluate_execution(
        model_id="gpt-4o",
        task_id="task-1",
        test_result=test_res,
        security_result=sec_fail,
    )
    assert job_penalized.quality_score < 100.0
