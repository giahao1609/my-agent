"""UI Design Style Engine — Creative design intelligence for UiCoderAgent.

Implements:
- DesignAntiPatternDetector: identify "AI-slop" generic patterns in UI code
- DesignContextAnalyzer: analyze project context to determine design direction
- DesignStyleProfile: structured design brief (DESIGN.md compatible)
- UiDesignStyleEngine: orchestrator generating creative, non-generic design briefs

Based on:
- Anthropic's DESIGN.md pattern (structured design contract for agents)
- 2025 creative direction principles: Editorial Minimalism, Neo-Brutalism,
  Kinetic Glassmorphism, Swiss-inspired design
- shadcn/ui, Magic UI, Aceternity UI design language patterns
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Anti-pattern definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AntiPatternRule:
    rule_id: str
    name: str
    description: str
    pattern: re.Pattern[str]
    penalty: float          # 0.0-1.0 how much this hurts originality score


# "AI slop" patterns — what generic AI-generated UIs look like
ANTI_PATTERN_RULES: tuple[AntiPatternRule, ...] = (
    AntiPatternRule(
        rule_id="GENERIC-001",
        name="Generic AI Primary Blue",
        description="Blue #3B82F6 or indigo #6366F1 as primary — the default AI color choice.",
        pattern=re.compile(r"#3[Bb]82[Ff]6|#6366[Ff]1|#8[Bb]5[Cc][Ff]6|blue-500|indigo-500|violet-500", re.IGNORECASE),
        penalty=0.12,
    ),
    AntiPatternRule(
        rule_id="GENERIC-002",
        name="Overused Default Fonts",
        description="Inter, Roboto, or DM Sans used as the sole typeface without distinctive pairing.",
        pattern=re.compile(r"font-family['\"]?\s*:\s*['\"]?(Inter|Roboto|DM\s+Sans)['\"]?\s*;?(?!.*\b(Cormorant|Syne|Playfair|Bebas|Space\s+Grotesk|Geist|IBM\s+Plex))", re.IGNORECASE),
        penalty=0.10,
    ),
    AntiPatternRule(
        rule_id="GENERIC-003",
        name="Universal Pill / Full-Round Radius",
        description="border-radius: 9999px or rounded-full applied uniformly on everything.",
        pattern=re.compile(r"rounded-full|border-radius\s*:\s*9999px|border-radius\s*:\s*50%", re.IGNORECASE),
        penalty=0.08,
    ),
    AntiPatternRule(
        rule_id="GENERIC-004",
        name="Soulless 3-Column Card Grid",
        description="Uniform 3-column card grid without visual hierarchy — the default AI layout.",
        pattern=re.compile(r"grid-cols-3|grid-template-columns\s*:\s*repeat\(3|sm:grid-cols-3\s+md:grid-cols-3", re.IGNORECASE),
        penalty=0.08,
    ),
    AntiPatternRule(
        rule_id="GENERIC-005",
        name="AI Default Shadow Formula",
        description="box-shadow: 0 2px 4px rgba(0,0,0,0.1) — the most generic elevation style.",
        pattern=re.compile(r"box-shadow\s*:\s*0\s+[12]px\s+[48]px\s+rgba\s*\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\.[012]\s*\)", re.IGNORECASE),
        penalty=0.07,
    ),
    AntiPatternRule(
        rule_id="GENERIC-006",
        name="Blue-to-Purple Gradient Cliché",
        description="Gradient from blue/indigo to purple/violet — the AI aesthetic signature.",
        pattern=re.compile(r"from-blue-\d+\s+to-purple-\d+|from-indigo-\d+\s+to-violet-\d+|from-blue.*?to-purple|#4F46E5.*?#7C3AED", re.IGNORECASE),
        penalty=0.12,
    ),
    AntiPatternRule(
        rule_id="GENERIC-007",
        name="Blank White Canvas Default",
        description="bg-white min-h-screen as the entire page background — zero personality.",
        pattern=re.compile(r"bg-white\s+min-h-screen|background(-color)?\s*:\s*#(?:fff|ffffff)\s*;?\s*min-height\s*:\s*100vh", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="GENERIC-008",
        name="Centered Hero with 2 CTAs",
        description="text-center hero section with huge h1 + subtitle + exactly 2 CTA buttons — the AI template.",
        pattern=re.compile(r"text-center.*?text-[45678]xl.*?(?:text-[12]xl|text-xl).*?(?:<button|btn).*?(?:<button|btn)", re.IGNORECASE | re.DOTALL),
        penalty=0.10,
    ),
    AntiPatternRule(
        rule_id="GENERIC-009",
        name="Lucide/Heroicons Uniform Primary Color",
        description="Icons all rendered in stroke-current or text-primary — no visual depth.",
        pattern=re.compile(r"stroke-current\s+text-(?:primary|blue|indigo)-\d+|className=\"[^\"]*stroke-current[^\"]*text-[a-z]+-\d+", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="GENERIC-010",
        name="Default AI Hover Transition",
        description="transition-all duration-200 ease-in-out — exact generic hover transition.",
        pattern=re.compile(r"transition-all\s+duration-200\s+ease-in-out|transition\s*:\s*all\s+0\.2s\s+ease-in-out", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="GENERIC-011",
        name="Gray-50 / Gray-100 Background",
        description="bg-gray-50 or bg-gray-100 as page background — clinical, no warmth.",
        pattern=re.compile(r"\bbg-gray-(?:50|100)\b|background(-color)?\s*:\s*#[Ff][89Aa][Ff][89Aa][Ff][89Aa]", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="GENERIC-012",
        name="Pure Black Text on White",
        description="#000000 or #111111 on #ffffff — no color story, no warmth.",
        pattern=re.compile(r"color\s*:\s*#(?:000|000000|111|111111)\s*;[^}]*background(-color)?\s*:\s*#(?:fff|ffffff)", re.IGNORECASE),
        penalty=0.05,
    ),
)


@dataclass(frozen=True, slots=True)
class AntiPatternViolation:
    rule_id: str
    rule_name: str
    description: str
    match_snippet: str      # the matching code snippet

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "description": self.description,
            "match_snippet": self.match_snippet,
        }


@dataclass(frozen=True, slots=True)
class OriginalityScore:
    score: float                    # 0.0-1.0 (1.0 = highly original)
    violations: tuple[AntiPatternViolation, ...]
    penalty_total: float
    verdict: str                    # "ORIGINAL" | "ACCEPTABLE" | "GENERIC" | "AI_SLOP"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "penalty_total": self.penalty_total,
            "violations": [v.to_dict() for v in self.violations],
        }


class DesignAntiPatternDetector:
    """Detects 'AI-slop' anti-patterns in UI source code.

    Scans CSS, JSX, TSX, HTML, and Vue/Svelte files for patterns that indicate
    generic, unimaginative, or AI-template-driven design choices.
    """

    VERDICT_THRESHOLDS = {
        "ORIGINAL": 0.75,
        "ACCEPTABLE": 0.5,
        "GENERIC": 0.25,
    }

    def detect(self, code: str) -> list[AntiPatternViolation]:
        """Detect all anti-pattern violations in code."""
        violations: list[AntiPatternViolation] = []
        for rule in ANTI_PATTERN_RULES:
            match = rule.pattern.search(code)
            if match:
                snippet = code[max(0, match.start() - 20):match.end() + 20].strip()
                violations.append(AntiPatternViolation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    description=rule.description,
                    match_snippet=snippet[:120],
                ))
        return violations

    def score_originality(self, code: str) -> OriginalityScore:
        """Score the originality of a UI implementation. 1.0 = highly original."""
        violations = self.detect(code)
        penalty = sum(
            r.penalty for r in ANTI_PATTERN_RULES
            if any(v.rule_id == r.rule_id for v in violations)
        )
        raw_score = max(0.0, 1.0 - penalty)

        if raw_score >= self.VERDICT_THRESHOLDS["ORIGINAL"]:
            verdict = "ORIGINAL"
        elif raw_score >= self.VERDICT_THRESHOLDS["ACCEPTABLE"]:
            verdict = "ACCEPTABLE"
        elif raw_score >= self.VERDICT_THRESHOLDS["GENERIC"]:
            verdict = "GENERIC"
        else:
            verdict = "AI_SLOP"

        return OriginalityScore(
            score=round(raw_score, 3),
            violations=tuple(violations),
            penalty_total=round(penalty, 3),
            verdict=verdict,
        )


# ---------------------------------------------------------------------------
# Design context
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DesignContext:
    """Analyzed design context for a project."""
    project_description: str
    sector: str                         # "fintech", "creative_agency", etc.
    target_audience: str                # "enterprise", "consumer", "developer"
    mood_keywords: tuple[str, ...]      # "trustworthy", "playful", "premium"
    existing_brand: str | None = None   # hex colors or font names if known
    framework_hint: str | None = None   # "react", "vue", "vanilla"


# Sector -> (visual_language, font_suggestions, color_temperature, spacing)
_SECTOR_ARCHETYPES: dict[str, tuple[str, list[str], str, str]] = {
    "fintech": (
        "Architectural Minimalism",
        ["Public Sans", "Geist", "IBM Plex Sans"],
        "cool-neutral",
        "structured",
    ),
    "creative_agency": (
        "Editorial Avant-garde",
        ["Syne", "Cormorant Garamond", "Playfair Display"],
        "warm-contrast",
        "editorial",
    ),
    "health_wellness": (
        "Organic Minimalism",
        ["DM Serif Display", "Nunito", "Lora"],
        "warm-muted",
        "generous",
    ),
    "saas_b2b": (
        "Structured Clarity",
        ["Geist", "IBM Plex Sans", "Inter (with Display pairing)"],
        "cool-clean",
        "dense",
    ),
    "portfolio": (
        "Neo-Brutalism",
        ["Space Grotesk", "Bebas Neue", "Neue Haas Grotesk"],
        "high-contrast",
        "editorial",
    ),
    "ecommerce": (
        "Tactile Editorial",
        ["Cormorant Garamond", "Neue Haas Grotesk", "Freight Display"],
        "warm-rich",
        "generous",
    ),
    "developer_tool": (
        "Monochrome Technical",
        ["JetBrains Mono", "Geist Mono", "Berkeley Mono"],
        "dark-muted",
        "dense",
    ),
    "startup_consumer": (
        "Kinetic Playful",
        ["Nunito", "Boogaloo", "Quicksand"],
        "vibrant",
        "generous",
    ),
    "education": (
        "Warm Academic",
        ["Merriweather", "Source Sans 3", "Literata"],
        "warm-neutral",
        "generous",
    ),
    "data_analytics": (
        "Precision Dashboard",
        ["Inter Display", "Barlow", "Source Sans 3"],
        "cool-dark",
        "dense",
    ),
}

_MOOD_TO_COLOR_TEMPERATURE: dict[str, str] = {
    "trustworthy": "cool-neutral",
    "playful": "vibrant",
    "premium": "warm-rich",
    "rebellious": "high-contrast",
    "calm": "warm-muted",
    "innovative": "cool-dark",
    "professional": "cool-clean",
    "creative": "warm-contrast",
    "minimal": "warm-neutral",
    "bold": "high-contrast",
}


class DesignContextAnalyzer:
    """Analyzes project context and determines design direction."""

    def analyze(
        self,
        description: str,
        sector: str = "saas_b2b",
        audience: str = "consumer",
        mood_keywords: Sequence[str] = (),
        existing_brand: str | None = None,
        framework_hint: str | None = None,
    ) -> DesignContext:
        # Normalize sector
        sector_key = sector.lower().replace(" ", "_").replace("-", "_")
        if sector_key not in _SECTOR_ARCHETYPES:
            sector_key = "saas_b2b"  # safe default

        return DesignContext(
            project_description=description,
            sector=sector_key,
            target_audience=audience,
            mood_keywords=tuple(mood_keywords),
            existing_brand=existing_brand,
            framework_hint=framework_hint,
        )

    def get_archetype(self, sector: str) -> tuple[str, list[str], str, str]:
        """Return (visual_language, fonts, color_temp, spacing) for sector."""
        return _SECTOR_ARCHETYPES.get(sector, _SECTOR_ARCHETYPES["saas_b2b"])

    def resolve_color_temperature(self, context: DesignContext) -> str:
        """Resolve final color temperature from mood keywords + sector archetype."""
        _, _, sector_temp, _ = self.get_archetype(context.sector)
        # Mood keywords can override sector default
        for mood in context.mood_keywords:
            mood_temp = _MOOD_TO_COLOR_TEMPERATURE.get(mood.lower())
            if mood_temp:
                return mood_temp
        return sector_temp


# ---------------------------------------------------------------------------
# Design style profile
# ---------------------------------------------------------------------------

# Color palettes per temperature
_COLOR_PALETTES: dict[str, dict[str, str]] = {
    "warm-contrast": {
        "surface_primary": "#F2EDE4",   # warm linen
        "surface_secondary": "#FFFCF7",
        "text_primary": "#1A1714",      # deep espresso
        "accent": "#C45C3B",            # burnt sienna
        "border": "#D4CBBC",
    },
    "cool-neutral": {
        "surface_primary": "#F6F7F8",   # cool white-gray
        "surface_secondary": "#FFFFFF",
        "text_primary": "#1C2128",      # deep navy-charcoal
        "accent": "#2D6A4F",            # forest green
        "border": "#D0D7DE",
    },
    "warm-muted": {
        "surface_primary": "#FAF7F2",   # warm cream
        "surface_secondary": "#FFFFFF",
        "text_primary": "#2C2416",      # dark brown
        "accent": "#6B8F71",            # sage green
        "border": "#DDD5C8",
    },
    "warm-rich": {
        "surface_primary": "#FBF8F3",   # ivory
        "surface_secondary": "#FFFFFF",
        "text_primary": "#1C140E",      # near-black with warm undertone
        "accent": "#9B2335",            # deep crimson
        "border": "#E8DECE",
    },
    "high-contrast": {
        "surface_primary": "#0D0D0D",   # near-black
        "surface_secondary": "#1A1A1A",
        "text_primary": "#F5F5F0",      # warm off-white
        "accent": "#FFD60A",            # electric yellow
        "border": "#333333",
    },
    "cool-clean": {
        "surface_primary": "#F8F9FA",
        "surface_secondary": "#FFFFFF",
        "text_primary": "#212529",
        "accent": "#0F6FEC",
        "border": "#DEE2E6",
    },
    "vibrant": {
        "surface_primary": "#FAFAF9",
        "surface_secondary": "#FFFFFF",
        "text_primary": "#1A1A1A",
        "accent": "#FF6B35",            # vivid orange
        "border": "#E0E0E0",
    },
    "cool-dark": {
        "surface_primary": "#0F1117",
        "surface_secondary": "#161B22",
        "text_primary": "#E6EDF3",
        "accent": "#58A6FF",
        "border": "#30363D",
    },
    "dark-muted": {
        "surface_primary": "#141414",
        "surface_secondary": "#1E1E1E",
        "text_primary": "#D4D4D4",
        "accent": "#4EC9B0",            # teal/mint for code
        "border": "#2D2D2D",
    },
    "warm-neutral": {
        "surface_primary": "#F9F6F1",
        "surface_secondary": "#FFFFFF",
        "text_primary": "#231F1A",
        "accent": "#8B6F47",            # warm brown
        "border": "#DDD5C5",
    },
}

# Border radius per visual language
_RADIUS_MAP: dict[str, str] = {
    "Architectural Minimalism": "2px",
    "Editorial Avant-garde": "0px",
    "Organic Minimalism": "12px",
    "Structured Clarity": "6px",
    "Neo-Brutalism": "0px",
    "Tactile Editorial": "4px",
    "Monochrome Technical": "4px",
    "Kinetic Playful": "16px",
    "Warm Academic": "8px",
    "Precision Dashboard": "4px",
}

# Motion per visual language
_MOTION_MAP: dict[str, tuple[str, str, str]] = {
    # (personality, easing, duration_base)
    "Architectural Minimalism": ("precise", "cubic-bezier(0.25, 0.1, 0.25, 1)", "180ms"),
    "Editorial Avant-garde": ("dramatic", "cubic-bezier(0.76, 0, 0.24, 1)", "350ms"),
    "Organic Minimalism": ("fluid", "cubic-bezier(0.34, 1.56, 0.64, 1)", "280ms"),
    "Structured Clarity": ("snappy", "cubic-bezier(0.4, 0, 0.2, 1)", "150ms"),
    "Neo-Brutalism": ("abrupt", "steps(4, end)", "100ms"),
    "Tactile Editorial": ("measured", "cubic-bezier(0.45, 0, 0.55, 1)", "240ms"),
    "Monochrome Technical": ("minimal", "ease-out", "120ms"),
    "Kinetic Playful": ("bouncy", "cubic-bezier(0.34, 1.56, 0.64, 1)", "300ms"),
    "Warm Academic": ("gentle", "cubic-bezier(0.25, 0.46, 0.45, 0.94)", "220ms"),
    "Precision Dashboard": ("efficient", "cubic-bezier(0.4, 0, 0.2, 1)", "140ms"),
}


@dataclass(frozen=True, slots=True)
class DesignStyleProfile:
    """Structured design brief — DESIGN.md compatible output.

    Encapsulates all creative decisions needed for an agent to produce
    distinctive, non-generic UI implementations.
    """

    # Identity
    visual_language: str
    design_rationale: str

    # Color
    surface_primary: str
    surface_secondary: str
    text_primary: str
    accent: str
    border_color: str
    color_temperature: str

    # Typography
    heading_font: str
    body_font: str
    accent_font: str               # optional display/mono for special use

    # Shape
    border_radius: str             # "0px", "4px", "12px" etc.
    spacing_philosophy: str        # "generous", "dense", "editorial", "structured"

    # Motion
    motion_personality: str        # "snappy", "fluid", "dramatic"
    transition_easing: str
    transition_duration_base: str

    # Governance
    anti_patterns_forbidden: tuple[str, ...]

    def to_design_md(self, project_name: str = "Project") -> str:
        """Generate a DESIGN.md compatible document."""
        forbidden_list = "\n".join(f"- {p}" for p in self.anti_patterns_forbidden)
        return f"""# Design System — {project_name}

## Visual Language
**{self.visual_language}**

{self.design_rationale}

---

## Color Palette

| Token | Value | Usage |
|---|---|---|
| `surface.primary` | `{self.surface_primary}` | Main page background |
| `surface.secondary` | `{self.surface_secondary}` | Card / elevated surfaces |
| `text.primary` | `{self.text_primary}` | Primary body text |
| `accent` | `{self.accent}` | CTAs, highlights, interactive |
| `border` | `{self.border_color}` | Dividers, outlines |

Color temperature: **{self.color_temperature}**

---

## Typography

- **Headings**: {self.heading_font}
- **Body**: {self.body_font}
- **Accent / Mono**: {self.accent_font}

---

## Shape & Spacing

- Border radius: **{self.border_radius}** (apply consistently, do not mix with rounded-full)
- Spacing philosophy: **{self.spacing_philosophy}**

---

## Motion

- Personality: **{self.motion_personality}**
- Easing: `{self.transition_easing}`
- Base duration: `{self.transition_duration_base}`

---

## Forbidden Patterns (DO NOT USE)

{forbidden_list}
"""

    def to_llm_design_directive(self) -> str:
        """Generate an authoritative prompt directive for the LLM coding agent.

        This string should be prepended to any UI implementation prompt to
        force creative, non-generic design choices.
        """
        forbidden = "\n".join(f"  - {p}" for p in self.anti_patterns_forbidden)
        return f"""DESIGN DIRECTIVE (MANDATORY — do not deviate from these specifications):

Visual Language: {self.visual_language}
Rationale: {self.design_rationale}

COLORS (use EXACTLY these values, do not substitute):
  Surface primary:   {self.surface_primary}
  Surface secondary: {self.surface_secondary}
  Text primary:      {self.text_primary}
  Accent:            {self.accent}
  Border:            {self.border_color}

TYPOGRAPHY:
  Headings:  {self.heading_font}
  Body:      {self.body_font}
  Accent:    {self.accent_font}

SHAPE:
  Border radius: {self.border_radius} (uniform on components, NO rounded-full)
  Spacing: {self.spacing_philosophy}

MOTION:
  Personality: {self.motion_personality}
  Easing: {self.transition_easing}
  Duration: {self.transition_duration_base}

FORBIDDEN — DO NOT USE THESE IN ANY CIRCUMSTANCES:
{forbidden}
"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_language": self.visual_language,
            "design_rationale": self.design_rationale,
            "surface_primary": self.surface_primary,
            "surface_secondary": self.surface_secondary,
            "text_primary": self.text_primary,
            "accent": self.accent,
            "border_color": self.border_color,
            "color_temperature": self.color_temperature,
            "heading_font": self.heading_font,
            "body_font": self.body_font,
            "accent_font": self.accent_font,
            "border_radius": self.border_radius,
            "spacing_philosophy": self.spacing_philosophy,
            "motion_personality": self.motion_personality,
            "transition_easing": self.transition_easing,
            "transition_duration_base": self.transition_duration_base,
            "anti_patterns_forbidden": list(self.anti_patterns_forbidden),
        }



# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class UiDesignStyleEngine:
    """Orchestrates creative design profile generation for UiCoderAgent.

    Usage::

        engine = UiDesignStyleEngine()
        context = engine.analyze_context(
            description="SaaS analytics platform for data scientists",
            sector="data_analytics",
            mood_keywords=["precision", "innovative"],
        )
        profile = engine.generate_style_profile(context)
        directive = profile.to_llm_design_directive()
        # Inject directive into UiCoderAgent's LLM prompt
    """

    def __init__(self) -> None:
        self._analyzer = DesignContextAnalyzer()
        self._detector = DesignAntiPatternDetector()

    def analyze_context(
        self,
        description: str,
        sector: str = "saas_b2b",
        audience: str = "consumer",
        mood_keywords: Sequence[str] = (),
        existing_brand: str | None = None,
        framework_hint: str | None = None,
    ) -> DesignContext:
        return self._analyzer.analyze(
            description=description,
            sector=sector,
            audience=audience,
            mood_keywords=mood_keywords,
            existing_brand=existing_brand,
            framework_hint=framework_hint,
        )

    def generate_style_profile(self, context: DesignContext) -> DesignStyleProfile:
        """Generate a DesignStyleProfile from analyzed context."""
        visual_language, fonts, _, spacing = self._analyzer.get_archetype(context.sector)
        color_temp = self._analyzer.resolve_color_temperature(context)
        palette = _COLOR_PALETTES.get(color_temp, _COLOR_PALETTES["cool-clean"])
        radius = _RADIUS_MAP.get(visual_language, "6px")
        motion_personality, easing, duration = _MOTION_MAP.get(
            visual_language,
            ("snappy", "cubic-bezier(0.4, 0, 0.2, 1)", "150ms"),
        )

        # Build rationale
        audience_desc = context.target_audience or "general users"
        mood_desc = ", ".join(context.mood_keywords) if context.mood_keywords else "balanced"
        rationale = (
            f"Designed for {audience_desc} in the {context.sector.replace('_', ' ')} space. "
            f"Mood direction: {mood_desc}. "
            f"Visual language '{visual_language}' was selected to signal "
            f"opinionated taste while avoiding AI-template aesthetics."
        )

        # Build forbidden patterns list from anti-pattern rules
        forbidden = [
            f"[{r.rule_id}] {r.name}: {r.description}"
            for r in ANTI_PATTERN_RULES
        ]

        return DesignStyleProfile(
            visual_language=visual_language,
            design_rationale=rationale,
            surface_primary=palette["surface_primary"],
            surface_secondary=palette["surface_secondary"],
            text_primary=palette["text_primary"],
            accent=palette["accent"],
            border_color=palette["border"],
            color_temperature=color_temp,
            heading_font=fonts[0] if fonts else "System UI",
            body_font=fonts[1] if len(fonts) > 1 else "System UI",
            accent_font=fonts[2] if len(fonts) > 2 else fonts[0] if fonts else "monospace",
            border_radius=radius,
            spacing_philosophy=spacing,
            motion_personality=motion_personality,
            transition_easing=easing,
            transition_duration_base=duration,
            anti_patterns_forbidden=tuple(forbidden),
        )

    def score_originality(self, code: str) -> OriginalityScore:
        """Score the originality of a UI code snippet."""
        return self._detector.score_originality(code)

    def detect_antipatterns(self, code: str) -> list[AntiPatternViolation]:
        """Detect anti-pattern violations in a UI code snippet."""
        return self._detector.detect(code)
