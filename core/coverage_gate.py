from __future__ import annotations

from .handoff_contracts import CoverageReviewResult, TestResult


class CoverageGateCoordinator:
    """Enforces minimum code coverage thresholds for PlanStep verification."""

    def __init__(self, default_threshold: float = 80.0) -> None:
        self.default_threshold = default_threshold

    def evaluate_coverage(
        self,
        test_result: TestResult,
        min_coverage: float | None = None,
    ) -> CoverageReviewResult:
        threshold = min_coverage if min_coverage is not None else self.default_threshold
        cov = test_result.coverage_percentage

        if cov is None:
            return CoverageReviewResult(
                step_id=test_result.step_id,
                coverage_percentage=0.0,
                threshold_percentage=threshold,
                passed_gate=False,
                summary=f"Coverage gate FAILED: No code coverage metrics reported by test runner (required >= {threshold}%).",
            )

        passed = cov >= threshold
        summary = (
            f"Coverage gate PASSED: {cov:.1f}% code coverage meets required >= {threshold}% threshold."
            if passed
            else f"Coverage gate FAILED: {cov:.1f}% code coverage is below required >= {threshold}% threshold."
        )

        return CoverageReviewResult(
            step_id=test_result.step_id,
            coverage_percentage=round(cov, 2),
            threshold_percentage=threshold,
            passed_gate=passed,
            summary=summary,
        )
