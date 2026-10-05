from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from core.handoff_contracts import SecurityReviewResult, TestResult


@dataclass(frozen=True, slots=True)
class EvaluationJob:
    job_id: str
    model_id: str
    task_id: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    security_findings_count: int
    duration_seconds: float
    cost_estimate_usd: float
    quality_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "model_id": self.model_id,
            "task_id": self.task_id,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "security_findings_count": self.security_findings_count,
            "duration_seconds": self.duration_seconds,
            "cost_estimate_usd": self.cost_estimate_usd,
            "quality_score": self.quality_score,
        }


class EvaluationHarness:
    """Benchmark harness for evaluating model & agent performance, test success, security, and efficiency."""

    def evaluate_execution(
        self,
        *,
        model_id: str,
        task_id: str,
        test_result: TestResult | None = None,
        security_result: SecurityReviewResult | None = None,
        duration_seconds: float = 1.0,
        cost_estimate_usd: float = 0.01,
    ) -> EvaluationJob:
        total_tests = test_result.total_tests if test_result else 0
        passed_tests = test_result.passed_tests if test_result else 0
        failed_tests = test_result.failed_tests if test_result else 0
        sec_findings = len(security_result.findings) if security_result else 0

        # Calculate quality score (0.0 to 100.0)
        test_pass_rate = (passed_tests / total_tests) if total_tests > 0 else 1.0
        sec_penalty = min(sec_findings * 15.0, 50.0)
        quality_score = max(0.0, round((test_pass_rate * 100.0) - sec_penalty, 2))

        return EvaluationJob(
            job_id=f"eval-{uuid.uuid4().hex[:12]}",
            model_id=model_id,
            task_id=task_id,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            security_findings_count=sec_findings,
            duration_seconds=duration_seconds,
            cost_estimate_usd=cost_estimate_usd,
            quality_score=quality_score,
        )
