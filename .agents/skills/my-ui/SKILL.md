---
name: my-ui
description: Creative UI/UX specialist, anti-AI-slop design enforcement, persistent DESIGN contract governance, Sector Archetypes, and Playwright a11y verification.
---

# UI Specialist & Creative Design System Workflow

Activate the **UI Coder Agent (`AgentRole.UI_CODER`)** to design and implement bespoke, highly creative interfaces that eliminate generic "AI slop" while strictly respecting repository boundary policies.

## 1. Core Principles: Anti-AI-Slop & Design Taste

AI-generated interfaces frequently collapse into lazy cliches: blinding pure white or clinical gray canvases, full-rounded pill shapes on every card, generic primary blue (`#3B82F6`), dated indigo-to-purple gradients, uniform 3-column card grids, and centered heroes with exactly 2 CTA buttons.

MyAgent enforces opinionated, production-grade aesthetics using the persistent design contract pattern:
- **Sector Archetypes**: Maps domains (Fintech, Creative Agency, B2B SaaS, Developer Tool, E-commerce, etc.) to curated visual languages and font pairings.
- **Persistent Design Contract**: Encapsulates color palettes, typography tokens, border radii, spacing philosophy, and forbidden patterns.
- **LLM Design Directive**: Injects mandatory design constraints into code generation prompts to enforce exact tokens and forbid AI cliches.
- **Originality Auditing**: Scans code against 12 anti-pattern rules before concluding the turn.
- **Zero-Pollution Policy**: Design contracts and internal metadata are stored exclusively inside MyAgent's internal data store (`data/designs/`), never polluting the user's project workspace.

## 2. Directory of 10 Sector Archetypes

| Sector | Visual Language | Recommended Typography Pairing | Color Temperature |
|---|---|---|---|
| `fintech` | Architectural Minimalism | Public Sans, Geist, IBM Plex Sans | cool-neutral |
| `creative_agency` | Editorial Avant-garde | Syne, Cormorant Garamond, Playfair Display | warm-contrast |
| `health_wellness` | Organic Minimalism | DM Serif Display, Nunito, Lora | warm-muted |
| `saas_b2b` | Structured Clarity | Plus Jakarta Sans, Inter, JetBrains Mono | clean-corporate |
| `portfolio` | Neo-Brutalism | Space Grotesk, Syne, Archivo | high-contrast |
| `ecommerce` | Tactile Editorial | Playfair Display, Outfit, Cormorant | warm-luxury |
| `developer_tool` | Monochrome Technical | JetBrains Mono, Fira Code, Geist Mono | high-contrast dark |
| `startup_consumer` | Kinetic Playful | Bricolage Grotesque, Plus Jakarta Sans | vibrant-punchy |
| `education` | Warm Academic | Newsreader, Source Serif 4, Lora | warm-scholarly |
| `data_analytics` | Precision Dashboard | Geist, Space Grotesk, IBM Plex Mono | technical-dense |

## 3. Five-Step Execution Protocol

### Step 1: Initialize / Retrieve Design Contract (Zero-Pollution)
1. Check whether MyAgent already has a design profile for this project in internal storage (`data/designs/<project>.design.md`).
2. If absent:
   - Call the MCP tool `generate_ui_design_profile(project_description, sector, audience, mood_keywords, workspace_path)` to analyze context and persist the contract inside MyAgent's data store (`data/designs/`).
   - **Zero-Pollution Policy**: DO NOT write loose `DESIGN.md` files into the root of the user's project workspace. All design specifications remain isolated in MyAgent repository storage.
3. If present:
   - Load tokens from MyAgent's store (`surface.primary`, `surface.secondary`, `accent`, `border`), typography pairings, and forbidden patterns.

### Step 2: Component Catalog Discovery & Reuse
- Inspect the workspace to identify existing components (React, Vue, HTML, Vanilla CSS).
- Maximize reuse via `UiComponentCatalog`; do not duplicate existing components.

### Step 3: Implement Interface with Mandatory Design Directives
- Apply exact design tokens from the design contract.
- Strictly enforce Forbidden Anti-Pattern Rules:
  - DO NOT use generic blue `#3B82F6` / `#6366F1` unless explicitly mandated by the brand.
  - DO NOT apply `rounded-full` indiscriminately to cards and containers.
  - DO NOT use uniform 3-column card grids without visual hierarchy.
  - DO NOT use generic blue-to-purple gradients.
  - DO NOT use uninspired pure white `#FFFFFF` or clinical `#F9FAFB` canvases.

### Step 4: Quality Gate & Anti-Pattern Auditing
- Call the MCP tool `audit_ui_design(code)` on the generated interface code.
- **Acceptance Criteria**:
  - Originality score must achieve `0.75` or higher (`verdict: ORIGINAL`).
  - Zero critical penalty violations (`GENERIC-001`, `GENERIC-006`).
  - If score is below `0.75`, refactor colors, typography, and container shapes before delivery.

### Step 5: Accessibility (a11y) & Headless Browser Verification
- Use semantic HTML tags (`<main>`, `<nav>`, `<header>`, `<article>`, `<section>`, `<button>`).
- Provide explicit `aria-label` attributes, image `alt` tags, and visible keyboard focus outlines (`tabindex`).
- Use the MCP tool `browser_navigate(url)` to capture headless screenshots and verify visual rendering.
- Return a structured `UiImplementationResult` documenting `originality_score`, `components_reused`, and `a11y_status`.


