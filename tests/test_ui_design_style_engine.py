"""Tests for core/ui_design_style_engine.py — UI Creative Engine."""
from __future__ import annotations

import pytest

from core.ui_design_style_engine import (
    ANTI_PATTERN_RULES,
    AntiPatternViolation,
    DesignAntiPatternDetector,
    DesignContext,
    DesignContextAnalyzer,
    DesignStyleProfile,
    OriginalityScore,
    UiDesignStyleEngine,
)


# ---------------------------------------------------------------------------
# Helpers — sample code snippets
# ---------------------------------------------------------------------------

GENERIC_TAILWIND_CODE = """
<div className="min-h-screen bg-white">
  <div className="grid grid-cols-3 gap-4">
    <div className="rounded-full bg-blue-500 transition-all duration-200 ease-in-out shadow-sm">
      <h2 className="text-5xl text-center">Welcome</h2>
      <button className="bg-blue-500">Get Started</button>
      <button className="bg-indigo-500">Learn More</button>
    </div>
  </div>
</div>
"""

ORIGINAL_TAILWIND_CODE = """
<div style="background:#F2EDE4; min-height:100vh">
  <header style="border-bottom: 1px solid #D4CBBC">
    <h1 style="font-family:'Cormorant Garamond'; font-size:4rem; letter-spacing:-0.03em">
      Studio Work
    </h1>
  </header>
  <main style="display:grid; grid-template-columns: 2fr 1fr; gap:3rem; padding:4rem">
    <article>
      <p style="font-family:Geist; color:#1A1714; line-height:1.65">Content here.</p>
    </article>
    <aside style="border-left: 2px solid #C45C3B; padding-left:2rem">
      <p>Details</p>
    </aside>
  </main>
</div>
"""


# ---------------------------------------------------------------------------
# Anti-pattern detection
# ---------------------------------------------------------------------------

class TestDesignAntiPatternDetector:

    def test_detects_generic_blue(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect("color: #3B82F6;")
        ids = [v.rule_id for v in violations]
        assert "GENERIC-001" in ids

    def test_detects_rounded_full(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect("className='rounded-full px-4'")
        ids = [v.rule_id for v in violations]
        assert "GENERIC-003" in ids

    def test_detects_ai_gradient_cliche(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect("className='from-blue-500 to-purple-600 bg-gradient-to-r'")
        ids = [v.rule_id for v in violations]
        assert "GENERIC-006" in ids

    def test_detects_generic_shadow(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect("box-shadow: 0 2px 4px rgba(0,0,0,0.1);")
        ids = [v.rule_id for v in violations]
        assert "GENERIC-005" in ids

    def test_detects_default_hover_transition(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect("className='transition-all duration-200 ease-in-out hover:bg-blue-600'")
        ids = [v.rule_id for v in violations]
        assert "GENERIC-010" in ids

    def test_original_design_has_no_violations(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect(ORIGINAL_TAILWIND_CODE)
        assert len(violations) == 0

    def test_generic_code_has_multiple_violations(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect(GENERIC_TAILWIND_CODE)
        assert len(violations) >= 3

    def test_violation_contains_snippet(self) -> None:
        detector = DesignAntiPatternDetector()
        violations = detector.detect("background: #3B82F6;")
        assert len(violations) > 0
        for v in violations:
            assert isinstance(v.match_snippet, str)
            assert len(v.match_snippet) > 0

    # Originality scoring

    def test_originality_score_generic_code_below_threshold(self) -> None:
        detector = DesignAntiPatternDetector()
        result = detector.score_originality(GENERIC_TAILWIND_CODE)
        assert isinstance(result, OriginalityScore)
        assert result.score < 0.75  # not ORIGINAL
        assert result.verdict in ("GENERIC", "AI_SLOP", "ACCEPTABLE")

    def test_originality_score_original_code_is_high(self) -> None:
        detector = DesignAntiPatternDetector()
        result = detector.score_originality(ORIGINAL_TAILWIND_CODE)
        assert result.score >= 0.75
        assert result.verdict == "ORIGINAL"

    def test_originality_score_empty_code_is_perfect(self) -> None:
        detector = DesignAntiPatternDetector()
        result = detector.score_originality("")
        assert result.score == 1.0
        assert result.verdict == "ORIGINAL"
        assert len(result.violations) == 0

    def test_originality_score_penalty_total_matches_violations(self) -> None:
        detector = DesignAntiPatternDetector()
        result = detector.score_originality(GENERIC_TAILWIND_CODE)
        # penalty_total should be positive when violations exist
        if result.violations:
            assert result.penalty_total > 0.0
        assert result.penalty_total + result.score <= 1.01  # floating point tolerance


# ---------------------------------------------------------------------------
# Design context analysis
# ---------------------------------------------------------------------------

class TestDesignContextAnalyzer:

    def test_analyze_returns_design_context(self) -> None:
        analyzer = DesignContextAnalyzer()
        ctx = analyzer.analyze("A fintech platform", sector="fintech")
        assert isinstance(ctx, DesignContext)
        assert ctx.sector == "fintech"

    def test_fintech_sector_archetype(self) -> None:
        analyzer = DesignContextAnalyzer()
        vl, fonts, temp, spacing = analyzer.get_archetype("fintech")
        assert vl == "Architectural Minimalism"
        assert "Public Sans" in fonts or "Geist" in fonts
        assert temp == "cool-neutral"

    def test_creative_portfolio_sector_archetype(self) -> None:
        analyzer = DesignContextAnalyzer()
        vl, fonts, temp, spacing = analyzer.get_archetype("portfolio")
        assert vl == "Neo-Brutalism"
        assert temp == "high-contrast"

    def test_unknown_sector_falls_back_to_saas_b2b(self) -> None:
        analyzer = DesignContextAnalyzer()
        ctx = analyzer.analyze("Unknown thing", sector="unknown_sector_xyz")
        assert ctx.sector == "saas_b2b"

    def test_mood_keywords_override_color_temperature(self) -> None:
        analyzer = DesignContextAnalyzer()
        ctx = analyzer.analyze(
            "A health platform",
            sector="health_wellness",
            mood_keywords=["rebellious"],
        )
        temp = analyzer.resolve_color_temperature(ctx)
        assert temp == "high-contrast"  # rebellious overrides warm-muted

    def test_no_mood_keywords_uses_sector_default(self) -> None:
        analyzer = DesignContextAnalyzer()
        ctx = analyzer.analyze("A wellness app", sector="health_wellness")
        temp = analyzer.resolve_color_temperature(ctx)
        assert temp == "warm-muted"

    def test_mood_premium_resolves_to_warm_rich(self) -> None:
        analyzer = DesignContextAnalyzer()
        ctx = analyzer.analyze("Luxury ecom", sector="ecommerce", mood_keywords=["premium"])
        temp = analyzer.resolve_color_temperature(ctx)
        assert temp == "warm-rich"


# ---------------------------------------------------------------------------
# Style profile generation
# ---------------------------------------------------------------------------

class TestUiDesignStyleEngine:

    def test_generate_profile_returns_design_style_profile(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("Portfolio site", sector="portfolio")
        profile = engine.generate_style_profile(ctx)
        assert isinstance(profile, DesignStyleProfile)

    def test_creative_portfolio_profile_has_dark_surface(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("Creative studio", sector="portfolio")
        profile = engine.generate_style_profile(ctx)
        # Neo-Brutalism uses high-contrast (dark) palette
        assert profile.color_temperature == "high-contrast"
        # Surface should not be white
        assert profile.surface_primary not in ("#ffffff", "#fff", "white")

    def test_saas_b2b_profile_has_cool_clean_palette(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("B2B SaaS analytics", sector="saas_b2b")
        profile = engine.generate_style_profile(ctx)
        assert profile.color_temperature == "cool-clean"

    def test_profile_heading_font_is_not_generic_sans(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("Editorial magazine", sector="creative_agency")
        profile = engine.generate_style_profile(ctx)
        # creative_agency sector should use distinctive fonts, not Inter/Roboto
        generic_fonts = {"Inter", "Roboto", "DM Sans"}
        assert profile.heading_font not in generic_fonts

    def test_profile_forbidden_patterns_contains_generic_rules(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("Any project")
        profile = engine.generate_style_profile(ctx)
        forbidden_text = " ".join(profile.anti_patterns_forbidden)
        assert "GENERIC-001" in forbidden_text
        assert "GENERIC-006" in forbidden_text

    def test_neo_brutalism_has_sharp_border_radius(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("Developer portfolio", sector="portfolio")
        profile = engine.generate_style_profile(ctx)
        assert profile.border_radius in ("0px", "1px", "2px")

    def test_organic_minimalism_has_soft_border_radius(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("Health app", sector="health_wellness")
        profile = engine.generate_style_profile(ctx)
        # Organic should have >= 8px radius
        radius_val = int(profile.border_radius.replace("px", ""))
        assert radius_val >= 8

    def test_design_md_output_is_valid_markdown(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project", sector="fintech")
        profile = engine.generate_style_profile(ctx)
        md = profile.to_design_md(project_name="FinApp")
        assert "# Design System" in md
        assert "## Color Palette" in md
        assert "## Typography" in md
        assert "## Motion" in md
        assert "## Forbidden Patterns" in md

    def test_design_md_contains_actual_color_tokens(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project", sector="fintech")
        profile = engine.generate_style_profile(ctx)
        md = profile.to_design_md()
        # Must contain hex color values
        import re
        hex_colors = re.findall(r"#[0-9A-Fa-f]{6}", md)
        assert len(hex_colors) >= 3

    def test_design_md_contains_typography(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project", sector="creative_agency")
        profile = engine.generate_style_profile(ctx)
        md = profile.to_design_md()
        assert profile.heading_font in md
        assert profile.body_font in md

    def test_design_md_contains_motion_tokens(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project")
        profile = engine.generate_style_profile(ctx)
        md = profile.to_design_md()
        assert "ms" in md  # duration
        assert "cubic-bezier" in md or "ease" in md  # easing

    def test_design_md_contains_forbidden_patterns_section(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project")
        profile = engine.generate_style_profile(ctx)
        md = profile.to_design_md()
        assert "Forbidden" in md or "DO NOT" in md
        assert "GENERIC-001" in md

    def test_llm_directive_is_actionable(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project", sector="fintech")
        profile = engine.generate_style_profile(ctx)
        directive = profile.to_llm_design_directive()
        assert "MANDATORY" in directive
        assert "FORBIDDEN" in directive or "DO NOT" in directive
        assert "#" in directive  # hex colors present

    def test_llm_directive_contains_explicit_forbidden_colors(self) -> None:
        engine = UiDesignStyleEngine()
        ctx = engine.analyze_context("My project")
        profile = engine.generate_style_profile(ctx)
        directive = profile.to_llm_design_directive()
        # The forbidden section should list specific rule IDs
        assert "GENERIC-001" in directive
        assert "GENERIC-006" in directive

    def test_score_originality_delegates_to_detector(self) -> None:
        engine = UiDesignStyleEngine()
        result = engine.score_originality(GENERIC_TAILWIND_CODE)
        assert isinstance(result, OriginalityScore)
        assert result.score < 1.0  # generic code should not be perfect

    def test_detect_antipatterns_delegates_to_detector(self) -> None:
        engine = UiDesignStyleEngine()
        violations = engine.detect_antipatterns("color: #3B82F6;")
        assert len(violations) > 0
        assert all(isinstance(v, AntiPatternViolation) for v in violations)
