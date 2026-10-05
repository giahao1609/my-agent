from __future__ import annotations

import pytest

from core.handoff_contracts import (
    ArchitectureProposal,
    ImplementationResult,
    ResearchResult,
    ReviewResult,
    ReviewStatus,
    SecurityFinding,
    SecurityReviewResult,
    SecuritySeverity,
    TestResult,
    UiImplementationResult,
)


def test_architecture_proposal_contract() -> None:
    prop = ArchitectureProposal(
        task_id="task-1",
        summary="Add payment service boundaries",
        affected_components=("payment_service", "auth_service"),
        interfaces_to_add=("PaymentProviderProtocol",),
        constraints=("No synchronous network calls",),
    )
    data = prop.to_dict()
    assert data["task_id"] == "task-1"
    assert "payment_service" in data["affected_components"]

    restored = ArchitectureProposal.from_dict(data)
    assert restored.task_id == prop.task_id
    assert restored.affected_components == prop.affected_components


def test_research_result_contract() -> None:
    res = ResearchResult(
        topic="Stripe API minor units",
        summary="USD uses 2-decimal minor units, VND uses internal x100 convention.",
        findings=("USD $19.99 = 1999 cents", "VND uses zero decimal in standard ISO"),
        sources=("https://stripe.com/docs/currencies",),
        recommendations=("Apply zero decimal check",),
    )
    data = res.to_dict()
    restored = ResearchResult.from_dict(data)
    assert restored.topic == res.topic
    assert restored.findings == res.findings


def test_implementation_results_contracts() -> None:
    imp = ImplementationResult(
        step_id="step-1",
        summary="Implemented currency validator",
        modified_files=("currency.go",),
        created_files=("currency_test.go",),
        success=True,
    )
    data = imp.to_dict()
    restored = ImplementationResult.from_dict(data)
    assert restored.step_id == imp.step_id
    assert restored.success is True

    ui_imp = UiImplementationResult(
        step_id="step-2",
        summary="Created payment button component",
        modified_files=("PaymentButton.tsx",),
        components_reused=("Button", "Icon"),
        components_created=("PaymentButton",),
        a11y_status="passed",
    )
    ui_data = ui_imp.to_dict()
    restored_ui = UiImplementationResult.from_dict(ui_data)
    assert restored_ui.components_reused == ("Button", "Icon")
    assert restored_ui.a11y_status == "passed"


def test_test_result_contract() -> None:
    tr_pass = TestResult(
        step_id="step-1",
        total_tests=15,
        passed_tests=15,
        failed_tests=0,
        duration_ms=450,
    )
    assert tr_pass.success is True

    tr_fail = TestResult(
        step_id="step-1",
        total_tests=15,
        passed_tests=14,
        failed_tests=1,
        failure_details=("TestCurrencyValidation failed",),
    )
    assert tr_fail.success is False
    restored = TestResult.from_dict(tr_fail.to_dict())
    assert restored.failed_tests == 1


def test_security_contracts() -> None:
    finding = SecurityFinding(
        rule_id="G101",
        severity=SecuritySeverity.HIGH,
        file_path="config.go",
        line_number=42,
        description="Hardcoded API secret",
        remediation="Use environment variable",
    )

    sec_review = SecurityReviewResult(
        step_id="step-1",
        passed_gate=False,
        summary="Found 1 high severity secret finding",
        findings=(finding,),
    )

    data = sec_review.to_dict()
    restored = SecurityReviewResult.from_dict(data)
    assert restored.passed_gate is False
    assert len(restored.findings) == 1
    assert restored.findings[0].severity == SecuritySeverity.HIGH


def test_review_result_contract() -> None:
    rev = ReviewResult(
        step_id="step-1",
        status=ReviewStatus.REQUEST_REWORK,
        summary="Need zero-decimal check for VND",
        comments=("Currency guard is too strict for USD",),
        required_repairs=("Add domain.Currency.ZeroDecimal() check",),
    )
    data = rev.to_dict()
    restored = ReviewResult.from_dict(data)
    assert restored.status == ReviewStatus.REQUEST_REWORK
    assert len(restored.required_repairs) == 1
