from __future__ import annotations

from .handoff_contracts import (
    ImplementationResult,
    ReviewResult,
    ReviewStatus,
    SecurityReviewResult,
    TestResult,
)


class VerificationGateCoordinator:
    """Deterministic acceptance gate coordinator.

    Evaluates objective test results and security scan findings to produce an authoritative ReviewResult.
    """

    def evaluate(
        self,
        *,
        step_id: str,
        implementation_result: ImplementationResult | None = None,
        test_result: TestResult | None = None,
        security_result: SecurityReviewResult | None = None,
    ) -> ReviewResult:
        comments: list[str] = []
        required_repairs: list[str] = []

        # Check implementation status
        if implementation_result is not None and not implementation_result.success:
            comments.append("Implementation reported failure.")
            required_repairs.append(f"Fix implementation step '{step_id}': {implementation_result.summary}")

        # Check test gate
        if test_result is not None:
            if not test_result.success:
                comments.append(f"Test gate failed: {test_result.failed_tests}/{test_result.total_tests} tests failed.")
                for detail in test_result.failure_details:
                    required_repairs.append(f"Test failure: {detail}")
            else:
                comments.append(f"Test gate passed: {test_result.passed_tests}/{test_result.total_tests} tests passed.")

        # Check security gate
        if security_result is not None:
            if not security_result.passed_gate:
                comments.append(f"Security gate failed: {len(security_result.findings)} findings present.")
                for finding in security_result.findings:
                    required_repairs.append(
                        f"Security issue [{finding.severity.value.upper()}] in {finding.file_path}:{finding.line_number} - {finding.description}"
                    )
            else:
                comments.append(f"Security gate passed: {security_result.summary}")

        # Fail-Closed guard: if no evidence at all is provided, the gate cannot approve.
        # Approving without evidence would be a Fail-Open vulnerability.
        no_evidence = (
            implementation_result is None
            and test_result is None
            and security_result is None
        )

        # Guard against synthetic/mocked execution results:
        # A mocked/simulated result must NEVER satisfy the production completion gate.
        is_mocked = False
        if implementation_result is not None:
            if getattr(implementation_result, "is_mocked", False):
                is_mocked = True
            elif "[noopstepexecutor]" in getattr(implementation_result, "summary", "").lower():
                is_mocked = True

        if is_mocked:
            comments.append("Implementation was simulated or mocked without real execution.")
            required_repairs.append(
                f"Step '{step_id}' was simulated/mocked. Real execution through an authorized runtime is required."
            )

        # Determine overall status
        if is_mocked:
            status = ReviewStatus.REJECTED
            summary = f"Step '{step_id}' REJECTED: implementation result is simulated/mocked. Real execution required."
        elif no_evidence:
            status = ReviewStatus.REQUEST_REWORK
            summary = (
                f"Step '{step_id}' cannot be approved: no implementation, test, or "
                "security evidence was provided. Supply at least one evidence input."
            )
            required_repairs.append(
                "Provide evidence: implementation_result, test_result, or security_result before requesting approval."
            )
        elif security_result is not None and not security_result.passed_gate:
            status = ReviewStatus.REJECTED
            summary = f"Step '{step_id}' REJECTED due to critical security findings."
        elif (test_result is not None and not test_result.success) or (
            implementation_result is not None and not implementation_result.success
        ):
            status = ReviewStatus.REQUEST_REWORK
            summary = f"Step '{step_id}' requires REWORK due to test/implementation failures."
        else:
            status = ReviewStatus.APPROVED
            summary = f"Step '{step_id}' APPROVED: all verification gates passed."

        return ReviewResult(
            step_id=step_id,
            status=status,
            summary=summary,
            comments=tuple(comments),
            required_repairs=tuple(required_repairs),
        )
