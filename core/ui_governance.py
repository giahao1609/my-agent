from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class UiComponentDef:
    name: str
    category: str
    file_path: str
    props_summary: str = ""
    is_reusable: bool = True


@dataclass(frozen=True, slots=True)
class UiGovernanceResult:
    passed: bool
    components_reused: tuple[str, ...]
    components_created: tuple[str, ...]
    a11y_violations: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""
    design_token_violations: tuple[str, ...] = field(default_factory=tuple)


class AccessibilityValidator:
    """Evaluates Web Accessibility (WAI-ARIA & WCAG 2.1) compliance in UI source files."""

    @classmethod
    def validate_content(cls, content: str, file_path: str = "") -> list[str]:
        violations: list[str] = []
        for line_idx, line in enumerate(content.splitlines(), start=1):
            prefix = f"{file_path}:{line_idx}: " if file_path else f"Line {line_idx}: "

            # 1. Missing img alt
            if "<img" in line and not re.search(r"\balt\s*=", line):
                violations.append(f"[A11Y-001] {prefix}Missing alt attribute on <img> element")

            # 2. Empty/icon button without aria-label
            if "<button" in line and not re.search(r"\b(?:aria-label|aria-labelledby)\s*=", line):
                if re.search(r"<button[^>]*>\s*<\/[^>]+>", line):
                    violations.append(f"[A11Y-002] {prefix}Icon/empty <button> missing aria-label")

            # 3. Clickable div without role="button" and tabIndex
            if "<div" in line and "onClick" in line:
                has_role = bool(re.search(r'\brole\s*=\s*["\']button["\']', line))
                has_tabindex = bool(re.search(r'\btabIndex\s*=', line))
                if not (has_role or has_tabindex):
                    violations.append(f"[A11Y-003] {prefix}Clickable <div> missing role='button' or tabIndex")

            # 4. Form input missing label/id
            if "<input" in line and not re.search(r"\b(?:id|name|aria-label|aria-labelledby)\s*=", line):
                violations.append(f"[A11Y-004] {prefix}Form <input> missing accessible identifier/label")

            # 5. Empty link without accessible text or aria-label
            if "<a" in line and not re.search(r"\b(?:aria-label|aria-labelledby)\s*=", line):
                if re.search(r"<a[^>]*>\s*<\/a>", line):
                    violations.append(f"[A11Y-005] {prefix}Empty <a> anchor missing accessible text or aria-label")

            # 6. SVG graphic without aria-hidden or role/title
            if "<svg" in line and not re.search(r'\baria-hidden\s*=\s*["\']true["\']', line):
                if not re.search(r'\b(?:aria-label|aria-labelledby|role\s*=\s*["\']img["\'])\b', line):
                    violations.append(f"[A11Y-006] {prefix}SVG graphic missing aria-hidden='true' or accessible title/aria-label")

            # 7. Table lacking accessible header <th> or scope attribute
            if "<table" in line:
                has_th = bool(re.search(r"<th\b", content))
                has_summary = bool(re.search(r"\b(?:summary|aria-label|aria-labelledby)\s*=", content))
                if not (has_th or has_summary):
                    violations.append(f"[A11Y-007] {prefix}Table lacks accessible header <th> or scope attribute")

        return violations


class DesignTokenValidator:
    """Validates design token consistency (colors, spacing, typography) and detects raw hardcoded values."""

    HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    RGB_COLOR_PATTERN = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\s*\(")

    EXCLUDED_FILE_PATTERNS = ("tailwind.config", "theme.", "tokens.", "colors.", "globals.css", "variables.css")

    @classmethod
    def validate_content(cls, content: str, file_path: str = "") -> list[str]:
        violations: list[str] = []
        if any(exc in file_path.lower() for exc in cls.EXCLUDED_FILE_PATTERNS):
            return violations

        for line_idx, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue

            prefix = f"{file_path}:{line_idx}: " if file_path else f"Line {line_idx}: "

            # Detect raw hardcoded hex/rgb colors in style props or inline CSS
            if "style=" in line or "color:" in line or "background" in line or "border" in line:
                if cls.HEX_COLOR_PATTERN.search(line):
                    violations.append(
                        f"[TOKEN-001] {prefix}Raw hardcoded hex color detected in component; prefer theme tokens or CSS variables"
                    )
                elif cls.RGB_COLOR_PATTERN.search(line):
                    violations.append(
                        f"[TOKEN-002] {prefix}Raw rgb/hsl color detected in component; prefer theme tokens or CSS variables"
                    )

        return violations


class UiComponentCatalog:
    """Discovers and registers reusable UI components and design system tokens in the project workspace."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        self._catalog: dict[str, UiComponentDef] = {}

    def scan_catalog(self) -> int:
        self._catalog.clear()
        if not self._workspace_root.exists():
            return 0

        component_files: list[Path] = []
        for ext in ("*.tsx", "*.jsx", "*.vue", "*.svelte"):
            component_files.extend(self._workspace_root.rglob(ext))

        for path in component_files:
            if any(part in path.parts for part in ("node_modules", ".git", "build", "dist")):
                continue

            try:
                rel_path = str(path.relative_to(self._workspace_root))
            except ValueError:
                rel_path = str(path)

            name = path.stem
            category = "ui" if "components/ui" in rel_path or "components" in rel_path else "feature"
            self._catalog[name.lower()] = UiComponentDef(
                name=name,
                category=category,
                file_path=rel_path,
                is_reusable=True,
            )

        return len(self._catalog)

    def search_components(self, query: str) -> list[UiComponentDef]:
        if not self._catalog:
            self.scan_catalog()
        q = query.lower()
        return [c for name, c in self._catalog.items() if q in name or q in c.category]

    def get_component(self, name: str) -> UiComponentDef | None:
        if not self._catalog:
            self.scan_catalog()
        return self._catalog.get(name.lower())


class UiGovernancePolicy:
    """Enforces design system reuse and accessibility standards."""

    def __init__(self, catalog: UiComponentCatalog | None = None) -> None:
        self._catalog = catalog or UiComponentCatalog()

    def evaluate_implementation(
        self,
        created_components: Sequence[str],
        used_components: Sequence[str],
        source_files: Sequence[Path | str] | None = None,
    ) -> UiGovernanceResult:
        reused: list[str] = []
        created: list[str] = list(created_components)
        a11y_issues: list[str] = []
        token_issues: list[str] = []

        for name in used_components:
            match = self._catalog.get_component(name)
            if match:
                reused.append(match.name)

        # Check if new components were created when reusable matches exist
        for name in created_components:
            existing = self._catalog.search_components(name)
            if existing:
                a11y_issues.append(
                    f"Component '{name}' was created but matching reusable component '{existing[0].name}' exists in catalog."
                )

        # Run accessibility & design token verification on provided source files
        if source_files:
            for f in source_files:
                p = Path(f)
                if p.is_file() and p.suffix in (".tsx", ".jsx", ".html", ".vue", ".svelte"):
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        violations = AccessibilityValidator.validate_content(content, file_path=p.name)
                        a11y_issues.extend(violations)
                        token_violations = DesignTokenValidator.validate_content(content, file_path=p.name)
                        token_issues.extend(token_violations)
                    except Exception:
                        pass

        total_issues = len(a11y_issues) + len(token_issues)
        passed = total_issues == 0
        summary = (
            f"UI Governance passed: {len(reused)} components reused, {len(created)} created."
            if passed
            else f"UI Governance warning: {total_issues} issues detected (A11Y: {len(a11y_issues)}, Tokens: {len(token_issues)})."
        )

        return UiGovernanceResult(
            passed=passed,
            components_reused=tuple(reused),
            components_created=tuple(created),
            a11y_violations=tuple(a11y_issues),
            summary=summary,
            design_token_violations=tuple(token_issues),
        )


# ---------------------------------------------------------------------------
# OriginalityGate — ensures AI-generated UI is not generic
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OriginalityGateResult:
    """Result of an originality gate check."""
    passed: bool
    score: float            # 0.0-1.0
    threshold: float
    verdict: str            # "ORIGINAL" | "ACCEPTABLE" | "GENERIC" | "AI_SLOP"
    reason: str


class OriginalityGate:
    """Governance gate that fails if design originality is below a threshold.

    Prevents AI-slop from passing review silently. Integrates with UiDesignStyleEngine
    to score generated UI code against known anti-patterns.

    Usage::

        gate = OriginalityGate(threshold=0.5)
        result = gate.check(code=generated_code)
        if not result.passed:
            # Reject or flag the implementation
            raise ValueError(result.reason)
    """

    DEFAULT_THRESHOLD = 0.5

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self._threshold = threshold

    def check_score(self, score: float, verdict: str = "") -> OriginalityGateResult:
        """Check a pre-computed originality score against the threshold."""
        passed = score >= self._threshold
        if passed:
            reason = f"Design originality score {score:.2f} meets threshold {self._threshold:.2f}."
        else:
            reason = (
                f"Design originality score {score:.2f} is below threshold {self._threshold:.2f}. "
                f"Verdict: {verdict or 'below threshold'}. "
                "Review detected anti-patterns and revise UI implementation."
            )
        return OriginalityGateResult(
            passed=passed,
            score=score,
            threshold=self._threshold,
            verdict=verdict or ("PASS" if passed else "FAIL"),
            reason=reason,
        )

    def check(self, code: str) -> OriginalityGateResult:
        """Score code and check originality. Imports UiDesignStyleEngine lazily."""
        from .ui_design_style_engine import UiDesignStyleEngine
        engine = UiDesignStyleEngine()
        result = engine.score_originality(code)
        return self.check_score(score=result.score, verdict=result.verdict)

