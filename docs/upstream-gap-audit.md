# UPSTREAM COMPARATIVE ARCHITECTURE & GAP AUDIT MATRIX
> **Document:** Upstream Comparative Architecture & Gap Audit Matrix  
> **Date:** 2026-08-28  
> **Comparative Scope:** 27 Upstream Repositories in `/Users/haohg/Project/upstreams` vs. 30 MyAgent Core Subsystems (422 passing tests)

---

## 1. SYSTEM COMPARATIVE OVERVIEW

MyAgent implements a comprehensive **Deterministic Control Plane**, establishing strict boundary separation across 4 orthogonal dimensions:
$$\text{Agent Identity} \neq \text{Execution Role} \neq \text{Model Identity} \neq \text{Runtime Identity}$$

The system was benchmarked directly against 27 industry-leading repositories across 4 architectural pillars:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MYAGENT CONTROL PLANE                             │
├──────────────────────┬──────────────────────┬───────────────────────────────┤
│ 1. Multi-Agent &     │ 2. Security, Policy  │ 3. Runtime, Sandbox           │
│    Workflow State    │    & Verification    │    & Observability            │
│ (MetaGPT, LangGraph, │ (Semgrep, Gitleaks,  │ (OpenHands, OpenVibeCoding,   │
│  OpenAI Agents SDK,  │  Trivy, OPA,         │  CubeSandbox, Langfuse,       │
│  CrewAI, Temporal)   │  Promptfoo, Garak)   │  Cline)                       │
├──────────────────────┴──────────────────────┴───────────────────────────────┤
│ 4. UI Governance, Design System & Accessibility (Storybook, Radix, Shadcn)  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. DETAILED COMPARATIVE MATRIX ACROSS 4 PILLARS

### Pillar 1: Multi-Agent Orchestration & Workflow State

| Upstream Repo | Distilled Architectural Pattern | MyAgent Current Baseline | Gap & Strategic Upgrades |
|---|---|---|---|
| **MetaGPT** (`geekan/MetaGPT`) | • Standard Operating Procedures (SOP)<br>• Distinct PM, Architect, Engineer roles<br>• Intermediate Structured Artifacts | Implemented: `AgentRole`, `RolePolicyEngine`, `WholePlanCoordinator`, `AgentWorkResult`. | • Add Pre/Post action hooks per role in `PlanStepExecutionCoordinator`.<br>• Standardize PRD/Architecture contract artifact templates. |
| **OpenAI Agents SDK** (`openai/openai-agents-python`) | • Input/output guardrails<br>• Strict Agent Tool State<br>• Agent-as-a-Tool handoff pattern | Implemented: `HandoffCoordinator`, `HandoffTransitionMatrix`, `HandoffGuardrailEngine`. | • Enforce strict Schema Validation on `AgentHandoffMessage` payloads.<br>• Automated context pruning during handoff to eliminate context bloat. |
| **LangGraph** (`langchain-ai/langgraph`) | • State Graph checkpoints<br>• Interrupt & Human-in-the-loop resume<br>• Time-travel debugging | Implemented: `SQLiteCheckpointStore`, `DecisionService`, `SessionState`. | • Replayable Step Execution: Enable deterministic replay of tool call events from DB following crash recovery. |
| **CrewAI** (`crewAIInc/crewAI`) | • Bounded tools per role<br>• Flow-based execution triggers | Implemented: `ToolPolicy` (ALLOW, REQUIRE_APPROVAL, DENY) keyed by `AgentRole`. | • Automated fallback mechanisms when specialist output fails quality gates. |
| **Pydantic AI** (`pydantic/pydantic-ai`) | • Typed result models<br>• Strict dependency injection | Implemented: `handoff_contracts.py` with Typed Dataclasses. | • Runtime validator automatically translating JSON responses into Typed Contracts. |

---

### Pillar 2: Security, Policy & Verification Gates

| Upstream Repo | Distilled Architectural Pattern | MyAgent Current Baseline | Gap & Strategic Upgrades |
|---|---|---|---|
| **Gitleaks** (`gitleaks/gitleaks`) | • 150+ multi-platform secret patterns<br>• Entropy scoring reducing false positives<br>• Allowlist configuration (`.gitleaks.toml`) | Implemented: `SecretDetectorAdapter` and `SecretRedactor` with foundational patterns (OpenAI, AWS, GitHub). | • **Upgrade Secret Patterns**: Add Private Keys (RSA/EC), GCP Service Accounts, Slack/Discord tokens, JWT, Stripe keys, Database URLs.<br>• Implement Shannon Entropy Filter to detect high-entropy random strings. |
| **Semgrep** (`semgrep/semgrep`) | • AST/Taint-analysis static SAST scanning<br>• CWE/OWASP classification<br>• External YAML rule configuration | Implemented: `SastScannerAdapter` targeting eval/exec patterns, basic SQL injection. | • **Expand SAST Rule Suite**: Cover CWE-89 (SQLi), CWE-78 (Command Injection), CWE-22 (Path Traversal), CWE-79 (XSS), CWE-502 (Insecure Deserialization).<br>• Native `semgrep CLI` invocation bridge when available on host. |
| **Trivy** & **OSV-Scanner** | • Lockfile scanning (`package-lock.json`, `poetry.lock`, `Cargo.lock`, `go.sum`)<br>• CVE/OSV vulnerability database integration | Implemented: `SBOMGenerator` extracting dependencies from `requirements.txt`, `pyproject.toml`, `package.json`. | • Comprehensive lockfile parsing (`package-lock.json`, `go.sum`, `Cargo.lock`).<br>• Checksum validation and license compatibility verification (GPL, MIT, Apache). |
| **OPA (Open Policy Agent)** | • Policy-as-code decoupled from business logic<br>• Deterministic allow/deny evaluation | Implemented: `ToolPolicy` and `RolePolicyEngine` in core code. | • Decouple tool authorization policies into configurable declarative rule files. |
| **Promptfoo** & **Garak** | • AI Red-teaming & Jailbreak probes<br>• Quantitative Prompt Injection defense benchmarks | Implemented: `PromptInjectionDefense` guarding against basic attack heuristics. | • Expand attack vector corpus: Indirect Prompt Injection, Markdown Exfiltration, System Prompt Extraction, Base64/Unicode Obfuscation. |
| **Cosign / Sigstore** | • Digital signing and artifact provenance attestations | Implemented: `ReleaseApprovalCoordinator` and `create_release_attestation` (SHA-256). | • Standardize in-toto format for release artifact provenance attestations. |

---

### Pillar 3: Coding Runtime, Sandbox & Observability

| Upstream Repo | Distilled Architectural Pattern | MyAgent Current Baseline | Gap & Strategic Upgrades |
|---|---|---|---|
| **OpenHands** & **OpenVibeCoding** | • Structured observation loops<br>• Decoupled Agent/Session & HITL Tool approvals<br>• Micro-diff workspace operations | Implemented: `CoderRuntimeWorker`, `CoderAgentStack`, `reconcile_coder_tool_result`. | • Optimize observation diffs: Return concise unified diffs instead of full large files to conserve context window. |
| **CubeSandbox** | • Sandbox lifecycle: create/connect/exec/pause/snapshot/rollback<br>• Default-deny egress network policy | Implemented: `ContainerSandboxBackend` and `LocalSandboxBackend`. | • Finalize filesystem snapshot / rollback before and after each PlanStep. |
| **Langfuse** | • Span-level tracing<br>• Cost, latency, and quality score telemetry | Implemented: `BudgetTracker`, `CostPolicyEngine`, structured event telemetry. | • Add latency telemetry and cumulative cost tracking per specialist agent run. |

---

### Pillar 4: UI Design System & Accessibility Governance

| Upstream Repo | Distilled Architectural Pattern | MyAgent Current Baseline | Gap & Strategic Upgrades |
|---|---|---|---|
| **Radix UI Primitives** | • WAI-ARIA compliant design primitives<br>• Decoupled interaction behavior and styling | Implemented: `UiGovernancePolicy` and `UiCoderAgent`. | • **Accessibility Verification**: Automated WCAG 2.1 auditing (ARIA roles, keyboard handlers, color contrast, semantic HTML). |
| **Storybook** | • Independent component catalog governance<br>• Mandatory component reuse before authoring new elements | Implemented: `UiComponentCatalog`. | • Automatically index existing workspace component libraries (React/Vue/Svelte/Tailwind) into catalog to avoid redundant code generation. |
| **Playwright** | • Headless browser E2E verification<br>• DOM extraction & visual regression screenshots | Implemented: `PlaywrightBrowserAdapter` and `browser_navigate` tool. | • Automated screenshot diffs for layout and visual regression auditing after UI coder step completion. |

---

## 3. ACTIONABLE UPGRADE ROADMAP

Based on the audit findings, the system enhancement roadmap is structured across 3 prioritized phases:

### Phase 1: Security Hardening & Verification Gate (P0)
1. **Upgrade `core/security_scanner.py`**:
   - Integrate 15+ Gitleaks-standard secret detection patterns (RSA/EC keys, GCP Service Accounts, Slack tokens, DB connection strings) with Shannon Entropy Filter.
   - Expand SAST rule coverage aligned with CWE standards (CWE-89, CWE-78, CWE-22, CWE-79, CWE-502).
2. **Upgrade `core/ai_security.py`**:
   - Implement defensive filters against Indirect Prompt Injection, Unicode Smuggling, and Exfiltration vectors based on Promptfoo/Garak taxonomies.
3. **Upgrade `core/release_governance.py`**:
   - Support deep lockfile analysis (`package-lock.json`, `go.sum`, `Cargo.lock`) for CycloneDX 1.5 SBOM generation.

### Phase 2: Multi-Agent Coordination & Handoff Guardrails (P1)
1. **Refine Schema Validation in `core/agent_handoff_context.py` & `core/handoff_coordinator.py`**:
   - Apply context pruning techniques inspired by OpenAI Agents SDK to eliminate context window expansion during cross-agent handoffs.
2. **Expand Pre/Post Validation Hooks in `core/plan_step_execution_coordinator.py`**:
   - Deterministically trigger Verification Gate and Security Gate prior to accepting specialist handoff results (`ImplementationResult`).

### Phase 3: UI Accessibility & Container Sandbox (P2)
1. **Upgrade `core/ui_governance.py`**:
   - Implement automated WAI-ARIA / WCAG 2.1 accessibility checks.
2. **Upgrade `core/sandbox_runtime.py`**:
   - Complete transactional filesystem snapshot and rollback mechanisms for sandboxed execution.

---
*Document officially cataloged at [docs/upstream-gap-audit.md](file:///Users/haohg/Project/my-agent/docs/upstream-gap-audit.md).*
