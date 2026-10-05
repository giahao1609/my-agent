from __future__ import annotations

import pytest

from core.handoff_contracts import (
    ImplementationResult,
    ReviewStatus,
    SecurityFinding,
    SecurityReviewResult,
    SecuritySeverity,
    TestResult,
)
from core.verification_gate_coordinator import VerificationGateCoordinator


def test_verification_gate_approval() -> None:
    coordinator = VerificationGateCoordinator()

    imp = ImplementationResult(step_id="step-1", summary="Done", success=True)
    test_res = TestResult(step_id="step-1", total_tests=10, passed_tests=10, failed_tests=0)
    sec_res = SecurityReviewResult(step_id="step-1", passed_gate=True, summary="Clean")

    review = coordinator.evaluate(
        step_id="step-1",
        implementation_result=imp,
        test_result=test_res,
        security_result=sec_res,
    )

    assert review.status == ReviewStatus.APPROVED
    assert len(review.required_repairs) == 0


def test_verification_gate_rework_on_test_failure() -> None:
    coordinator = VerificationGateCoordinator()

    imp = ImplementationResult(step_id="step-1", summary="Done", success=True)
    test_res = TestResult(
        step_id="step-1",
        total_tests=10,
        passed_tests=9,
        failed_tests=1,
        failure_details=("test_payment failed",),
    )
    sec_res = SecurityReviewResult(step_id="step-1", passed_gate=True, summary="Clean")

    review = coordinator.evaluate(
        step_id="step-1",
        implementation_result=imp,
        test_result=test_res,
        security_result=sec_res,
    )

    assert review.status == ReviewStatus.REQUEST_REWORK
    assert len(review.required_repairs) > 0
    assert "Test failure: test_payment failed" in review.required_repairs[0]


def test_verification_gate_rejection_on_security_failure() -> None:
    coordinator = VerificationGateCoordinator()

    finding = SecurityFinding(
        rule_id="SECRET-001",
        severity=SecuritySeverity.CRITICAL,
        file_path="config.py",
        line_number=10,
        description="Exposed API key",
    )
    sec_res = SecurityReviewResult(step_id="step-1", passed_gate=False, summary="Secret found", findings=(finding,))
    test_res = TestResult(step_id="step-1", total_tests=10, passed_tests=10, failed_tests=0)

    review = coordinator.evaluate(
        step_id="step-1",
        test_result=test_res,
        security_result=sec_res,
    )

    assert review.status == ReviewStatus.REJECTED
    assert len(review.required_repairs) > 0
    assert "Security issue [CRITICAL]" in review.required_repairs[0]


def test_verification_gate_no_evidence_requires_rework() -> None:
    """Fail-Closed: gate must not approve when no evidence is provided at all."""
    coordinator = VerificationGateCoordinator()

    review = coordinator.evaluate(step_id="step-no-evidence")

    assert review.status == ReviewStatus.REQUEST_REWORK
    assert "cannot be approved" in review.summary
    assert len(review.required_repairs) > 0
    assert "evidence" in review.required_repairs[0].lower()


def test_verification_gate_only_implementation_no_test_no_security_approved() -> None:
    """Implementation-only evidence with success=True still counts as valid evidence."""
    coordinator = VerificationGateCoordinator()

    imp = ImplementationResult(step_id="step-2", summary="Feature complete", success=True)
    review = coordinator.evaluate(step_id="step-2", implementation_result=imp)

    # Has evidence (implementation), implementation passed, no test/security failures
    assert review.status == ReviewStatus.APPROVED


def test_verification_gate_implementation_failure_no_test_no_security_rework() -> None:
    """Implementation failed and no other evidence → REQUEST_REWORK."""
    coordinator = VerificationGateCoordinator()

    imp = ImplementationResult(step_id="step-3", summary="Build failed", success=False)
    review = coordinator.evaluate(step_id="step-3", implementation_result=imp)

    assert review.status == ReviewStatus.REQUEST_REWORK
    assert len(review.required_repairs) > 0
