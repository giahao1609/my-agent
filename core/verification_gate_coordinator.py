from __future__ import annotations

from collections.abc import Mapping

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

    def __init__(self, *, require_real_execution: bool = False) -> None:
        self._require_real_execution = require_real_execution

    def evaluate(
        self,
        *,
        step_id: str,
        implementation_result: ImplementationResult | None = None,
        test_result: TestResult | None = None,
        security_result: SecurityReviewResult | None = None,
        require_real_execution: bool | None = None,
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
        no_evidence = (
            implementation_result is None
            and test_result is None
            and security_result is None
        )

        strict_real = (
            self._require_real_execution
            if require_real_execution is None
            else require_real_execution
        )

        # Execution integrity check
        # A mocked/simulated result must NEVER satisfy the production completion gate.
        # Positive real execution evidence is strictly required for production completion.
        is_mocked = False
        execution_integrity_failed = False
        integrity_reason = ""

        if implementation_result is not None:
            evidence = getattr(implementation_result, "execution_evidence", None)
            if getattr(implementation_result, "is_mocked", False):
                is_mocked = True
                integrity_reason = "Implementation was simulated/mocked without real execution."
            elif "[noopstepexecutor]" in getattr(implementation_result, "summary", "").lower():
                is_mocked = True
                integrity_reason = "Implementation was simulated/mocked via NoOpStepExecutor."
            elif isinstance(evidence, Mapping) and evidence.get("is_real") is False:
                is_mocked = True
                integrity_reason = "Execution evidence explicitly indicates non-real execution (is_real=False)."
            elif strict_real:
                # Production completion requires positive evidence of real execution.
                # Absence of is_mocked=True is NOT sufficient evidence of real execution.
                if not isinstance(evidence, Mapping) or not evidence:
                    execution_integrity_failed = True
                    integrity_reason = "Missing positive execution evidence. Production completion requires real execution evidence."
                elif evidence.get("is_real") is not True:
                    execution_integrity_failed = True
                    integrity_reason = f"Execution evidence does not indicate real execution (is_real={evidence.get('is_real')!r})."
                else:
                    executor_id = evidence.get("executor_identity") or evidence.get("evidence_source")
                    if not isinstance(executor_id, str) or not executor_id.strip():
                        execution_integrity_failed = True
                        integrity_reason = "Execution evidence lacks valid executor identity / provenance."
        elif strict_real:
            execution_integrity_failed = True
            integrity_reason = "No implementation result provided for real execution verification."

        if is_mocked or execution_integrity_failed:
            comments.append(integrity_reason)
            required_repairs.append(
                f"Step '{step_id}' failed execution integrity: {integrity_reason} Real execution with positive evidence is required."
            )

        # Determine overall status
        if is_mocked or execution_integrity_failed:
            status = ReviewStatus.REJECTED
            summary = f"Step '{step_id}' REJECTED: {integrity_reason}"
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
