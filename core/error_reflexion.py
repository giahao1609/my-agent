from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    ASSERTION_FAILURE = "assertion_failure"
    SYNTAX_ERROR = "syntax_error"
    IMPORT_ERROR = "import_error"
    TYPE_ATTRIBUTE_ERROR = "type_attribute_error"
    SECURITY_VIOLATION = "security_violation"
    TIMEOUT_ERROR = "timeout_error"
    RUNTIME_EXCEPTION = "runtime_exception"


@dataclass(frozen=True, slots=True)
class ReflexionDiagnosis:
    """Structured diagnosis of an execution or verification failure."""

    category: ErrorCategory
    target_file: str
    line_number: int | None
    root_cause_summary: str
    failed_assertion_detail: str
    actionable_instruction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "target_file": self.target_file,
            "line_number": self.line_number,
            "root_cause_summary": self.root_cause_summary,
            "failed_assertion_detail": self.failed_assertion_detail,
            "actionable_instruction": self.actionable_instruction,
        }

    def format_brief(self) -> str:
        loc = f"{self.target_file}:{self.line_number}" if self.line_number else self.target_file
        return (
            f"[{self.category.value.upper()}] at {loc} — {self.root_cause_summary} | "
            f"Fix: {self.actionable_instruction}"
        )


class ErrorReflexionEngine:
    """Analyzes test failures, traceback outputs, and security findings to generate actionable repair hints."""

    FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\w+))?')
    PYTEST_FAILURE_RE = re.compile(r"^([\w/\\._-]+\.py):(\d+): (.*)$", re.MULTILINE)

    @classmethod
    def diagnose_test_failure(cls, failure_text: str) -> ReflexionDiagnosis:
        """Parses test failure logs and traceback to identify root cause and precise target location."""
        text = failure_text.strip()
        category = ErrorCategory.RUNTIME_EXCEPTION
        target_file = ""
        line_num: int | None = None
        root_cause = ""
        detail = ""
        action = ""

        # Check for pytest failure headers like `tests/test_foo.py:42: AssertionError`
        pytest_match = cls.PYTEST_FAILURE_RE.search(text)
        if pytest_match:
            target_file = pytest_match.group(1)
            try:
                line_num = int(pytest_match.group(2))
            except ValueError:
                line_num = None
            detail = pytest_match.group(3).strip()

        # Check traceback File line patterns (search from bottom up to find failing site)
        file_matches = list(cls.FILE_LINE_RE.finditer(text))
        if file_matches:
            # Prefer non-test production file if present in the traceback chain
            prod_match = None
            for m in reversed(file_matches):
                fn = m.group(1)
                if "test" not in fn and not fn.startswith("<") and "/lib/" not in fn:
                    prod_match = m
                    break
            chosen = prod_match or file_matches[-1]
            target_file = chosen.group(1)
            try:
                line_num = int(chosen.group(2))
            except ValueError:
                pass

        if not target_file:
            target_file = "unknown_location"

        # Determine error category and actionable instruction
        if "AssertionError" in text or "assert " in text or "FAILED" in text:
            category = ErrorCategory.ASSERTION_FAILURE
            # Extract assertion difference if possible
            assert_lines = [
                line.strip() for line in text.splitlines()
                if line.strip().startswith("assert ") or "AssertionError" in line or line.strip().startswith("E   ")
            ]
            root_cause = "Test assertion condition evaluated to False"
            detail = " | ".join(assert_lines[:2]) if assert_lines else text[:120]
            action = f"Update logic in '{target_file}' so output satisfies test invariant: {detail}"

        elif "ImportError" in text or "ModuleNotFoundError" in text:
            category = ErrorCategory.IMPORT_ERROR
            imp_match = re.search(r"(?:No module named|cannot import name) ['\"]([^'\"]+)['\"]", text)
            mod_name = imp_match.group(1) if imp_match else "missing_module"
            root_cause = f"Cannot resolve import module/symbol '{mod_name}'"
            detail = f"Missing import: {mod_name}"
            action = f"Ensure '{mod_name}' is declared, correctly spelled, or resolve circular dependency in '{target_file}'"

        elif "SyntaxError" in text or "IndentationError" in text:
            category = ErrorCategory.SYNTAX_ERROR
            root_cause = "Invalid Python syntax or indentation"
            detail = text.splitlines()[-1] if text else "Syntax error"
            action = f"Fix syntax or indentation error at line {line_num or '?'} in '{target_file}'"

        elif "AttributeError" in text or "TypeError" in text:
            category = ErrorCategory.TYPE_ATTRIBUTE_ERROR
            err_line = [l for l in text.splitlines() if "AttributeError:" in l or "TypeError:" in l]
            detail = err_line[-1].strip() if err_line else text[:100]
            root_cause = detail
            action = f"Review interface or method signature at line {line_num or '?'} in '{target_file}': {detail}"

        elif "Timeout" in text or "timed out" in text:
            category = ErrorCategory.TIMEOUT_ERROR
            root_cause = "Operation timed out during execution"
            detail = text[:100]
            action = f"Optimize slow execution or check for deadlock/infinite loop in '{target_file}'"

        else:
            category = ErrorCategory.RUNTIME_EXCEPTION
            last_line = text.splitlines()[-1].strip() if text else "Unknown error"
            root_cause = last_line[:120]
            detail = text[:120]
            action = f"Inspect and handle unhandled exception at line {line_num or '?'} in '{target_file}'"

        return ReflexionDiagnosis(
            category=category,
            target_file=target_file,
            line_number=line_num,
            root_cause_summary=root_cause,
            failed_assertion_detail=detail,
            actionable_instruction=action,
        )

    @classmethod
    def diagnose_security_finding(cls, finding: Any) -> ReflexionDiagnosis:
        """Converts security gate finding into actionable repair instruction."""
        target_file = getattr(finding, "file_path", "") or getattr(finding, "file", "") or "workspace"
        line_number = getattr(finding, "line_number", None) or getattr(finding, "line", None)
        rule_id = getattr(finding, "rule_id", "SECURITY_POLICY")
        description = getattr(finding, "description", str(finding))

        return ReflexionDiagnosis(
            category=ErrorCategory.SECURITY_VIOLATION,
            target_file=target_file,
            line_number=line_number,
            root_cause_summary=f"Security gate policy violation [{rule_id}]",
            failed_assertion_detail=description,
            actionable_instruction=(
                f"Redact secret, sanitize input, or fix vulnerability matching '{rule_id}' "
                f"in '{target_file}' (line {line_number or '?'})"
            ),
        )

    @classmethod
    def build_repair_prompt(
        cls,
        diagnoses: list[ReflexionDiagnosis],
        max_items: int = 3,
    ) -> str:
        """Formats structured repair prompts to be passed into Coder Agent repair loops."""
        if not diagnoses:
            return "No specific failure diagnostics available."

        items = diagnoses[:max_items]
        lines = ["### [Error Reflexion & Actionable Repair Guide]"]
        for i, diag in enumerate(items, 1):
            loc = f"{diag.target_file}:{diag.line_number}" if diag.line_number else diag.target_file
            lines.append(f"{i}. Category: {diag.category.value.upper()}")
            lines.append(f"   Target: {loc}")
            lines.append(f"   Root cause: {diag.root_cause_summary}")
            if diag.failed_assertion_detail:
                lines.append(f"   Detail: {diag.failed_assertion_detail}")
            lines.append(f"   Actionable fix: {diag.actionable_instruction}")

        return "\n".join(lines)
