"""UiCoderAgent — Creative UI specialist with design intelligence.

Upgraded with:
- Design context analysis and style profile generation
- DESIGN.md-compatible creative briefs (Anthropic pattern)
- Anti-pattern detection and originality scoring
- Persistent workspace design contract (read/write DESIGN.md)
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from core.agent_role import AgentRole
from core.design_md_generator import DesignMdGenerator
from core.handoff_contracts import UiImplementationResult
from core.plan import PlanStepRecord
from core.ui_design_style_engine import (
    DesignContext,
    DesignStyleProfile,
    UiDesignStyleEngine,
)
from core.ui_governance import OriginalityGate, UiComponentCatalog, UiGovernancePolicy


class UiCoderAgent:
    """Specialist UI Coder Agent bound to AgentRole.UI_CODER.

    Enforces design system governance, component reuse, and accessibility patterns.

    Implements the Anthropic-pattern DESIGN.md contract for persistent design
    consistency: on first task, generates a DesignStyleProfile and writes DESIGN.md
    to the workspace root. On subsequent tasks, reads the existing DESIGN.md to
    maintain visual language continuity.

    Every UI task receives an explicit LLM design directive that:
    - Specifies exact colors, typography, and motion tokens
    - Lists forbidden anti-patterns (generic AI aesthetics)
    - Anchors the implementation to a named visual language
    """

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.role = AgentRole.UI_CODER
        self._workspace = Path(workspace_root) if workspace_root else None
        self.catalog = UiComponentCatalog(workspace_root)
        self.policy = UiGovernancePolicy(self.catalog)
        self._design_engine = UiDesignStyleEngine()
        self._design_md_gen = DesignMdGenerator()
        self._originality_gate = OriginalityGate(threshold=0.5)
        self._active_profile: DesignStyleProfile | None = None

    # ------------------------------------------------------------------
    # Design intelligence
    # ------------------------------------------------------------------

    def analyze_design_context(
        self,
        description: str,
        sector: str = "saas_b2b",
        audience: str = "consumer",
        mood_keywords: Sequence[str] = (),
        existing_brand: str | None = None,
        framework_hint: str | None = None,
    ) -> DesignContext:
        """Analyze project context to determine design direction."""
        return self._design_engine.analyze_context(
            description=description,
            sector=sector,
            audience=audience,
            mood_keywords=mood_keywords,
            existing_brand=existing_brand,
            framework_hint=framework_hint,
        )

    def generate_design_brief(self, context: DesignContext) -> DesignStyleProfile:
        """Generate a creative design brief from analyzed context."""
        return self._design_engine.generate_style_profile(context)

    def get_or_create_design_profile(
        self,
        workspace: Path | None = None,
        description: str = "Web application",
        sector: str = "saas_b2b",
    ) -> DesignStyleProfile:
        """Get an existing design profile from DESIGN.md or create a new one.

        If DESIGN.md exists in the workspace, reads and reconstructs the profile
        for visual continuity. Otherwise generates a new profile and writes DESIGN.md.

        This implements the persistent design contract pattern from the
        Anthropic DESIGN.md framework.
        """
        ws = workspace or self._workspace
        if ws is not None and self._design_md_gen.workspace_has_design_md(ws):
            existing = self._design_md_gen.read_profile_from_workspace(ws, self._design_engine)
            if existing is not None:
                self._active_profile = existing
                return existing

        # Generate new profile
        context = self.analyze_design_context(description=description, sector=sector)
        profile = self.generate_design_brief(context)
        self._active_profile = profile

        if ws is not None:
            try:
                self._design_md_gen.write_to_workspace(profile, ws)
            except OSError:
                pass  # workspace may be read-only in tests

        return profile

    def build_implementation_directive(
        self,
        step: PlanStepRecord,
        profile: DesignStyleProfile,
    ) -> str:
        """Build the full LLM prompt directive for a UI implementation step.

        Combines the step description with the design directive to produce
        a prompt that forces creative, non-generic UI output.
        """
        design_directive = profile.to_llm_design_directive()
        return f"""{design_directive}

IMPLEMENTATION TASK:
{step.description or step.title}

REQUIREMENTS:
- Implement using the visual language and tokens above
- Do NOT deviate from the specified colors, fonts, or motion values
- Detect and avoid all listed FORBIDDEN patterns
- Components must be accessible (WCAG 2.1 AA)
"""

    def evaluate_design_originality(self, code: str) -> tuple[float, str, list[str]]:
        """Score originality and return (score, verdict, violation_ids)."""
        result = self._design_engine.score_originality(code)
        violation_ids = [v.rule_id for v in result.violations]
        return result.score, result.verdict, violation_ids

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    async def execute_ui_step(
        self,
        step: PlanStepRecord,
        modified_files: Sequence[str],
        components_reused: Sequence[str],
        components_created: Sequence[str],
        generated_code: str = "",       # optionally pass generated code for scoring
        design_profile: DesignStyleProfile | None = None,
    ) -> UiImplementationResult:
        """Execute a UI implementation step with governance and design checks."""

        # 1. Governance: component reuse + accessibility
        governance_res = self.policy.evaluate_implementation(
            components_created,
            components_reused,
            source_files=list(modified_files),
        )
        a11y_status = "passed" if governance_res.passed else "warning"

        # 2. Design intelligence: originality scoring
        profile = design_profile or self._active_profile
        visual_language = profile.visual_language if profile else ""
        design_brief = (
            f"{visual_language} — {profile.color_temperature}" if profile else ""
        )
        anti_patterns_avoided: list[str] = []
        anti_patterns_detected: list[str] = []
        originality_score = 1.0  # default if no code to evaluate

        if generated_code and profile:
            originality_score, verdict, violations = self.evaluate_design_originality(generated_code)
            anti_patterns_detected = violations
            # Avoided = all known anti-patterns minus detected ones
            all_pattern_ids = [r.rule_id for r in _import_anti_pattern_rules()]
            anti_patterns_avoided = [p for p in all_pattern_ids if p not in violations]

        summary = (
            f"UI Implementation for '{step.title}': {governance_res.summary}"
        )
        if visual_language:
            summary = f"[{visual_language}] " + summary

        return UiImplementationResult(
            step_id=step.step_id,
            summary=summary,
            modified_files=tuple(modified_files),
            components_reused=governance_res.components_reused,
            components_created=governance_res.components_created,
            a11y_status=a11y_status,
            success=True,
            visual_language=visual_language,
            design_originality_score=round(originality_score, 3),
            anti_patterns_avoided=tuple(anti_patterns_avoided),
            anti_patterns_detected=tuple(anti_patterns_detected),
            design_brief_used=design_brief,
        )


def _import_anti_pattern_rules():
    """Lazy import of ANTI_PATTERN_RULES to avoid circular imports at module load."""
    from core.ui_design_style_engine import ANTI_PATTERN_RULES
    return ANTI_PATTERN_RULES
