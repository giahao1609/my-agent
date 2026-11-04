---
name: my-ui
description: Creative UI/UX specialist, anti-AI-slop design enforcement, persistent DESIGN contract governance, Sector Archetypes, living states, and Playwright a11y verification.
---

# UI Specialist & Anti-Mechanical Design System Workflow

Activate the **UI Coder Agent (`AgentRole.UI_CODER`)** to design and implement bespoke, diverse, and living interfaces that eliminate formulaic "AI slop" and robotic, mechanical UI patterns while strictly respecting repository boundary policies.

## 1. Core Principles: Anti-Mechanical Taste & Living Interfaces

AI-generated interfaces frequently collapse into predictable, robotic clichés: symmetrical rows of identical cards, clinical pure-white or gray canvases, missing tactile button feedback, unhandled text overflow breaking layouts, raw spinners instead of geometric skeleton shimmers, and lazy "No data" strings instead of illustrated empty states.

MyAgent integrates lessons from top design systems (`ui-ux-pro-max-skill`, `taste-skill`, and `impeccable`) to enforce production-grade, human-crafted design taste:
- **Surface Modes (`earn`, `persuade`, `operate`, `read`)**: Tailors visual density and interaction patterns to the user's intent.
- **Spatial Layout Paradigms**: Dismantles monotonous 3-column card grids in favor of asymmetric bento layouts, master-detail docks, split heroes, and feed streams.
- **Living States & Tactile Physics**: Demands full lifecycle states (loading skeletons matching component geometry, rich illustrated empty states, `active:scale-[0.98]` tactile press physics, and explicit keyboard focus rings).
- **Enriched Color System**: Dual-tone borders (`border_highlight`), elevated surfaces (`surface_elevated`), WCAG-compliant secondary text (`text_secondary`), status accent highlights (`accent_secondary`), and ambient glow tokens (`glow_ambient`).
- **Operational Design Dials**: Fine-tunes aesthetic direction via `diversify`, `harden`, `polish`, `bolder`, or `quieter`.
- **35 Anti-Pattern & Mechanical Rules**: Deterministically detects visual cliches, accessibility traps, and formulaic layout repetition.
- **Zero-Pollution Policy**: Design contracts and internal metadata are stored exclusively inside MyAgent's internal data store (`data/designs/`), never polluting the user's project workspace.

---

## 2. Four Surface Modes & Spatial Layout Paradigms

| Surface Mode | Primary Intent | Default Layout Paradigm | Design Characteristics |
|---|---|---|---|
| `earn` | High-efficiency workspace, app dashboards, tutoring consoles | `bento_asymmetric` | 60% focal anchor card + variable utility tiles; high density; subtle translucent borders. |
| `persuade` | Landing pages, feature showcases, conversion funnels | `split_hero` | High-impact typography, editorial copy left + interactive live canvas right; atmospheric glow. |
| `operate` | Data grids, query consoles, dense management tools | `master_detail` | Fixed dock navigation + responsive inspector pane; 100% viewport management; zero dead space. |
| `read` | Documentation, blog articles, curriculum lesson viewers | `feed_stream` | Continuous visual guideline + sticky chapter headers; optimal 65ch line length; serif typography. |

### Spatial Layout Paradigms
1. **`bento_asymmetric`**: Replaces robotic symmetrical card grids with an asymmetric hierarchy. A prominent focal anchor tile commands 60% of the visual weight, supported by contextual utility tiles.
2. **`master_detail`**: Side-by-side or docked master list paired with a dynamic detail/inspector panel, ideal for dense exploration.
3. **`split_hero`**: Asymmetric split viewport pairing authoritative editorial copy on the left with an active interactive component or preview canvas on the right.
4. **`feed_stream`**: Vertical narrative stream connected by a continuous visual guide line, utilizing sticky date/section milestones.

---

## 3. Directory of 13 Sector Archetypes

| Sector | Visual Language | Recommended Typography Pairing | Color Temperature | Spacing |
|---|---|---|---|---|
| `fintech` | Architectural Minimalism | Public Sans, Geist, IBM Plex Sans | cool-neutral | balanced |
| `creative_agency` | Editorial Avant-garde | Syne, Cormorant Garamond, Playfair Display | warm-contrast | editorial |
| `health_wellness` | Organic Minimalism | DM Serif Display, Nunito, Lora | warm-muted | generous |
| `saas_b2b` | Structured Clarity | Plus Jakarta Sans, Inter, JetBrains Mono | cool-clean | balanced |
| `portfolio` | Neo-Brutalism | Space Grotesk, Syne, Archivo | high-contrast | dense |
| `ecommerce` | Tactile Editorial | Playfair Display, Outfit, Cormorant | warm-rich | generous |
| `developer_tool` | Monochrome Technical | JetBrains Mono, Fira Code, Geist Mono | cool-dark | dense |
| `startup_consumer` | Kinetic Playful | Bricolage Grotesque, Plus Jakarta Sans | vibrant | generous |
| `education` | Warm Academic | Newsreader, Source Serif 4, Lora | warm-neutral | balanced |
| `data_analytics` | Precision Dashboard | Geist, Space Grotesk, IBM Plex Mono | cool-dark | dense |
| `edtech_ai` | Cognitive Focus & Modern Clarity | Plus Jakarta Sans, Newsreader, JetBrains Mono | warm-focus | balanced |
| `web3_crypto` | Cyber Dark & Neon Accent | Space Grotesk, Syne, Geist Mono | dark-vibrant | dense |
| `social_community` | Expressive Social | Outfit, Plus Jakarta Sans, Bricolage Grotesque | vibrant | generous |

---

## 4. Five Operational Design Dials

When refining interfaces via `generate_ui_remediation_prompt`, apply an operational dial to guide the refactoring focus:

- **`diversify` (Anti-Mechanical Focus)**: Dismantles repetitive 3-column card rows into an Asymmetric Bento Grid. Breaks monotony by promoting one card to a 60% featured focal anchor and varying content density across secondary tiles.
- **`harden` (Living States & Robustness Focus)**: Mandates robust living states: geometry-matching skeleton shimmer loaders during fetch, illustrated empty states with action CTAs, text overflow constraints (`truncate`, `line-clamp-2`), and mobile-responsive scroll wrappers (`overflow-x-auto`).
- **`polish` (Micro-Craft Focus)**: Refines tactile interactions: `active:scale-[0.98]`, dual-layer translucent borders (`border_highlight`), subtle backdrop blurs (`backdrop-blur-md`), and tuned transition curves (`transition-all duration-200 ease-out`).
- **`bolder` (High-Energy Contrast Focus)**: Sharpens visual hierarchy: scales typography ratio up to 1.333, activates distinctive display fonts, applies punchy accent highlights, and deepens ambient background contrast.
- **`quieter` (Minimalist Calm Focus)**: Reduces visual clutter: mutes bright accents, increases whitespace padding, eliminates unnecessary decorative borders, and centers attention purely on domain content.

---

## 5. Directory of 35 Anti-Pattern & Mechanical Rules

### Generic AI Slop & Accessibility Baseline (`GENERIC-001` - `GENERIC-020`)
- `GENERIC-001`: Generic AI Primary Blue (`#3B82F6` / `indigo-500`)
- `GENERIC-002`: Overused Default Fonts (standalone Inter / Roboto)
- `GENERIC-003`: Universal Pill / Full-Round Radius (`rounded-full` on cards)
- `GENERIC-004`: Soulless 3-Column Card Grid
- `GENERIC-005`: AI Default Shadow Formula (`shadow-lg` without layered borders)
- `GENERIC-006`: Blue-to-Purple Gradient Cliche
- `GENERIC-007`: Blank White Canvas Default (`bg-white` / `bg-black` without tinted surface)
- `GENERIC-008`: Centered Hero with 2 CTAs
- `GENERIC-009`: Lucide/Heroicons Uniform Primary Color
- `GENERIC-010`: Default AI Hover Transition (`200ms ease-in-out` without tactile transform)
- `GENERIC-011`: Gray-50 / Gray-100 Clinical Background
- `GENERIC-012`: Pure Black Text on Stark White
- `GENERIC-013`: Low-Contrast Muted Text (violating WCAG AA 4.5:1)
- `GENERIC-014`: Sub-40px Touch Target (violating mobile a11y)
- `GENERIC-015`: Nested Scrollbar Syndrome (`overflow-y-auto` traps)
- `GENERIC-016`: Generic AI Placeholder Avatar (`via.placeholder.com`)
- `GENERIC-017`: Inaccessible Div Click Trap (`<div onClick>` without role/tabIndex)
- `GENERIC-018`: Monolithic Unlayered Border (`border border-gray-200` without depth)
- `GENERIC-019`: Backdrop Without Blur (`fixed inset-0 bg-black/50` without backdrop-blur)
- `GENERIC-020`: Generic AI Loading Spinner (circular spinning border instead of skeleton)

### Anti-Mechanical & Living States (`MECHANICAL-001` - `MECHANICAL-015`)
- `MECHANICAL-001`: Repetitive Card Clone Syndrome (3+ identical rounded border cards)
- `MECHANICAL-002`: Lack of Tactile Click Feedback (buttons without `active:scale-[0.98]`)
- `MECHANICAL-003`: Unhandled Text Overflow (headings/cards without `truncate` or `line-clamp`)
- `MECHANICAL-004`: Missing Skeleton Shimmer Loader (raw spinner for content loading)
- `MECHANICAL-005`: Lazy Raw Empty State (unformatted "No data" string without CTA)
- `MECHANICAL-006`: Removed Focus Outline Without Ring (`focus:outline-none` missing `focus-visible:ring-2`)
- `MECHANICAL-007`: Stat Box Row Cliché (3+ large numbers with small muted labels)
- `MECHANICAL-008`: Unstyled Generic Input Form (inputs missing focus ring or token border)
- `MECHANICAL-009`: Unstructured Arbitrary Z-Index (`z-[9999]` instead of semantic z-index)
- `MECHANICAL-010`: Missing Main Landmark or Skip Link (full page without `<main>`)
- `MECHANICAL-011`: Unstyled Native Checkbox/Radio (raw native control without custom styling)
- `MECHANICAL-012`: Unresponsive Static Table Container (`<table` without `overflow-x-auto` wrapper)
- `MECHANICAL-013`: Monochromatic Overuse on Status Badges (all badges using generic gray)
- `MECHANICAL-014`: Magic Number Spacing In Inline Styles (`margin: 37px` breaking spacing rhythm)
- `MECHANICAL-015`: Missing Active Selection State on Navigation (`<nav>` without active indicator)

---

## 6. Execution Protocol

### Step 1: Initialize / Retrieve Design Contract (Zero-Pollution)
1. Check whether MyAgent already has a design profile for this project in internal storage (`data/designs/<project>.design.md`).
2. If absent:
   - Call the MCP tool `generate_ui_design_profile(project_description, sector, audience, mood_keywords, surface_mode, layout_paradigm, workspace_path)` to persist the contract inside MyAgent's data store (`data/designs/`).
   - **Zero-Pollution Policy**: DO NOT write loose `DESIGN.md` files into the root of the user's project workspace. All design specifications remain isolated in MyAgent repository storage.
3. If present:
   - Load tokens from MyAgent's store: `surface_primary`, `surface_elevated`, `text_secondary`, `accent`, `accent_secondary`, `border_highlight`, `glow_ambient`, typography pairings, and spatial layout rules.

### Step 2: Component Catalog Discovery & Reuse
- Inspect the workspace to identify existing components (React, Vue, HTML, Vanilla CSS).
- Maximize reuse via `UiComponentCatalog`; avoid duplicating existing primitives.

### Step 3: Implement Interface with Mandatory Design Directives
- Apply exact design tokens from the design contract.
- Build around real domain content and living states:
  - Add geometry-matching skeleton shimmer placeholders for loading state.
  - Implement illustrated empty states with an action CTA.
  - Add `truncate` or `line-clamp-2` to dynamic text fields.
  - Add `active:scale-[0.98]` tactile press physics and `focus-visible:ring-2` to interactive elements.
  - Wrap tables in `overflow-x-auto` containers.

### Step 4: Quality Gate & Anti-Pattern Auditing
- Call the MCP tool `audit_ui_design(code)` on the generated interface code.
- Evaluates against the 35 Anti-Pattern & Mechanical Rules.
- **Acceptance Criteria**:
  - Originality score must achieve `0.75` or higher (`verdict: ORIGINAL`).
  - Zero critical penalty violations (`GENERIC-001`, `GENERIC-006`, `GENERIC-017`, `MECHANICAL-006`).
  - If score is below `0.75`, proceed to Step 5 (Automated UI Remediation).

### Step 5: Automated UI Remediation Prompt Generation
- When refactoring existing generic components or repairing audit failures, invoke the MCP tool:
  `generate_ui_remediation_prompt(code, sector, surface_mode, layout_paradigm, dial, project_name, target_file, mood_keywords)`.
- Returns an authoritative, load-bearing prompt specification containing:
  - Exact token replacements mapped to the target Sector Archetype.
  - Concrete violation fixes mapped to code snippets.
  - Dial directives (`diversify`, `harden`, `polish`, `bolder`, `quieter`).
  - Mandatory accessibility (WCAG AA) and layout hierarchy directives.

### Step 6: Accessibility (a11y) & Headless Browser Verification
- Use semantic HTML tags (`<main>`, `<nav>`, `<header>`, `<article>`, `<section>`, `<button>`).
- Provide explicit `aria-label` attributes, image `alt` tags, and visible keyboard focus outlines (`tabindex`).
- Use the MCP tool `browser_navigate(url)` to capture headless screenshots and verify visual rendering.
- Return a structured `UiImplementationResult` documenting `originality_score`, `components_reused`, and `a11y_status`.
