# Design System — High-end fashion editorial sto

## Visual Language & Spatial Composition
- **Visual Language:** Editorial Avant-garde
- **Surface Mode:** earn
- **Layout Paradigm:** bento_asymmetric

Designed for general in the creative agency space. Surface mode: EARN with Bento Grid Asymmetric (60% Focal Anchor + Variable Aspect Utility Tiles) spatial architecture. Mood direction: balanced. Visual language 'Editorial Avant-garde' was selected to signal opinionated taste, high usability, and anti-mechanical craftsmanship.

---

## Color Palette & Ambient Lighting

| Token | Value | Role & Usage |
|---|---|---|
| `surface.canvas` | `#F2EDE4` | Base application background |
| `surface.card` | `#FFFCF7` | Primary cards, panels, containers |
| `surface.elevated` | `#FFFFFF` | Modals, floating dropdowns, popovers |
| `text.primary` | `#1A1714` | Primary high-contrast body & titles |
| `text.secondary` | `#5C5346` | Accessible muted subtitles (WCAG AA) |
| `accent.primary` | `#C45C3B` | Brand CTAs, active focus outlines |
| `accent.secondary` | `#D97706` | Status highlights, badges, accents |
| `border.subtle` | `#D4CBBC` | Component dividers and card edges |
| `border.highlight` | `rgba(196, 92, 59, 0.15)` | Translucent top-edge ambient highlight |
| `glow.ambient` | `rgba(196, 92, 59, 0.08)` | Subtle ambient lighting behind hero/cards |

Color temperature: **warm-contrast**

---

## Typography

- **Headings**: Syne
- **Body**: Cormorant Garamond
- **Accent / Mono**: Playfair Display

---

## Shape & Spacing

- Border radius: **0px** (apply consistently, do not mix with rounded-full)
- Spacing philosophy: **editorial**

---

## Motion & Tactile Feedback

- Personality: **dramatic**
- Easing: `cubic-bezier(0.76, 0, 0.24, 1)`
- Base duration: `350ms`
- Tactile Click: `active:scale-[0.98]` micro-interaction on interactive components

---

## Layout & Anti-Mechanical Directives

- Primary Focal Anchor: Assign 60% grid area (col-span-8 on desktop) to the main interactive workspace.
- Secondary Auxiliary Tiles: Use variable aspect ratios (1x1 metric tiles, 2x1 quick tools) around the anchor.
- Avoid Monotonous Rows: Strictly forbid 3 or more identical cards side-by-side with uniform heights.
- Layered Elevation: Canvas (surface_primary) -> Cards (surface_secondary) -> Floating Toolbars (surface_elevated).

Mode 'Earn' (High-Efficiency Workspace & Dashboard):
- Maximize signal-to-noise ratio: clear data hierarchy, high text contrast, compact metadata.
- Keyboard navigable: focus-visible:ring-2 on every interactive component.
- Real-world edge cases: implement skeleton shimmer loaders and multi-line line-clamp with tooltip disclosure.
- Tactile feedback: buttons must have active:scale-[0.98] micro-interaction.

---

## Forbidden Patterns (DO NOT USE)

- [GENERIC-001] Generic AI Primary Blue: Blue #3B82F6 or indigo #6366F1 as primary — the default AI color choice.
- [GENERIC-002] Overused Default Fonts: Inter, Roboto, or DM Sans used as the sole typeface without distinctive pairing.
- [GENERIC-003] Universal Pill / Full-Round Radius: border-radius: 9999px or rounded-full applied uniformly on everything.
- [GENERIC-004] Soulless 3-Column Card Grid: Uniform 3-column card grid without visual hierarchy — the default AI layout.
- [GENERIC-005] AI Default Shadow Formula: box-shadow: 0 2px 4px rgba(0,0,0,0.1) — the most generic elevation style.
- [GENERIC-006] Blue-to-Purple Gradient Cliché: Gradient from blue/indigo to purple/violet — the AI aesthetic signature.
- [GENERIC-007] Blank White Canvas Default: bg-white min-h-screen as the entire page background — zero personality.
- [GENERIC-008] Centered Hero with 2 CTAs: text-center hero section with huge h1 + subtitle + exactly 2 CTA buttons — the AI template.
- [GENERIC-009] Lucide/Heroicons Uniform Primary Color: Icons all rendered in stroke-current or text-primary — no visual depth.
- [GENERIC-010] Default AI Hover Transition: transition-all duration-200 ease-in-out — exact generic hover transition.
- [GENERIC-011] Gray-50 / Gray-100 Background: bg-gray-50 or bg-gray-100 as page background — clinical, no warmth.
- [GENERIC-012] Pure Black Text on White: #000000 or #111111 on #ffffff — no color story, no warmth.
- [GENERIC-013] Low-Contrast Muted Text: text-gray-400 or text-slate-400 used for content text on light background, violating WCAG AA 4.5:1.
- [GENERIC-014] Sub-40px Touch Target: Button or interactive element with height under 40px (h-6, h-7, py-0.5, py-1) violating mobile accessibility.
- [GENERIC-015] Nested Scrollbar Syndrome: Inner container with overflow-y-auto nested inside another scroll container causing dual scrollbars.
- [GENERIC-016] Generic AI Placeholder Avatar: via.placeholder.com, placeholder-avatar, or generic unstyled initials circle.
- [GENERIC-017] Inaccessible Div Click Trap: <div onClick=...> without role='button' or tabIndex, breaking keyboard and screen reader access.
- [GENERIC-018] Monolithic Unlayered Border: border border-gray-200 applied uniformly without visual elevation or hierarchy.
- [GENERIC-019] Backdrop Without Blur: Modal or drawer backdrop with bg-black/20 or bg-white/90 without backdrop-blur.
- [GENERIC-020] Generic AI Loading Spinner: animate-spin rounded-full border-b-2 border-blue-500 — the exact default AI spinner.
- [MECHANICAL-001] Repetitive Card Clone Syndrome: Uniform row or grid of identical cards without visual hierarchy or featured focal anchor.
- [MECHANICAL-002] Lack of Tactile Click Feedback: Buttons or clickable items without active state (active:scale-[0.98] or active:translate-y).
- [MECHANICAL-003] Unhandled Text Overflow: Dynamic variable in heading or card title without truncate or line-clamp, risking layout breakage.
- [MECHANICAL-004] Missing Skeleton Shimmer Loader: Raw spinner used for content area instead of structured skeleton loader matching content geometry.
- [MECHANICAL-005] Lazy Raw Empty State: Raw text 'No items' or 'No data' rendered without an illustrated container, helpful guidance, or action CTA.
- [MECHANICAL-006] Removed Focus Outline Without Ring Replacement: focus:outline-none used without providing a visible focus-visible:ring-2 replacement for keyboard a11y.
- [MECHANICAL-007] Stat Box Row Cliché: Uniform row of 3-4 stat boxes with just huge text and tiny bottom label without sparkline or delta badge.
- [MECHANICAL-008] Unstyled Generic Input Form: Input without transition, focus ring, or icon prefix, feeling like an unstyled browser form.
- [MECHANICAL-009] Unstructured Arbitrary Z-Index: Arbitrary z-[9999] or uncoordinated high z-index instead of structured token layers.
- [MECHANICAL-010] Missing Main Landmark or Skip Link: Full page view without semantic <main> landmark tag, hurting screen reader accessibility.
- [MECHANICAL-011] Unstyled Native Checkbox/Radio: Raw type='checkbox' or type='radio' without custom styling or accessible switch component.
- [MECHANICAL-012] Unresponsive Static Table Container: Table element without horizontal scroll wrapper (overflow-x-auto), causing mobile viewport clipping.
- [MECHANICAL-013] Monochromatic Overuse on Status Badges: Badges and tags all using the exact same generic badge color instead of semantic state differentiation.
- [MECHANICAL-014] Magic Number Spacing In Inline Styles: Hardcoded pixel spacing in style attributes breaking design rhythm scale.
- [MECHANICAL-015] Missing Active Selection State on Navigation: Navigation links or sidebar items without distinct active/current state styling (aria-current or isActive).
