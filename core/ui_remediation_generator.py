"""UI Remediation Prompt Generator — Automated repair directives for generic AI UI code.

Combines DesignAntiPatternDetector audit findings with Sector Archetype DesignStyleProfile,
Surface Modes, and Design Dials to synthesize an authoritative, load-bearing Technical English
prompt for refactoring UI code away from AI-slop into distinctive, accessible, production-grade interfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from core.ui_design_style_engine import (
    AntiPatternViolation,
    DesignStyleProfile,
    UiDesignStyleEngine,
)

_RULE_FIX_TEMPLATES: dict[str, str] = {
    # 20 Generic Anti-pattern rules
    "GENERIC-001": "Replace generic #3B82F6 / blue-500 / indigo-500 with prescribed accent: {accent}.",
    "GENERIC-002": "Replace standalone Inter/Roboto with typography pairing: Headings: {heading_font}, Body: {body_font}.",
    "GENERIC-003": "Replace rounded-full / 9999px with uniform component border radius: {border_radius}.",
    "GENERIC-004": "Break symmetrical 3-column grid with asymmetric layout, Bento grid, or distinct card emphasis.",
    "GENERIC-005": "Replace generic box-shadow with subtle layered borders ({border_color}) and ambient surface elevation ({surface_secondary}).",
    "GENERIC-006": "Remove AI-signature blue-to-purple gradient; use solid surface colors or subtle monochromatic warmth.",
    "GENERIC-007": "Replace stark bg-white min-h-screen with surface_canvas: {surface_primary}.",
    "GENERIC-008": "Restructure hero section: asymmetric alignment, single high-intent CTA + secondary link, distinct hierarchy.",
    "GENERIC-009": "Vary icon weights and colors to match semantic state and hierarchy instead of uniform primary color.",
    "GENERIC-010": "Replace transition-all 200ms ease-in-out with duration: {transition_duration_base}, easing: {transition_easing}.",
    "GENERIC-011": "Replace clinical bg-gray-50/100 with intentional canvas: {surface_primary}.",
    "GENERIC-012": "Replace #000000 on #FFFFFF with high-clarity softened text: {text_primary} on {surface_primary}.",
    "GENERIC-013": "Ensure all text meets WCAG AA 4.5:1 contrast; avoid text-gray-400 on light backgrounds; use {text_primary} or {text_secondary}.",
    "GENERIC-014": "Enlarge buttons and interactive targets to minimum 44px height (min-h-[44px] or py-2.5 px-4) for mobile touch compliance.",
    "GENERIC-015": "Remove redundant overflow-y-auto on inner child containers; maintain single unified scroll container.",
    "GENERIC-016": "Replace generic placeholder URLs with styled SVG initials or branded avatar component.",
    "GENERIC-017": "Convert <div onClick> to semantic <button> or add role='button', tabIndex={{0}}, and onKeyDown handler.",
    "GENERIC-018": "Replace uniform border-gray-200 with subtle border ({border_color}) and layered surface elevation ({surface_secondary}).",
    "GENERIC-019": "Add backdrop-blur-sm or backdrop-blur-md to semi-transparent overlays.",
    "GENERIC-020": "Replace default blue spin ring with custom brand pulse, skeleton loader, or sleek accent spinner.",

    # 15 Anti-Mechanical rules
    "MECHANICAL-001": "Dismantle repetitive card clones. Convert identical 3-card rows into an asymmetric Bento layout (60% featured anchor workspace card with live preview + stacked auxiliary utility tiles).",
    "MECHANICAL-002": "Add tactile click feedback: apply active:scale-[0.98] or active:translate-y-0.5 and subtle active ring to all interactive buttons and actionable cards.",
    "MECHANICAL-003": "Prevent text overflow: add 'truncate' or 'line-clamp-2' with 'title={{item.title}}' tooltip disclosure on dynamic headings.",
    "MECHANICAL-004": "Replace generic spinners with geometric Skeleton Shimmer loaders (animate-pulse bg-slate-200/80 rounded) matching the exact dimensions of the content.",
    "MECHANICAL-005": "Replace raw 'No data' with a rich illustrated Empty State container, encouraging microcopy, and a clear primary action CTA.",
    "MECHANICAL-006": "Ensure visible keyboard focus: replace raw 'focus:outline-none' with 'focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-{accent}'.",
    "MECHANICAL-007": "Transform static number stat boxes into dynamic metric tiles with trend sparklines, percentage delta badges, and contextual comparison labels.",
    "MECHANICAL-008": "Upgrade generic inputs with styled focus rings ({accent}), icon prefixes, subtle borders ({border_color}), and smooth transition physics.",
    "MECHANICAL-009": "Normalize z-index hierarchy: use structured layers (z-base: 0, z-card: 10, z-sticky: 20, z-modal: 40, z-toast: 50) instead of arbitrary z-[9999].",
    "MECHANICAL-010": "Add semantic <main> landmark tag and accessible document structure for keyboard navigation and screen readers.",
    "MECHANICAL-011": "Replace unstyled browser checkboxes/radios with custom accessible controls (peer sr-only with styled visual indicator).",
    "MECHANICAL-012": "Wrap data table in a responsive horizontal scroll container ('overflow-x-auto') with sticky headers and shadow boundary hints.",
    "MECHANICAL-013": "Differentiate badge colors semantically: use green for success/completed, amber ({accent_secondary}) for pending/in-progress, red for alerts, slate for neutral.",
    "MECHANICAL-014": "Replace inline pixel style magic numbers with design system spacing scale (4/8/16/24/32px scale).",
    "MECHANICAL-015": "Add distinct active selection states: style active navigation links with 'aria-current=\"page\"' and subtle background tint / accent indicator.",
}

_DIAL_INSTRUCTIONS: dict[str, str] = {
    "diversify": (
        "PRIORITY DIAL [DIVERSIFY — Break Robotic Symmetry]:\n"
        "- Transform monotonous card rows into an asymmetric Bento Grid: assign 60% grid area to the primary focal workspace with rich live preview; stack secondary utility tools into variable aspect tiles.\n"
        "- Inject secondary accent tokens ({accent_secondary}) for badges, progress chips, and active highlights to eliminate single-hue AI monotony.\n"
        "- Create distinct visual hierarchy between canvas ({surface_primary}), base cards ({surface_secondary}), and floating elements ({surface_elevated})."
    ),
    "harden": (
        "PRIORITY DIAL [HARDEN — Real-World Edge Case Armor]:\n"
        "- Loading States: Replace raw spinners with geometric Skeleton Shimmer loaders matching exact component heights.\n"
        "- Empty States: Guard array maps with rich illustrated empty states, helpful instructions, and primary action CTAs.\n"
        "- Text Clamping: Add 'truncate' or 'line-clamp-2' with 'title={{...}}' attribute to prevent long Vietnamese strings from breaking card layouts.\n"
        "- Error Boundaries: Gracefully render inline alert containers when child components or APIs fail."
    ),
    "polish": (
        "PRIORITY DIAL [POLISH — Spatial Rhythm & Layered Borders]:\n"
        "- Enforce strict 4/8/16/24/32px spacing scale; remove arbitrary margin/padding values.\n"
        "- Implement dual-tone layered borders: subtle border ({border_color}) combined with ambient highlight ({border_highlight}).\n"
        "- Calibrate subtle ambient elevation shadows: avoid harsh black shadows; use diffused multi-stop ambient elevation."
    ),
    "bolder": (
        "PRIORITY DIAL [BOLDER — High-Impact Typographic Scale]:\n"
        "- Elevate typographic scale ratio to 1.333 (Perfect Fourth); use display heading font ({heading_font}) for key titles.\n"
        "- Maximize contrast ratio between text primary ({text_primary}) and card surface ({surface_secondary}).\n"
        "- Punch up callouts with bold eyebrow badges (uppercase tracking-wider font-mono text-xs)."
    ),
    "quieter": (
        "PRIORITY DIAL [QUIETER — Serene Minimalist Focus]:\n"
        "- Soften visual contrast; reduce loud borders in favor of delicate surface tone shifts.\n"
        "- Remove unnecessary decorative icons and borders; let typography and whitespace breathe.\n"
        "- Keep interactive accents understated and focused strictly on the primary action."
    ),
}


@dataclass(frozen=True, slots=True)
class UiRemediationResult:
    """Structured result of a UI remediation analysis."""

    originality_score: float
    verdict: str
    violations_count: int
    violations: tuple[AntiPatternViolation, ...]
    sector: str
    surface_mode: str
    layout_paradigm: str
    dial: str
    visual_language: str
    remediation_prompt: str
    prescribed_tokens: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "originality_score": self.originality_score,
            "verdict": self.verdict,
            "violations_count": self.violations_count,
            "violations": [v.to_dict() for v in self.violations],
            "sector": self.sector,
            "surface_mode": self.surface_mode,
            "layout_paradigm": self.layout_paradigm,
            "dial": self.dial,
            "visual_language": self.visual_language,
            "remediation_prompt": self.remediation_prompt,
            "prescribed_tokens": self.prescribed_tokens,
        }


class UiRemediationGenerator:
    """Generates structured, authoritative UI refactoring prompts."""

    def __init__(self, engine: UiDesignStyleEngine | None = None) -> None:
        self._engine = engine or UiDesignStyleEngine()

    def generate(
        self,
        code: str,
        sector: str = "edtech_ai",
        surface_mode: str = "earn",
        layout_paradigm: str | None = None,
        dial: str = "diversify",
        project_name: str = "Project",
        target_file: str | None = None,
        mood_keywords: Sequence[str] = (),
    ) -> UiRemediationResult:
        """Analyze code for anti-patterns and generate an actionable remediation prompt.

        Args:
            code: Source code of the component / UI view.
            sector: Sector archetype key (e.g. 'edtech_ai', 'saas_b2b', 'web3_crypto').
            surface_mode: Surface mode ('earn', 'persuade', 'operate', 'read').
            layout_paradigm: Optional spatial layout paradigm ('bento_asymmetric', 'master_detail', etc.).
            dial: Design dial ('diversify', 'harden', 'polish', 'bolder', 'quieter').
            project_name: Name of the project or module.
            target_file: Optional path or filename of the target component.
            mood_keywords: Optional keywords to influence color/vibe direction.

        Returns:
            UiRemediationResult with audit score, violations list, and synthesized prompt.
        """
        score = self._engine.score_originality(code)
        context = self._engine.analyze_context(
            description=f"{project_name} UI component",
            sector=sector,
            mood_keywords=mood_keywords,
            surface_mode=surface_mode,
            layout_paradigm=layout_paradigm,
        )
        profile = self._engine.generate_style_profile(context)

        tokens: dict[str, str] = {
            "surface_primary": profile.surface_primary,
            "surface_secondary": profile.surface_secondary,
            "surface_elevated": profile.surface_elevated,
            "text_primary": profile.text_primary,
            "text_secondary": profile.text_secondary,
            "accent": profile.accent,
            "accent_secondary": profile.accent_secondary,
            "border_color": profile.border_color,
            "border_highlight": profile.border_highlight,
            "glow_ambient": profile.glow_ambient,
            "heading_font": profile.heading_font,
            "body_font": profile.body_font,
            "accent_font": profile.accent_font,
            "border_radius": profile.border_radius,
            "spacing_philosophy": profile.spacing_philosophy,
            "motion_personality": profile.motion_personality,
            "transition_easing": profile.transition_easing,
            "transition_duration_base": profile.transition_duration_base,
        }

        dial_normalized = dial.lower() if dial else "diversify"
        if dial_normalized not in _DIAL_INSTRUCTIONS:
            dial_normalized = "diversify"

        prompt = self._build_prompt(
            code=code,
            score=score.score,
            verdict=score.verdict,
            violations=score.violations,
            profile=profile,
            tokens=tokens,
            dial=dial_normalized,
            project_name=project_name,
            target_file=target_file,
        )

        return UiRemediationResult(
            originality_score=score.score,
            verdict=score.verdict,
            violations_count=len(score.violations),
            violations=score.violations,
            sector=context.sector,
            surface_mode=context.surface_mode,
            layout_paradigm=context.layout_paradigm,
            dial=dial_normalized,
            visual_language=profile.visual_language,
            remediation_prompt=prompt,
            prescribed_tokens=tokens,
        )

    def _build_prompt(
        self,
        code: str,
        score: float,
        verdict: str,
        violations: Sequence[AntiPatternViolation],
        profile: DesignStyleProfile,
        tokens: dict[str, str],
        dial: str,
        project_name: str,
        target_file: str | None,
    ) -> str:
        lines: list[str] = []
        target_desc = f"File: {target_file}" if target_file else f"Project: {project_name}"
        lines.append(f"# UI REMEDIATION SPECIFICATION — {target_desc.upper()}")
        lines.append("")
        lines.append("## 1. System Role & Mission")
        lines.append(
            "You are a Principal UI/UX Staff Engineer and Design Systems Architect. "
            "Refactor the provided component to eliminate robotic AI aesthetic signatures ('AI slop'), "
            "break symmetrical card clones, enforce real-world living states (skeletons, empty states, line-clamp), "
            "and implement the authoritative design system tokens below with strict WCAG AA compliance."
        )
        lines.append("")
        lines.append("## 2. Spatial Composition & Surface Architecture")
        lines.append(f"- **Visual Language:** {profile.visual_language}")
        lines.append(f"- **Surface Mode:** `{profile.surface_mode.upper()}`")
        lines.append(f"- **Layout Paradigm:** `{profile.layout_paradigm}`")
        lines.append(f"- **Active Dial:** `{dial.upper()}`")
        lines.append(f"- **Design Rationale:** {profile.design_rationale}")
        lines.append("")

        # Insert dial instructions
        dial_text = _DIAL_INSTRUCTIONS.get(dial, _DIAL_INSTRUCTIONS["diversify"]).format(**tokens)
        lines.append("### Active Dial Directives")
        lines.append(dial_text)
        lines.append("")

        lines.append("### Prescribed Design Tokens & Lighting")
        lines.append("| Token Name | Prescribed Value | Architectural Role |")
        lines.append("|---|---|---|")
        lines.append(f"| `surface.canvas` | `{tokens['surface_primary']}` | Application base background |")
        lines.append(f"| `surface.card` | `{tokens['surface_secondary']}` | Primary interactive cards and panels |")
        lines.append(f"| `surface.elevated` | `{tokens['surface_elevated']}` | Modals, popovers, floating toolbars |")
        lines.append(f"| `text.primary` | `{tokens['text_primary']}` | High-contrast readable typography |")
        lines.append(f"| `text.secondary` | `{tokens['text_secondary']}` | Accessible muted subtitles (WCAG AA >= 4.5:1) |")
        lines.append(f"| `accent.primary` | `{tokens['accent']}` | Primary brand CTA, active focus rings |")
        lines.append(f"| `accent.secondary` | `{tokens['accent_secondary']}` | Contextual badges, status highlights |")
        lines.append(f"| `border.subtle` | `{tokens['border_color']}` | Subtle card edges and section dividers |")
        lines.append(f"| `border.highlight` | `{tokens['border_highlight']}` | Translucent top-edge ambient highlight |")
        lines.append(f"| `glow.ambient` | `{tokens['glow_ambient']}` | Atmospheric ambient lighting |")
        lines.append(f"| `font.heading` | `{tokens['heading_font']}` | Section headlines and titles |")
        lines.append(f"| `font.body` | `{tokens['body_font']}` | Readable body copy, labels |")
        lines.append(f"| `font.accent` | `{tokens['accent_font']}` | Code, metrics, tabular numbers |")
        lines.append(f"| `border.radius` | `{tokens['border_radius']}` | Uniform component radius (no rounded-full) |")
        lines.append(f"| `spacing` | `{tokens['spacing_philosophy']}` | 4/8/16/24/32px scale rhythm |")
        lines.append(f"| `motion.transition` | `{tokens['transition_duration_base']} {tokens['transition_easing']}` | Micro-interaction motion |")
        lines.append("")

        lines.append("## 3. Audit Findings & Violations to Fix")
        lines.append(f"- **Originality Score:** {score:.2f} / 1.00 (Verdict: `{verdict}`)")
        lines.append(f"- **Violations Count:** {len(violations)}")
        lines.append("")

        if violations:
            lines.append("### Detected Anti-Pattern & Mechanical Violations")
            for idx, v in enumerate(violations, 1):
                fix_template = _RULE_FIX_TEMPLATES.get(
                    v.rule_id,
                    f"Refactor to comply with {profile.visual_language} standards.",
                )
                directive = fix_template.format(**tokens)
                snippet_preview = v.match_snippet.strip().replace("\n", " ")
                if len(snippet_preview) > 80:
                    snippet_preview = snippet_preview[:77] + "..."
                lines.append(f"{idx}. **[{v.rule_id}] {v.rule_name}**")
                lines.append(f"   - Match snippet: `{snippet_preview}`")
                lines.append(f"   - Required action: {directive}")
        else:
            lines.append("No automated anti-pattern violations detected. Elevate component with the active dial and tokens above.")

        lines.append("")
        lines.append("## 4. Mandatory Anti-Mechanical Engineering Directives")
        lines.append("1. **Tactile Interaction Physics:**")
        lines.append("   - All buttons and clickable elements MUST have `active:scale-[0.98]` or `active:translate-y-0.5`.")
        lines.append("   - Replace raw `focus:outline-none` with visible `focus-visible:ring-2 focus-visible:ring-offset-2`.")
        lines.append("2. **Real-World Living States (No Blank Screens):**")
        lines.append("   - Implement geometric Skeleton Shimmer loaders (`animate-pulse bg-slate-200/80 rounded`) for loading states.")
        lines.append("   - Implement contextual Empty States with clear guidance and an actionable CTA button.")
        lines.append("   - Enforce `truncate` or `line-clamp-2` with `title={...}` attribute on dynamic text.")
        lines.append("3. **Content-Driven Layouts (No Dummy AI Clones):**")
        lines.append("   - Design around the real domain content and terminology.")
        lines.append("   - Break uniform 3-card grids into asymmetric Bento blocks with 60% focal anchor cards.")
        lines.append("4. **Functional Preservation:**")
        lines.append("   - Preserve all existing props, React hooks, callbacks, and event handlers.")
        lines.append("   - Do not remove business logic or third-party library imports.")
        lines.append("")
        lines.append("## 5. Output Requirements")
        lines.append("Return the complete, refactored, production-ready code with no conversational filler.")

        return "\n".join(lines)
