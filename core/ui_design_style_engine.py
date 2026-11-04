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
    AntiPatternRule(
        rule_id="GENERIC-013",
        name="Low-Contrast Muted Text",
        description="text-gray-400 or text-slate-400 used for content text on light background, violating WCAG AA 4.5:1.",
        pattern=re.compile(r"\btext-(?:gray|slate|zinc|neutral)-400\b", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="GENERIC-014",
        name="Sub-40px Touch Target",
        description="Button or interactive element with height under 40px (h-6, h-7, py-0.5, py-1) violating mobile accessibility.",
        pattern=re.compile(r"\b(?:h-6|h-7|py-0\.5|py-1)\s+(?:px-[123]|w-)\b.*?(?:<button|btn|onClick)", re.IGNORECASE | re.DOTALL),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="GENERIC-015",
        name="Nested Scrollbar Syndrome",
        description="Inner container with overflow-y-auto nested inside another scroll container causing dual scrollbars.",
        pattern=re.compile(r"overflow-(?:y-)?auto.*?overflow-(?:y-)?auto", re.IGNORECASE | re.DOTALL),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="GENERIC-016",
        name="Generic AI Placeholder Avatar",
        description="via.placeholder.com, placeholder-avatar, or generic unstyled initials circle.",
        pattern=re.compile(r"via\.placeholder\.com|avatar-placeholder|default-avatar\.png", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="GENERIC-017",
        name="Inaccessible Div Click Trap",
        description="<div onClick=...> without role='button' or tabIndex, breaking keyboard and screen reader access.",
        pattern=re.compile(r"<div[^>]*\bonClick\b(?![^>]*\brole=)(?![^>]*\btabIndex=)", re.IGNORECASE),
        penalty=0.08,
    ),
    AntiPatternRule(
        rule_id="GENERIC-018",
        name="Monolithic Unlayered Border",
        description="border border-gray-200 applied uniformly without visual elevation or hierarchy.",
        pattern=re.compile(r"\bborder\s+border-(?:gray|slate|zinc)-200\b", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="GENERIC-019",
        name="Backdrop Without Blur",
        description="Modal or drawer backdrop with bg-black/20 or bg-white/90 without backdrop-blur.",
        pattern=re.compile(r"\b(?:bg-black\/[123]0|bg-white\/[89]0)\b(?![^\"']*backdrop-blur)", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="GENERIC-020",
        name="Generic AI Loading Spinner",
        description="animate-spin rounded-full border-b-2 border-blue-500 — the exact default AI spinner.",
        pattern=re.compile(r"animate-spin\s+rounded-full\s+border-[a-z]-[12]\s+border-(?:blue|indigo)-500", re.IGNORECASE),
        penalty=0.07,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-001",
        name="Repetitive Card Clone Syndrome",
        description="Uniform row or grid of identical cards without visual hierarchy or featured focal anchor.",
        pattern=re.compile(r"(?:className=[\"'][^\"']*(?:p-[46]|rounded)[^\"']*border[^\"']*[\"'].*?){3,}", re.IGNORECASE | re.DOTALL),
        penalty=0.08,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-002",
        name="Lack of Tactile Click Feedback",
        description="Buttons or clickable items without active state (active:scale-[0.98] or active:translate-y).",
        pattern=re.compile(r"<button\b(?![^>]*\bactive:(?:scale|translate|bg|ring)\b)[^>]*className=[\"'][^\"']*[\"']", re.IGNORECASE),
        penalty=0.04,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-003",
        name="Unhandled Text Overflow",
        description="Dynamic variable in heading or card title without truncate or line-clamp, risking layout breakage.",
        pattern=re.compile(r"<(?:h[1-6]|span|p)\b(?![^>]*\b(?:truncate|line-clamp-\d)\b)[^>]*className=[\"'][^\"']*(?:text-(?:lg|xl|2xl|3xl)|font-(?:bold|semibold))[^\"']*[\"'][^>]*>\{\s*(?:item|course|lesson|data|props|user|doc)\.[a-zA-Z0-9_]+", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-004",
        name="Missing Skeleton Shimmer Loader",
        description="Raw spinner used for content area instead of structured skeleton loader matching content geometry.",
        pattern=re.compile(r"(?:isLoading|loading)\s*\?\s*\(?\s*<div[^>]*animate-spin", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-005",
        name="Lazy Raw Empty State",
        description="Raw text 'No items' or 'No data' rendered without an illustrated container, helpful guidance, or action CTA.",
        pattern=re.compile(r">\s*(?:No\s+data(?:\s+found)?|No\s+items(?:\s+available)?|Chưa\s+có\s+dữ\s+liệu)\s*<", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-006",
        name="Removed Focus Outline Without Ring Replacement",
        description="focus:outline-none used without providing a visible focus-visible:ring-2 replacement for keyboard a11y.",
        pattern=re.compile(r"\bfocus:outline-none\b(?![^\"']*\bfocus-visible:ring)", re.IGNORECASE),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-007",
        name="Stat Box Row Cliché",
        description="Uniform row of 3-4 stat boxes with just huge text and tiny bottom label without sparkline or delta badge.",
        pattern=re.compile(r"(?:text-[234]xl\s+font-bold.*?text-(?:xs|sm)\s+text-(?:gray|slate)-[45]00.*?){3,}", re.IGNORECASE | re.DOTALL),
        penalty=0.06,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-008",
        name="Unstyled Generic Input Form",
        description="Input without transition, focus ring, or icon prefix, feeling like an unstyled browser form.",
        pattern=re.compile(r"<input\b[^>]*className=[\"'][^\"']*\bborder\s+border-(?:gray|slate)-[23]00\b(?![^\"']*focus:(?:ring|border))[^\"']*[\"']", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-009",
        name="Unstructured Arbitrary Z-Index",
        description="Arbitrary z-[9999] or uncoordinated high z-index instead of structured token layers.",
        pattern=re.compile(r"\bz-(?:\[\d{3,5}\]|999\b|9999\b)", re.IGNORECASE),
        penalty=0.04,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-010",
        name="Missing Main Landmark or Skip Link",
        description="Full page view without semantic <main> landmark tag, hurting screen reader accessibility.",
        pattern=re.compile(r"min-h-screen(?![^>]*<main\b)", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-011",
        name="Unstyled Native Checkbox/Radio",
        description="Raw type='checkbox' or type='radio' without custom styling or accessible switch component.",
        pattern=re.compile(r"<input\b[^>]*type=[\"'](?:checkbox|radio)[\"'](?![^>]*className=[\"'][^\"']*(?:sr-only|peer|appearance-none|accent-))", re.IGNORECASE),
        penalty=0.04,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-012",
        name="Unresponsive Static Table Container",
        description="Table element without horizontal scroll wrapper (overflow-x-auto), causing mobile viewport clipping.",
        pattern=re.compile(r"<table\b(?![^>]*\b(?:overflow-x-auto|responsive-table)\b)", re.IGNORECASE),
        penalty=0.05,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-013",
        name="Monochromatic Overuse on Status Badges",
        description="Badges and tags all using the exact same generic badge color instead of semantic state differentiation.",
        pattern=re.compile(r"(?:<span[^>]*badge[^>]*bg-gray-100.*?){3,}", re.IGNORECASE | re.DOTALL),
        penalty=0.04,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-014",
        name="Magic Number Spacing In Inline Styles",
        description="Hardcoded pixel spacing in style attributes breaking design rhythm scale.",
        pattern=re.compile(r"style\s*=\s*\{\{\s*[^}]*(?:margin|padding)(?:Top|Bottom|Left|Right)?\s*:\s*['\"][1-9]\d*px['\"]", re.IGNORECASE),
        penalty=0.04,
    ),
    AntiPatternRule(
        rule_id="MECHANICAL-015",
        name="Missing Active Selection State on Navigation",
        description="Navigation links or sidebar items without distinct active/current state styling (aria-current or isActive).",
        pattern=re.compile(r"<nav\b(?![^>]*aria-label)(?![^>]*aria-current)", re.IGNORECASE),
        penalty=0.04,
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
    sector: str                         # "fintech", "creative_agency", "edtech_ai", etc.
    target_audience: str                # "enterprise", "consumer", "developer"
    mood_keywords: tuple[str, ...]      # "trustworthy", "playful", "premium"
    surface_mode: str = "earn"          # "earn" (dashboard/app), "persuade" (landing), "operate" (data), "read" (docs)
    layout_paradigm: str = "bento_asymmetric"  # "bento_asymmetric", "master_detail", "split_hero", "feed_stream"
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
    "edtech_ai": (
        "Cognitive Focus & Modern Clarity",
        ["Plus Jakarta Sans", "Newsreader", "JetBrains Mono"],
        "warm-focus",
        "balanced",
    ),
    "web3_crypto": (
        "Cyber Dark & Neon Accent",
        ["Space Grotesk", "Syne", "Geist Mono"],
        "dark-vibrant",
        "dense",
    ),
    "social_community": (
        "Expressive Social",
        ["Outfit", "Plus Jakarta Sans", "Bricolage Grotesque"],
        "vibrant",
        "generous",
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
    "focused": "warm-focus",
    "futuristic": "dark-vibrant",
}

# Layout paradigms for diverse, anti-mechanical spatial composition
_LAYOUT_PARADIGM_MAP: dict[str, tuple[str, list[str]]] = {
    "bento_asymmetric": (
        "Bento Grid Asymmetric (60% Focal Anchor + Variable Aspect Utility Tiles)",
        [
            "Primary Focal Anchor: Assign 60% grid area (col-span-8 on desktop) to the main interactive workspace.",
            "Secondary Auxiliary Tiles: Use variable aspect ratios (1x1 metric tiles, 2x1 quick tools) around the anchor.",
            "Avoid Monotonous Rows: Strictly forbid 3 or more identical cards side-by-side with uniform heights.",
            "Layered Elevation: Canvas (surface_primary) -> Cards (surface_secondary) -> Floating Toolbars (surface_elevated).",
        ],
    ),
    "master_detail": (
        "Master-Detail Split Workspace (Dock Navigation + Focused Inspector)",
        [
            "Persistent Navigation Rail: Left dock with distinct active states (aria-current='page', subtle background pill).",
            "Inspector / Action Canvas: Center-right high-density work area with breadcrumb context and quick actions.",
            "Inline Summary Bar: Bottom or header telemetry strip for rapid status assessment.",
            "Thumb-Friendly Mobile Drawer: Left dock folds into a bottom-sheet on viewports under 768px.",
        ],
    ),
    "split_hero": (
        "Split-Hero Editorial Canvas (Narrative Statement Left + Live Preview Right)",
        [
            "Editorial Statement: High-contrast display typography with eyebrow badge and primary CTA.",
            "Live Interactive Canvas: Right-side interactive component with real mock data and tactile controls.",
            "Asymmetric Tension: Avoid centered hero text; left-align content with structured breathing room.",
        ],
    ),
    "feed_stream": (
        "Structured Stream (Vertical Visual Anchor + Sticky Chrono Markers)",
        [
            "Continuous Visual Track: Subtle vertical line guide linking events or items in chronological sequence.",
            "Variable Density Cards: Important milestone cards expand with preview; routine logs remain compact chips.",
            "Sticky Group Headers: Month/category headers stick during scroll with backdrop blur.",
        ],
    ),
}

# Surface mode behavioral directives
_SURFACE_MODE_DIRECTIVES: dict[str, list[str]] = {
    "earn": [
        "Mode 'Earn' (High-Efficiency Workspace & Dashboard):",
        "- Maximize signal-to-noise ratio: clear data hierarchy, high text contrast, compact metadata.",
        "- Keyboard navigable: focus-visible:ring-2 on every interactive component.",
        "- Real-world edge cases: implement skeleton shimmer loaders and multi-line line-clamp with tooltip disclosure.",
        "- Tactile feedback: buttons must have active:scale-[0.98] micro-interaction.",
    ],
    "persuade": [
        "Mode 'Persuade' (Landing Page & Brand Narrative):",
        "- Expressive typographic scale (1.333 ratio) with distinct editorial headline font.",
        "- High-impact asymmetric hero with directional visual weight toward the primary CTA.",
        "- Ambient glow accents and multi-layered surface elevation to build brand prestige.",
    ],
    "operate": [
        "Mode 'Operate' (Data Grid & Admin Console):",
        "- Tabular data alignment: numeric values right-aligned with font-mono tabular-nums.",
        "- Responsive horizontal scroll container with sticky header and sticky first column.",
        "- Contextual status chips with distinct semantic colors (success, warning, info, neutral).",
    ],
    "read": [
        "Mode 'Read' (Documentation & Academic Curriculum):",
        "- Optimized reading measure: 60-70 characters per line (max-w-prose).",
        "- Warm academic typography with comfortable leading (leading-relaxed) and serif headlines.",
        "- Generous whitespace and subtle margin notes/callouts.",
    ],
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
        surface_mode: str = "earn",
        layout_paradigm: str | None = None,
    ) -> DesignContext:
        # Normalize sector
        sector_key = sector.lower().replace(" ", "_").replace("-", "_")
        if sector_key not in _SECTOR_ARCHETYPES:
            sector_key = "saas_b2b"  # safe default

        # Normalize surface_mode
        mode = surface_mode.lower()
        if mode not in ("earn", "persuade", "operate", "read"):
            mode = "earn"

        # Normalize layout_paradigm
        paradigm = (layout_paradigm or "").lower()
        if paradigm not in _LAYOUT_PARADIGM_MAP:
            if mode == "persuade":
                paradigm = "split_hero"
            elif mode == "operate":
                paradigm = "master_detail"
            elif mode == "read":
                paradigm = "feed_stream"
            else:
                paradigm = "bento_asymmetric"

        return DesignContext(
            project_description=description,
            sector=sector_key,
            target_audience=audience,
            mood_keywords=tuple(mood_keywords),
            surface_mode=mode,
            layout_paradigm=paradigm,
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
        "surface_primary": "#F2EDE4",   # warm linen canvas
        "surface_secondary": "#FFFCF7", # elevated warm card
        "surface_elevated": "#FFFFFF",  # modal / popover
        "text_primary": "#1A1714",      # deep espresso
        "text_secondary": "#5C5346",    # accessible muted warm
        "accent": "#C45C3B",            # burnt sienna
        "accent_secondary": "#D97706",  # amber badge
        "border": "#D4CBBC",
        "border_highlight": "rgba(196, 92, 59, 0.15)",
        "glow_ambient": "rgba(196, 92, 59, 0.08)",
    },
    "cool-neutral": {
        "surface_primary": "#F6F7F8",   # cool white-gray
        "surface_secondary": "#FFFFFF",
        "surface_elevated": "#FFFFFF",
        "text_primary": "#1C2128",      # deep navy-charcoal
        "text_secondary": "#57606A",
        "accent": "#2D6A4F",            # forest green
        "accent_secondary": "#0284C7",  # sky blue highlight
        "border": "#D0D7DE",
        "border_highlight": "rgba(45, 106, 79, 0.15)",
        "glow_ambient": "rgba(45, 106, 79, 0.06)",
    },
    "warm-muted": {
        "surface_primary": "#FAF7F2",   # warm cream
        "surface_secondary": "#FFFFFF",
        "surface_elevated": "#FFFFFF",
        "text_primary": "#2C2416",      # dark brown
        "text_secondary": "#635B4E",
        "accent": "#6B8F71",            # sage green
        "accent_secondary": "#D97706",
        "border": "#DDD5C8",
        "border_highlight": "rgba(107, 143, 113, 0.15)",
        "glow_ambient": "rgba(107, 143, 113, 0.06)",
    },
    "warm-rich": {
        "surface_primary": "#FBF8F3",   # ivory
        "surface_secondary": "#FFFFFF",
        "surface_elevated": "#FFFFFF",
        "text_primary": "#1C140E",      # near-black with warm undertone
        "text_secondary": "#594B42",
        "accent": "#9B2335",            # deep crimson
        "accent_secondary": "#B45309",
        "border": "#E8DECE",
        "border_highlight": "rgba(155, 35, 53, 0.15)",
        "glow_ambient": "rgba(155, 35, 53, 0.06)",
    },
    "high-contrast": {
        "surface_primary": "#0D0D0D",   # near-black
        "surface_secondary": "#1A1A1A",
        "surface_elevated": "#262626",
        "text_primary": "#F5F5F0",      # warm off-white
        "text_secondary": "#A3A3A3",
        "accent": "#FFD60A",            # electric yellow
        "accent_secondary": "#38BDF8",  # electric cyan
        "border": "#333333",
        "border_highlight": "rgba(255, 214, 10, 0.25)",
        "glow_ambient": "rgba(255, 214, 10, 0.12)",
    },
    "cool-clean": {
        "surface_primary": "#F8F9FA",
        "surface_secondary": "#FFFFFF",
        "surface_elevated": "#FFFFFF",
        "text_primary": "#212529",
        "text_secondary": "#6C757D",
        "accent": "#0F6FEC",
        "accent_secondary": "#10B981",  # emerald status
        "border": "#DEE2E6",
        "border_highlight": "rgba(15, 111, 236, 0.15)",
        "glow_ambient": "rgba(15, 111, 236, 0.06)",
    },
    "vibrant": {
        "surface_primary": "#FAFAF9",
        "surface_secondary": "#FFFFFF",
        "surface_elevated": "#FFFFFF",
        "text_primary": "#1A1A1A",
        "text_secondary": "#57534E",
        "accent": "#FF6B35",            # vivid orange
        "accent_secondary": "#8B5CF6",  # purple punch
        "border": "#E0E0E0",
        "border_highlight": "rgba(255, 107, 53, 0.18)",
        "glow_ambient": "rgba(255, 107, 53, 0.08)",
    },
    "cool-dark": {
        "surface_primary": "#0F1117",
        "surface_secondary": "#161B22",
        "surface_elevated": "#21262D",
        "text_primary": "#E6EDF3",
        "text_secondary": "#8B949E",
        "accent": "#58A6FF",
        "accent_secondary": "#3FB950",  # green
        "border": "#30363D",
        "border_highlight": "rgba(88, 166, 255, 0.2)",
        "glow_ambient": "rgba(88, 166, 255, 0.08)",
    },
    "dark-muted": {
        "surface_primary": "#141414",
        "surface_secondary": "#1E1E1E",
        "surface_elevated": "#282828",
        "text_primary": "#D4D4D4",
        "text_secondary": "#858585",
        "accent": "#4EC9B0",            # teal/mint
        "accent_secondary": "#CE9178",  # warm peach
        "border": "#2D2D2D",
        "border_highlight": "rgba(78, 201, 176, 0.2)",
        "glow_ambient": "rgba(78, 201, 176, 0.08)",
    },
    "warm-neutral": {
        "surface_primary": "#F9F6F1",
        "surface_secondary": "#FFFFFF",
        "surface_elevated": "#FFFFFF",
        "text_primary": "#231F1A",
        "text_secondary": "#6B6357",
        "accent": "#8B6F47",            # warm brown
        "accent_secondary": "#2563EB",  # cobalt
        "border": "#DDD5C5",
        "border_highlight": "rgba(139, 111, 71, 0.15)",
        "glow_ambient": "rgba(139, 111, 71, 0.06)",
    },
    "warm-focus": {
        "surface_primary": "#F7F5F0",   # warm cognitive paper
        "surface_secondary": "#FFFFFF", # crisp white card
        "surface_elevated": "#FFFFFF",  # elevated modal / dropdown
        "text_primary": "#1E2022",      # crisp dark slate
        "text_secondary": "#525866",    # accessible secondary
        "accent": "#4361EE",            # royal cognitive blue
        "accent_secondary": "#F59E0B",  # warm amber badge / progress
        "border": "#E2DDD5",            # subtle warm border
        "border_highlight": "rgba(67, 97, 238, 0.15)",
        "glow_ambient": "rgba(67, 97, 238, 0.08)",
    },
    "dark-vibrant": {
        "surface_primary": "#0A0B0E",   # deep void
        "surface_secondary": "#12141A",
        "surface_elevated": "#1C202B",
        "text_primary": "#F0F2F5",      # crisp white
        "text_secondary": "#8A94A6",
        "accent": "#00F5D4",            # electric cyan
        "accent_secondary": "#7B2CBF",  # deep neon purple
        "border": "#222631",
        "border_highlight": "rgba(0, 245, 212, 0.25)",
        "glow_ambient": "rgba(0, 245, 212, 0.12)",
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
    "Cognitive Focus & Modern Clarity": "8px",
    "Cyber Dark & Neon Accent": "2px",
    "Expressive Social": "14px",
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
    "Cognitive Focus & Modern Clarity": ("focused", "cubic-bezier(0.2, 0.8, 0.2, 1)", "200ms"),
    "Cyber Dark & Neon Accent": ("electric", "cubic-bezier(0.16, 1, 0.3, 1)", "130ms"),
    "Expressive Social": ("expressive", "cubic-bezier(0.34, 1.56, 0.64, 1)", "260ms"),
}


@dataclass(frozen=True, slots=True)
class DesignStyleProfile:
    """Structured design brief — DESIGN.md compatible output.

    Encapsulates all creative decisions needed for an agent to produce
    distinctive, non-generic, anti-mechanical UI implementations.
    """

    # Identity & Spatial Architecture
    visual_language: str
    design_rationale: str
    surface_mode: str
    layout_paradigm: str

    # Color & Ambient Lighting
    surface_primary: str           # Canvas background
    surface_secondary: str         # Primary card container
    surface_elevated: str          # Modals, floating dropdowns
    text_primary: str              # High-contrast readable copy
    text_secondary: str            # Accessible muted descriptions (WCAG AA)
    accent: str                    # Brand interactive CTA, focus rings
    accent_secondary: str          # Status chips, secondary highlights
    border_color: str              # Component edges and dividers
    border_highlight: str          # Subtle top-edge highlight
    glow_ambient: str              # Ambient atmospheric glow
    color_temperature: str

    # Typography
    heading_font: str
    body_font: str
    accent_font: str               # optional display/mono for special use

    # Shape & Rhythm
    border_radius: str             # "0px", "4px", "8px", "12px" etc.
    spacing_philosophy: str        # "generous", "dense", "editorial", "structured"

    # Motion & Tactile Feedback
    motion_personality: str        # "snappy", "fluid", "dramatic"
    transition_easing: str
    transition_duration_base: str

    # Governance & Directives
    anti_patterns_forbidden: tuple[str, ...] = ()
    layout_directives: tuple[str, ...] = ()
    surface_directives: tuple[str, ...] = ()

    def to_design_md(self, project_name: str = "Project") -> str:
        """Generate a DESIGN.md compatible document."""
        forbidden_list = "\n".join(f"- {p}" for p in self.anti_patterns_forbidden)
        layout_list = "\n".join(f"- {d}" for d in self.layout_directives) if self.layout_directives else "- Asymmetric Bento layout with clear visual focal anchor."
        surface_list = "\n".join(f"{d}" for d in self.surface_directives) if self.surface_directives else "- High signal-to-noise ratio, tactile feedback, keyboard accessibility."

        return f"""# Design System — {project_name}

## Visual Language & Spatial Composition
- **Visual Language:** {self.visual_language}
- **Surface Mode:** {self.surface_mode}
- **Layout Paradigm:** {self.layout_paradigm}

{self.design_rationale}

---

## Color Palette & Ambient Lighting

| Token | Value | Role & Usage |
|---|---|---|
| `surface.canvas` | `{self.surface_primary}` | Base application background |
| `surface.card` | `{self.surface_secondary}` | Primary cards, panels, containers |
| `surface.elevated` | `{self.surface_elevated}` | Modals, floating dropdowns, popovers |
| `text.primary` | `{self.text_primary}` | Primary high-contrast body & titles |
| `text.secondary` | `{self.text_secondary}` | Accessible muted subtitles (WCAG AA) |
| `accent.primary` | `{self.accent}` | Brand CTAs, active focus outlines |
| `accent.secondary` | `{self.accent_secondary}` | Status highlights, badges, accents |
| `border.subtle` | `{self.border_color}` | Component dividers and card edges |
| `border.highlight` | `{self.border_highlight}` | Translucent top-edge ambient highlight |
| `glow.ambient` | `{self.glow_ambient}` | Subtle ambient lighting behind hero/cards |

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

## Motion & Tactile Feedback

- Personality: **{self.motion_personality}**
- Easing: `{self.transition_easing}`
- Base duration: `{self.transition_duration_base}`
- Tactile Click: `active:scale-[0.98]` micro-interaction on interactive components

---

## Layout & Anti-Mechanical Directives

{layout_list}

{surface_list}

---

## Forbidden Patterns (DO NOT USE)

{forbidden_list}
"""

    def to_llm_design_directive(self) -> str:
        """Generate an authoritative prompt directive for the LLM coding agent.

        This string should be prepended to any UI implementation prompt to
        force creative, non-generic, anti-mechanical design choices.
        """
        forbidden = "\n".join(f"  - {p}" for p in self.anti_patterns_forbidden)
        layout_rules = "\n".join(f"  - {d}" for d in self.layout_directives) if self.layout_directives else "  - Asymmetric Bento grid with 60% focal anchor."
        surface_rules = "\n".join(f"  - {d}" for d in self.surface_directives) if self.surface_directives else "  - High signal-to-noise ratio, tactile click physics."

        return f"""DESIGN DIRECTIVE (MANDATORY — do not deviate from these specifications):

Visual Language: {self.visual_language}
Surface Mode: {self.surface_mode}
Layout Paradigm: {self.layout_paradigm}
Rationale: {self.design_rationale}

COLORS & LIGHTING (use EXACTLY these values):
  Canvas base:        {self.surface_primary}
  Card surface:       {self.surface_secondary}
  Elevated surface:   {self.surface_elevated}
  Text primary:       {self.text_primary}
  Text secondary:     {self.text_secondary}
  Accent primary:     {self.accent}
  Accent secondary:   {self.accent_secondary}
  Border subtle:      {self.border_color}
  Border highlight:   {self.border_highlight}
  Ambient glow:       {self.glow_ambient}

TYPOGRAPHY:
  Headings:  {self.heading_font}
  Body:      {self.body_font}
  Accent:    {self.accent_font}

SHAPE & SPACING:
  Border radius: {self.border_radius} (uniform on components, NO rounded-full)
  Spacing rhythm: {self.spacing_philosophy} (4/8/16/24/32px scale)

MOTION & TACTILE FEEDBACK:
  Personality: {self.motion_personality}
  Easing: {self.transition_easing}
  Duration: {self.transition_duration_base}
  Tactile click: active:scale-[0.98] on all clickable items

LAYOUT ARCHITECTURE (DO NOT USE ROBOTIC 3-CARD GRIDS):
{layout_rules}

SURFACE BEHAVIOR:
{surface_rules}

FORBIDDEN — DO NOT USE THESE UNDER ANY CIRCUMSTANCES:
{forbidden}
"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_language": self.visual_language,
            "design_rationale": self.design_rationale,
            "surface_mode": self.surface_mode,
            "layout_paradigm": self.layout_paradigm,
            "surface_primary": self.surface_primary,
            "surface_secondary": self.surface_secondary,
            "surface_elevated": self.surface_elevated,
            "text_primary": self.text_primary,
            "text_secondary": self.text_secondary,
            "accent": self.accent,
            "accent_secondary": self.accent_secondary,
            "border_color": self.border_color,
            "border_highlight": self.border_highlight,
            "glow_ambient": self.glow_ambient,
            "color_temperature": self.color_temperature,
            "heading_font": self.heading_font,
            "body_font": self.body_font,
            "accent_font": self.accent_font,
            "border_radius": self.border_radius,
            "spacing_philosophy": self.spacing_philosophy,
            "motion_personality": self.motion_personality,
            "transition_easing": self.transition_easing,
            "transition_duration_base": self.transition_duration_base,
            "layout_directives": list(self.layout_directives),
            "surface_directives": list(self.surface_directives),
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
        surface_mode: str = "earn",
        layout_paradigm: str | None = None,
    ) -> DesignContext:
        return self._analyzer.analyze(
            description=description,
            sector=sector,
            audience=audience,
            mood_keywords=mood_keywords,
            existing_brand=existing_brand,
            framework_hint=framework_hint,
            surface_mode=surface_mode,
            layout_paradigm=layout_paradigm,
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
        layout_info = _LAYOUT_PARADIGM_MAP.get(
            context.layout_paradigm,
            _LAYOUT_PARADIGM_MAP["bento_asymmetric"],
        )
        surface_directives = tuple(
            _SURFACE_MODE_DIRECTIVES.get(
                context.surface_mode,
                _SURFACE_MODE_DIRECTIVES["earn"],
            )
        )
        layout_directives = tuple(layout_info[1])

        # Build rationale
        audience_desc = context.target_audience or "general users"
        mood_desc = ", ".join(context.mood_keywords) if context.mood_keywords else "balanced"
        rationale = (
            f"Designed for {audience_desc} in the {context.sector.replace('_', ' ')} space. "
            f"Surface mode: {context.surface_mode.upper()} with {layout_info[0]} spatial architecture. "
            f"Mood direction: {mood_desc}. "
            f"Visual language '{visual_language}' was selected to signal "
            f"opinionated taste, high usability, and anti-mechanical craftsmanship."
        )

        # Build forbidden patterns list from anti-pattern rules
        forbidden = [
            f"[{r.rule_id}] {r.name}: {r.description}"
            for r in ANTI_PATTERN_RULES
        ]

        return DesignStyleProfile(
            visual_language=visual_language,
            design_rationale=rationale,
            surface_mode=context.surface_mode,
            layout_paradigm=context.layout_paradigm,
            surface_primary=palette["surface_primary"],
            surface_secondary=palette["surface_secondary"],
            surface_elevated=palette.get("surface_elevated", palette["surface_secondary"]),
            text_primary=palette["text_primary"],
            text_secondary=palette.get("text_secondary", palette["text_primary"]),
            accent=palette["accent"],
            accent_secondary=palette.get("accent_secondary", palette["accent"]),
            border_color=palette["border"],
            border_highlight=palette.get("border_highlight", "rgba(255,255,255,0.08)"),
            glow_ambient=palette.get("glow_ambient", "rgba(67,97,238,0.06)"),
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
            layout_directives=layout_directives,
            surface_directives=surface_directives,
        )

    def score_originality(self, code: str) -> OriginalityScore:
        """Score the originality of a UI code snippet."""
        return self._detector.score_originality(code)

    def detect_antipatterns(self, code: str) -> list[AntiPatternViolation]:
        """Detect anti-pattern violations in a UI code snippet."""
        return self._detector.detect(code)
