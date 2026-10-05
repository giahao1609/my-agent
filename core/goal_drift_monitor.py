from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from pathlib import Path

from .handoff_contracts import GoalDriftResult


class GoalDriftMonitor:
    """Monitors step execution for goal drift, invariant violations, and out-of-scope modifications."""

    RESTRICTED_PATTERNS = (
        "*.env*",
        "*.pem",
        "*.key",
        "*id_rsa*",
        "*.sqlite*",
        "*.db",
        ".git/*",
        "*credentials*",
    )

    UPSTREAM_RESTRICTION = "/upstreams"

    @classmethod
    def evaluate_drift(
        cls,
        step_id: str,
        modified_files: Sequence[str],
        allowed_paths: Sequence[str] | None = None,
        original_goal: str = "",
    ) -> GoalDriftResult:
        violations: list[str] = []
        out_of_scope: list[str] = []

        allowed_set = [p.strip() for p in (allowed_paths or []) if p.strip()]

        for f in modified_files:
            file_clean = f.strip().lstrip("./")

            # 1. Check prohibited sensitive files
            for pat in cls.RESTRICTED_PATTERNS:
                if fnmatch.fnmatch(file_clean, pat) or fnmatch.fnmatch(Path(file_clean).name, pat):
                    violations.append(f"Modification of sensitive/invariant file prohibited: '{file_clean}'")

            # 2. Check upstream repo boundary
            if "upstreams" in file_clean or "/upstreams" in f:
                violations.append(f"Modification of upstream reference repository prohibited: '{file_clean}'")

            # 3. Check allowed scope boundaries if explicitly specified
            if allowed_set:
                is_allowed = False
                for allowed in allowed_set:
                    allowed_clean = allowed.lstrip("./")
                    # Match exact, directory prefix, or wildcard
                    if (
                        file_clean == allowed_clean
                        or file_clean.startswith(allowed_clean.rstrip("/") + "/")
                        or fnmatch.fnmatch(file_clean, allowed_clean)
                    ):
                        is_allowed = True
                        break

                if not is_allowed:
                    out_of_scope.append(file_clean)

        drift_detected = len(out_of_scope) > 0 or len(violations) > 0
        passed = not drift_detected

        if not passed:
            summary_parts = []
            if out_of_scope:
                summary_parts.append(f"{len(out_of_scope)} out-of-scope files modified ({', '.join(out_of_scope[:3])})")
            if violations:
                summary_parts.append(f"{len(violations)} invariant violations ({'; '.join(violations[:2])})")
            summary = f"Goal drift detected: {'; '.join(summary_parts)}."
        else:
            summary = (
                f"Goal alignment verified: {len(modified_files)} modified files strictly within approved scope."
                if modified_files
                else "Goal alignment verified: No file modifications."
            )

        return GoalDriftResult(
            step_id=step_id,
            passed=passed,
            drift_detected=drift_detected,
            out_of_scope_files=tuple(out_of_scope),
            violations=tuple(violations),
            summary=summary,
        )
