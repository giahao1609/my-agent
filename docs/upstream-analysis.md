# MyAgent — Upstream Analysis, Architecture Plan, and Final Goal

> Status: Living architecture document
> Updated: 2026-08-27

---

## 1. Final Goal

MyAgent is intended to become a **durable, multi-agent software engineering system** in which multiple specialized AI agents collaborate through explicit contracts, policies, and lifecycle boundaries.

The target is **not** a single large coding agent that plans, edits, tests, reviews, approves, and deploys everything by itself.

The target is a system in which:

- each agent has one primary responsibility;
- agent identity is separate from model identity and runtime identity;
- every agent receives only the tools and context required for its role;
- inter-agent handoff is explicit and coordinator-controlled;
- durable Task / Plan / PlanStep / Session state is owned by MyAgent control-plane services;
- models propose or execute work but do not directly own lifecycle state;
- execution is sandboxed, least-privilege, resource-budgeted, and observable;
- testing, security, review, and policy gates can reject work before a step is accepted;
- UI generation is constrained by a registered design system and component catalog;
- memory, knowledge, code graph, browser, sandbox, model, and external services remain replaceable behind interfaces;
- process restart must not destroy task continuity;
- provider/runtime replacement must not change the meaning of an agent role.

The final system should support a workflow similar to:

```text
User Objective
     |
     v
Durable Task
     |
     v
Planner Agent
     |
     v
Typed PlanProposal
     |
     v
PlanService
     |
     v
ACTIVE Plan
     |
     v
Execution Coordinator
     |
     +-------------------+-------------------+
     |                   |                   |
     v                   v                   v
Research Agent      Architect Agent     Specialist Coder
                                             |
                          +------------------+------------------+
                          |                                     |
                          v                                     v
                    Backend Coder                          UI Coder
                          |                                     |
                          +------------------+------------------+
                                             |
                                             v
                                         Test Agent
                                             |
                                             v
                                      Security Reviewer
                                             |
                                             v
                                         Reviewer Agent
                                             |
                                      structured result
                                             |
                                             v
                                    Execution Coordinator
                                             |
                                             v
                                      PlanService transition
                                             |
                                  COMPLETED / FAILED / REPLAN
```

The final MyAgent must remain usable with different models and runtimes:

```text
Agent identity != Execution role != Model identity != Runtime identity
```

Example:

```text
agent_id: ui-coder
role: ui_implementation
model: provider/model-A
runtime: in_process
```

Changing the model to another provider must not turn the UI agent into a planner or reviewer.

---

## 2. Architectural Principles

- Upstreams are references/building blocks, not repositories to merge wholesale.
- Flow: **Research -> Audit -> Select -> Adapt -> Integrate**.
- MyAgent Python core owns interfaces, orchestration, policy, lifecycle, and domain models.
- External implementations stay behind adapters.
- Durable domain state has one authoritative owner.
- Model output is never persistence authority by itself.
- Prompt instructions are not authorization boundaries.
- Least privilege is enforced structurally through tools, policies, paths, capabilities, and contracts.
- Internal components should communicate directly through typed interfaces; MCP/ACP are external integration boundaries, not mandatory internal hops.
- Local host execution is never an automatic fallback from failed sandbox execution.
- Network access is default-deny and resource-budgeted.
- Raw prompts, source code, secrets, tool output, memory contents, and traces are not telemetry by default.
- MyAgent should prefer deterministic verification wherever a deterministic tool exists.

---

## 3. Core Control-Plane Rule

The most important architectural separation is:

```text
AI / Model Plane
    proposes / executes / evaluates

Control Plane
    owns durable lifecycle and authorization
```

Control-plane components include:

- `TaskService`
- `PlanService`
- `PlanStepExecutionCoordinator`
- `Orchestrator`
- `SessionStore`
- `CheckpointStore`
- `ToolPolicy`
- future `AgentPolicy`
- future execution/recovery coordinators

Agents must not directly mutate durable Task or Plan state.

For example:

```text
Planner Agent
   X activate plan
   X start coder session
   X complete task

Coder Agent
   X mark PlanStep completed
   X change Task state
   X approve its own destructive action

Reviewer Agent
   X rewrite production code during review
   X bypass tests or security gates
```

Instead:

```text
Agent -> typed result -> Coordinator -> Domain Service -> durable state
```

---

## 4. Agent Isolation Model

Agent isolation must not depend on prompts alone.

Each agent should eventually be defined by a policy object similar to:

```text
AgentDefinition
├── agent_id
├── role
├── allowed_tools
├── forbidden_tools
├── allowed_paths / scopes
├── input_contract
├── output_contract
├── model target policy
├── runtime policy
├── delegation policy
└── approval requirements
```

A role is therefore:

```text
responsibility
+ allowed input
+ allowed output
+ allowed tools
+ allowed scope
+ lifecycle position
```

not merely:

```text
system_prompt = "You are a reviewer"
```

### Recommended initial specialist roles

#### Planner Agent

Responsibilities:

- interpret the durable Task objective;
- decompose work into PlanSteps;
- identify dependencies and execution order;
- propose execution roles for steps.

Must not:

- edit production code;
- execute shell commands;
- mutate durable Task/Plan lifecycle directly.

#### Architect Agent

Responsibilities:

- analyze boundaries, interfaces, dependencies, and system design;
- use Code Graph and repository context;
- propose implementation constraints and contracts.

Must not:

- become a second planner;
- write production code unless explicitly assigned a coding step.

#### Research Agent

Responsibilities:

- inspect upstreams, documentation, web references, and code graph;
- return evidence-backed structured research.

Must not:

- modify workspace files;
- execute destructive tools.

#### Backend Coder Agent

Responsibilities:

- implement backend/business/core logic for an assigned step;
- use only the step scope and allowed workspace/tool set.

Must not:

- change Task/Plan state;
- perform UI design work outside the assigned contract.

#### UI Coder Agent

Responsibilities:

- implement UI, accessibility, design-system-aligned components and interactions;
- reuse existing components before adding new ones;
- update stories/tests when required.

Must not:

- redesign backend contracts without explicit handoff;
- create arbitrary styling systems outside project governance.

#### Test Agent

Responsibilities:

- inspect acceptance criteria;
- run deterministic test tools;
- author missing tests when the step specifically assigns that work;
- return structured test evidence.

Must not:

- silently fix production code while evaluating it;
- decide durable completion by itself.

#### Security Reviewer Agent

Responsibilities:

- consume deterministic security findings;
- deduplicate, contextualize, prioritize, and explain them;
- propose remediation;
- return a typed security review result.

Must not:

- disable security scanners;
- rotate credentials autonomously;
- approve its own bypass;
- receive raw secret values when redacted metadata is enough.

#### Reviewer Agent

Responsibilities:

- inspect diff, tests, security results, architecture constraints, and acceptance criteria;
- return `approve`, `reject`, or `request_rework` as structured output.

Must not:

- directly rewrite production code during the review step;
- mutate PlanStep lifecycle directly.

### Later specialist roles

Possible later additions:

- Database/Migration Agent
- Performance Agent
- Documentation Agent
- Security Red-Team Agent
- Release Agent
- Dependency Upgrade Agent

These should be added only when the deterministic workflow and role isolation are proven.

---

## 5. Typed Inter-Agent Contracts

Agents should not freely chat with one another.

Preferred flow:

```text
Agent A
  |
  v
Typed Result
  |
  v
Coordinator
  |
  v
Typed Job
  |
  v
Agent B
```

Avoid:

```text
Agent A <-> Agent B <-> Agent C <-> Agent D
```

because ownership, scope, and durable causality become difficult to reason about.

Planned result contracts may include:

- `PlanProposal`
- `ArchitectureProposal`
- `ResearchResult`
- `ImplementationResult`
- `UiImplementationResult`
- `TestResult`
- `SecurityFinding`
- `SecurityReviewResult`
- `ReviewResult`
- `ExecutionResult`

All model-generated structured results should be validated before they are materialized into durable domain state.

---

## 6. Durable Domain Model

The durable hierarchy should remain explicit:

```text
Project
  |
  +-- Task
        |
        +-- Plan revision
              |
              +-- PlanStep
                    |
                    +-- execution_session_id
                          |
                          +-- Coder Session
```

### Task

Task lifecycle remains control-plane owned.

Current lifecycle:

```text
CREATED -> PLANNING -> EXECUTING -> COMPLETED
    \          \            \
     -> FAILED  -> FAILED     -> FAILED
     -> CANCELLED             -> CANCELLED
```

### Plan

Current lifecycle:

```text
DRAFT -> ACTIVE -> COMPLETED
  |        |  \
  |        |   -> FAILED
  |        -> SUPERSEDED
  -> SUPERSEDED
```

### PlanStep

Current lifecycle:

```text
PENDING -> RUNNING -> COMPLETED
   |          \
   |           -> FAILED
   -> SKIPPED
```

### Execution session binding

A running PlanStep may bind to a durable execution session:

```text
PlanStep
├── step_id
├── state = RUNNING
└── execution_session_id = <session id>
```

This binding is required for restart/recovery and terminal reconciliation.

---

## 7. Current Implementation Checkpoint

### Completed foundations

Current completed or established foundations include:

- reusable Code Graph foundation;
- SQLite persistence foundation;
- durable Coder Session continuity;
- restart/resume/handoff behavior;
- durable approval recovery;
- Task control-plane;
- Plan control-plane;
- Planner abstraction;
- ModelBackend abstraction and provider/router infrastructure;
- OpenAI-compatible model backend;
- production model-generated planning;
- IN_PROCESS coder runtime;
- model -> tool -> result -> continuation loop;
- destructive-tool approval flow;
- coder task/worker lifecycle cleanup;
- runtime failure observability;
- shared coder-session startup path.

Last confirmed full-suite baseline before the newest single-step execution changes:

```text
311 passed
```

### Single PlanStep Execution — In Progress

Implemented:

- `PlanService.start_step()` active-plan/task guards;
- `PlanService.fail_step()`;
- `PlanStepExecutionCoordinator`;
- `PlanStepCoderLauncher` protocol;
- durable `execution_session_id` on `PlanStepRecord`;
- SQLite migration for legacy `plan_steps` tables;
- `PlanService.bind_step_execution_session()`;
- shared `_start_coder_session_for_project(...)` path;
- explicit PlanStep task ownership using `task.task_id`;
- MCP `start_plan_step_execution(step_id)`;
- partial-start cleanup if setup fails after session creation.

Latest focused regression at the current pause point:

```text
9 passed
```

Still pending before single-step execution is complete:

1. prove behavior when setup fails and `orchestrator.cancel()` also fails;
2. reconcile successful coder-session termination to `PlanStep.COMPLETED`;
3. reconcile runtime/session failure to `PlanStep.FAILED`;
4. recover/reconcile running PlanSteps after process restart using `execution_session_id`;
5. run broad regression;
6. run full repository suite and establish a new baseline.

Whole-plan automatic sequencing must not start before these are proven.

---

# 8. Upstream Analysis

## Principles

- Upstreams are references/building blocks, not repositories to merge wholesale.
- Flow: Research -> Audit -> Select -> Adapt -> Integrate.
- MyAgent Python core owns interfaces, orchestration, policy, lifecycle, and domain models.
- External implementations stay behind adapters.

---

## TencentDB-Agent-Memory

SELECT:

- Chat memory L0-L3 concepts behind MemoryBackend.
- Wiki and CodeGraph behind KnowledgeBackend.
- Progressive/on-demand retrieval patterns.
- Separate Chat Memory from Wiki/CodeGraph.
- Thin HTTP/SDK integration boundary.

ADAPT:

- ExecutionContext owns workspace/user/agent/session/task/project scope.
- Explicit lifecycle: create -> initialize -> start -> stop.
- Durable L0 first; background enrichment tracked and drained.
- Typed unavailable/not-ready states.
- Metadata-only telemetry by default.

AVOID:

- Tencent-specific IDs and URLs in core.
- Default-open auth, permissive isolation, raw prompt/code/error telemetry.
- Mandatory MCP hop inside MyAgent.

---

## OpenVibeCoding

SELECT:

- AgentRuntime abstraction.
- Agent/Session separation.
- Structured event streaming.
- Persistent HITL/tool approval model.
- Lazy sandbox acquisition and resumable sessions.
- SandboxRuntime protocol and dependency injection.

ADAPT:

- Remote isolated sandbox as safe default.
- Local execution explicit dev-only.
- Capability states: READY / DEGRADED / UNAVAILABLE.

AVOID:

- Claude SDK coupling in core.
- CloudBase-specific infrastructure.
- Global permission singleton.
- Mandatory Git remote/archive.
- Raw backend errors and sensitive runtime config in model context.

---

## CubeSandbox

SELECT:

- Preferred hardened production SandboxBackend via Python SDK/API.
- create/connect/exec/files/pty/pause/resume/snapshot/rollback/clone lifecycle.
- Independent volume/workspace lifecycle.
- Default-deny egress and external credential injection concepts.
- Explicit resource limits and sandbox capabilities.

ADAPT:

- Cube-specific SDK stays under integrations/cubesandbox.
- Sync SDK calls must not block async orchestrator.
- Traffic tokens remain inside transport adapter.
- Finite execution timeout by default.

AVOID:

- CubeAPI/CubeMaster/Cubelet/KVM/eBPF internals in core.
- Auth-off defaults.
- Unlimited execution/resource defaults.
- Automatic fallback to local execution.

---

## OpenHands Agent Canvas

SELECT:

- Frontend/control-plane separated from agent execution.
- Multiple interchangeable agent backends.
- ACP as external integration boundary.
- Backend location independent from agent identity.
- Explicit runtime service discovery.

AVOID:

- React/Electron implementation details in backend core.
- Host execution as automatic fallback.
- Canvas deployment/UI infrastructure.

---

## OpenHands

SELECT:

- Coding-agent runtime architecture.
- Workspace interaction patterns.
- Tool/action observation loops.
- Separation between agent logic and execution environment.
- Long-running coding-task concepts.
- Evaluation-friendly coding trajectories.

ADAPT:

- Coding runtime concepts behind AgentRuntime / SandboxBackend.
- MyAgent keeps Task/Plan/Session lifecycle ownership.
- Tool events remain structured AgentEvent records.
- Specialist coding roles receive scoped tool sets.

AVOID:

- Replacing MyAgent orchestrator with OpenHands runtime.
- OpenHands-specific event/domain types in core.
- One monolithic coding agent owning every software-engineering responsibility.
- Automatic host execution fallback.

---

## Cline

SELECT:

- Coding-agent tool-loop patterns.
- IDE surface separated from core agent execution.
- Explicit file/tool operations.
- Approval boundaries around sensitive operations.
- Context acquisition for coding tasks.
- Extension/backend separation.

ADAPT:

- Coding tools remain MyAgent ToolDefinition entries.
- IDE/UI becomes a client of MyAgent rather than owning orchestration.
- Permission decisions remain centralized in ToolPolicy.
- Runtime/model remain replaceable.

AVOID:

- VS Code-specific architecture in MyAgent core.
- Prompt-based authorization.
- One giant coding agent owning planning, execution, testing, and review.
- IDE process as durable source of truth.

---

## MetaGPT

SELECT:

- Role-oriented software organization concepts.
- Product Manager / Architect / Project Manager / Engineer specialization.
- SOP-driven agent behavior instead of unconstrained agent collaboration.
- Structured intermediate artifacts passed between roles.
- Explicit responsibility boundaries between software-development roles.

ADAPT:

- Agent roles become MyAgent AgentDefinition / capability profiles.
- SOP concepts become deterministic workflow policies owned by MyAgent.
- Inter-agent communication goes through typed artifacts and coordinator-controlled handoffs.
- Role-specific tool permissions are enforced outside the model.
- One PlanStep has an explicit execution role.

AVOID:

- Prompt-only role isolation.
- Allowing agents to freely message or delegate to one another.
- MetaGPT-specific role classes in MyAgent core.
- Autonomous software-company loops controlling durable Task/Plan state.
- Treating natural-language SOPs as authorization boundaries.

---

## CrewAI

SELECT:

- Explicit role, goal, task, and tool ownership.
- Separation between autonomous agent groups and deterministic workflow flows.
- Task-specific agent assignment.
- Guardrails around task outputs.
- Structured delegation concepts.

ADAPT:

- MyAgent coordinator owns delegation.
- Each agent receives only tools required for its role.
- Agent outputs use typed domain contracts.
- Flow concepts inform MyAgent execution coordination while durable lifecycle remains in TaskService / PlanService.
- Delegation is explicit and policy-controlled.

AVOID:

- Free-form delegation between agents.
- Giving every agent the complete tool registry.
- CrewAI objects becoming MyAgent domain models.
- Letting model decisions directly mutate Task or Plan lifecycle.
- Hidden shared mutable state between agents.

---

## OpenAI Agents SDK

SELECT:

- Agent handoff concepts.
- Agents-as-tools for bounded specialist invocation.
- Input/output guardrails.
- Session concepts.
- Structured tracing of agent execution.
- Tool/schema-driven agent capabilities.

ADAPT:

- Handoffs become explicit coordinator operations.
- Specialist agents are invoked behind typed MyAgent interfaces.
- Guardrails become policy checks outside model prompts.
- Tracing defaults to metadata-only.
- Agent identity remains independent from model/runtime backend.

AVOID:

- OpenAI SDK types in MyAgent core domain.
- Provider-specific model assumptions in orchestration.
- Raw prompt/code/tool-result tracing by default.
- Agent-controlled durable lifecycle changes.
- Unrestricted nested agent delegation.

---

## LangGraph

SELECT:

- Durable execution concepts.
- Explicit workflow state.
- Interrupt/resume patterns.
- Human-in-the-loop checkpoints.
- Recovery after process restart.
- Graph-based conditional execution.

ADAPT:

- Durable execution principles feed MyAgent ExecutionCoordinator.
- MyAgent stores remain the authoritative source of Task/Plan/Session state.
- Recovery decisions use durable identifiers such as task_id, plan_id, step_id, execution_session_id.
- Workflow transitions remain validated by domain services.

AVOID:

- Making LangGraph state the second source of truth.
- Mirroring complete Task/Plan state into an external graph runtime.
- Coupling MyAgent core to LangGraph.
- Graph nodes directly writing durable lifecycle state.
- Automatically converting every workflow into a graph.

---

## Pydantic AI

SELECT:

- Typed model inputs and outputs.
- Structured result validation.
- Dependency injection for agent dependencies.
- Typed tool schemas.
- Explicit model/context boundaries.
- Specialist/sub-agent patterns.

ADAPT:

- Every MyAgent specialist returns a typed domain result.
- Invalid model output fails before reaching durable services.
- Agent context is explicitly scoped through ExecutionContext.
- Tool schemas remain derived from MyAgent ToolDefinition.

AVOID:

- Pydantic-specific models becoming mandatory core domain types.
- Giving model output direct persistence authority.
- Provider/runtime coupling in domain interfaces.
- Passing unrestricted global context into every specialist.

---

## WorkBuddy Bench

LICENSE: restricted Tencent license; reference-only.

SELECT CONCEPTS ONLY:

- EvaluationJob = model + agent/harness + task/dataset + isolated workspace + grader.
- Capture patch/artifact, trajectory, test results, efficiency, score.
- Evaluation categories: Code, Web, Office, Security.

AVOID:

- Copying/adapting source or datasets without separate license review.

---

## Playwright

SELECT:

- Playwright CLI for token-efficient agent browser interaction.
- Playwright Test for deterministic E2E verification.
- Playwright Library for low-level BrowserBackend when required.
- Playwright MCP only for external MCP consumers.
- Fresh browser context per test/task.
- Trace/screenshots as optional artifacts.

ADAPT:

- BrowserPolicy controls domains, private networks, timeout, downloads, artifacts, credentials.

AVOID:

- Browser patches/internal Playwright source.
- Routing internal TesterAgent through MCP unnecessarily.

---

## Ant Design

SELECT:

- UI component dependency/reference only.
- Enterprise UI layout/component patterns.
- Consistency principles for forms, tables, navigation, feedback, and data-heavy applications.

ADAPT:

- UI Agent may use registered project components and design rules derived from the target project.

AVOID:

- Copying the repository or build system wholesale.
- Backend/core dependency on Ant Design.

---

## shadcn/ui

SELECT:

- Selected customizable source components.
- Build MyAgent-specific design system and domain components.
- Code ownership model rather than opaque component dependency assumptions.

AVOID:

- Copying the monorepo/build system wholesale.
- Allowing UI agents to produce duplicated uncontrolled component variants.

---

## Storybook

SELECT:

- Component-first UI development.
- Isolated component states.
- Executable UI documentation.
- Visual and interaction verification.
- Reusable component catalog.
- Design-system governance patterns.

ADAPT:

- UI Agent must search the existing component catalog before creating components.
- New reusable UI components require stories when the target project uses Storybook.
- Existing components are preferred over duplicated implementations.
- UI verification may produce screenshots/traces as optional artifacts.
- TesterAgent may validate stories independently from UiCoderAgent.

AVOID:

- Storybook implementation details in backend core.
- Treating Storybook as application runtime.
- Allowing UI agents to create arbitrary components outside project design-system policy.

---

## Radix Primitives

SELECT:

- Accessible UI primitive patterns.
- Behavior separated from visual styling.
- Keyboard/focus/dialog/menu interaction semantics.
- Composable low-level primitives.

ADAPT:

- Accessibility rules become part of UiAgent policy.
- Existing accessible primitives are preferred over custom interaction behavior.
- Visual design remains project-specific.

AVOID:

- React-specific assumptions in MyAgent core.
- Reimplementing accessibility behavior without need.
- Treating a component library as the UI architecture itself.

---

# 9. Security Track

Security is not one AI role. It is a layered architecture composed of deterministic scanners, policy engines, isolation boundaries, and AI-assisted review.

Preferred model:

```text
Deterministic scanners
        |
        v
SecurityFinding[]
        |
        v
SecurityReviewerAgent
        |
        v
SecurityReviewResult
        |
        v
Coordinator / Policy Gate
```

The AI interprets evidence; it does not replace evidence-producing tools.

---

## Semgrep

SELECT:

- Static analysis as a deterministic security signal.
- Security and correctness rules.
- Project-specific coding guardrails.
- Local/CI execution.
- Structured findings suitable for machine consumption.

ADAPT:

- Findings become typed `SecurityFinding` records.
- SecurityReviewerAgent consumes findings; it does not replace the scanner.
- Scanner execution stays behind a `CodeSecurityScanner` adapter.
- MyAgent policy decides whether a finding blocks completion.

AVOID:

- Letting an LLM invent SAST findings without scanner evidence.
- Semgrep-specific domain models inside MyAgent core.
- Mandatory MCP routing internally.
- Uploading repository source by default.

---

## Trivy / OSV-Scanner

SELECT:

- Dependency vulnerability scanning.
- Lockfile/package analysis.
- SBOM concepts.
- Machine-readable vulnerability results.

ADAPT:

- Dependency scanning lives behind a `DependencySecurityBackend` abstraction.
- Findings remain scoped to workspace/project.
- Severity, affected package, version, and advisory metadata stay structured.
- SecurityReviewerAgent may explain and prioritize findings.

AVOID:

- Kubernetes-specific architecture in MyAgent.
- Tool-specific vulnerability models as core domain types.
- Automatically updating dependencies without an explicit PlanStep.

---

## Gitleaks

SELECT:

- Secret detection before and after code modification.
- Repository and working-tree scanning.
- Structured secret findings.

ADAPT:

- Secret scanning lives behind a `SecretScanner` adapter.
- Matched secret values are redacted before model exposure.
- Durable findings store fingerprints/locations and metadata, not raw credentials.

AVOID:

- Sending discovered secrets into prompts.
- Persisting secret values in telemetry.
- Allowing agents to rotate credentials autonomously.

---

## Open Policy Agent

SELECT:

- Policy-as-data / policy-as-code concepts.
- Separation of policy decision from execution.
- Explicit allow/deny decisions with structured context.

ADAPT:

- MyAgent `ToolPolicy` remains core authority.
- A future optional `PolicyBackend` may delegate complex enterprise rules.
- Policy inputs use sanitized structured ExecutionContext.

AVOID:

- LLM-based authorization decisions.
- Making OPA mandatory for local MyAgent.
- Passing raw prompts, code, or secrets into policy evaluation unnecessarily.

---

## Sigstore / Cosign

SELECT:

- Artifact signing and provenance concepts.
- Verification of release artifacts.
- Supply-chain transparency concepts.

ADAPT:

- Future `SupplyChainVerifier` / release gate.
- Signing belongs to release workflow, not coder workflow.

AVOID:

- CoderAgent autonomously signing or publishing artifacts.
- Introducing release infrastructure before core execution/review is stable.

---

## Promptfoo / garak

SELECT:

- AI red-team scenarios.
- Prompt-injection testing.
- Tool-abuse tests.
- Agent-policy bypass tests.
- Model/provider comparison.
- Repeatable adversarial evaluation.

ADAPT:

- Security evaluation runs in isolated sandbox.
- Red-team agents/models receive no production credentials.
- Test results become typed `EvaluationResult` / `SecurityFinding` records.
- Red-team execution is explicitly requested or scheduled.

AVOID:

- Running adversarial payloads on trusted host with production secrets.
- Treating model-generated attacks as trusted code.
- Giving red-team agent write access to production workspace by default.

---

# 10. UI Governance Track

A dedicated UI Agent must follow a project-specific UI constitution rather than generating a new design language for every task.

Target model:

```text
UI_SYSTEM
├── design_tokens
│   ├── spacing
│   ├── typography
│   ├── radius
│   ├── shadows
│   └── breakpoints
├── component_catalog
├── accessibility_rules
├── interaction_patterns
├── page_patterns
│   ├── CRUD
│   ├── Dashboard
│   ├── Settings
│   └── Detail
└── verification
    ├── Storybook
    ├── visual checks
    └── Playwright
```

UI Agent workflow should eventually be:

```text
1. inspect target project's design system
2. search existing component catalog
3. reuse existing component when possible
4. create new component only when justified
5. obey design tokens
6. obey accessibility rules
7. update story/test where applicable
8. run deterministic UI verification
9. return UiImplementationResult
```

Avoid uncontrolled proliferation such as:

```text
ButtonBlue
ButtonNew
CustomButton2
```

when an existing project component can be reused.

---

# 11. Testing and Evaluation Track

Testing and agent evaluation are separate concerns.

## Functional verification

Potential deterministic tools:

- pytest
- project-native unit/integration tests
- Playwright
- type checkers
- linters
- formatters
- compiler/build tools
- framework-specific test runners

## Agent evaluation

Evaluation should capture:

```text
EvaluationJob
├── model
├── agent/harness
├── task/dataset
├── isolated workspace
├── resulting patch/artifacts
├── trajectory metadata
├── test results
├── security results
├── efficiency metrics
└── score
```

The evaluation system should be able to compare:

- models;
- agent prompts/policies;
- tool configurations;
- routing strategies;
- memory/retrieval strategies;
- sandbox policies;
- specialist workflows.

Evaluation must not become production lifecycle authority.

---

# 12. Observability Track

Potential references:

- OpenTelemetry
- Langfuse

MyAgent should expose a `TelemetryBackend`-style boundary while preserving metadata-only defaults.

Safe default telemetry may include:

```text
TraceEvent
├── task_id
├── plan_id
├── step_id
├── session_id
├── agent_id
├── role
├── model target metadata
├── runtime metadata
├── event_type
├── duration
├── token usage
├── status
└── sanitized error type
```

Do not include by default:

- raw prompts;
- source code;
- memory contents;
- credentials;
- full tool outputs;
- unrestricted traces.

Observability should answer questions such as:

- Which agent owned a step?
- Which model/runtime executed it?
- Which tools were requested?
- Which approvals occurred?
- Why did execution fail?
- How much time/tokens did each stage consume?
- Which deterministic gate rejected the result?

---

# 13. Browser Automation Track

Browser execution is a privileged capability and must be policy-governed.

Recommended abstractions:

- `BrowserBackend`
- `BrowserPolicy`

Policy controls should include:

- allowed domains;
- denied domains;
- private network access;
- authentication scope;
- downloads;
- upload permissions;
- execution timeout;
- artifact capture;
- fresh context/session boundaries;
- credential exposure.

Playwright MCP is for external MCP consumers; internal agents should use direct typed browser interfaces when appropriate.

---

# 14. Sandbox and Execution Isolation

Production execution target:

```text
SandboxBackend
├── CubeSandbox adapter candidate
├── LocalSandbox explicit dev-only
└── FakeSandbox tests
```

Security defaults:

- remote isolated execution preferred for untrusted code;
- no automatic host fallback;
- default-deny egress;
- finite timeouts;
- CPU/memory/disk/process limits;
- external credential injection only when explicitly scoped;
- workspace/session separation;
- sandbox capability reporting;
- pause/resume/snapshot support where the backend supports it.

The Sandbox backend must remain replaceable.

---

# 15. Memory and Knowledge Track

The distinction remains:

```text
MemoryBackend
    = conversational / durable agent memory

KnowledgeBackend
    = wiki / code graph / project knowledge / indexed references
```

Do not collapse them into one generic retrieval bucket.

Retrieval should be progressive and scoped by `ExecutionContext`:

```text
workspace_id
user_id
agent_id
session_id
task_id
project_id
```

Memory and knowledge providers must not become mandatory internal MCP hops.

---

# 16. Tool and Permission Architecture

`ToolDefinition` remains the single source of truth for:

- schema;
- permissions;
- handler;
- exposure.

Future agent-role policy should filter tool exposure before model invocation.

Example:

```text
Planner Agent
  allowed: code search, knowledge read
  denied: write_file, shell_execute, destructive tools

UI Coder Agent
  allowed: read/write UI files, component search, UI tests
  denied: task lifecycle mutation, policy mutation

Reviewer Agent
  allowed: read diff, read code, run tests
  denied: write_file by default
```

Authorization chain:

```text
credential
   -> principal
      -> authorization
         -> ExecutionContext scope
            -> agent role policy
               -> tool permission
                  -> approval if required
                     -> execution
```

---

# 17. Model Routing, Cost, and Reliability

Model routing is an integration concern, not an agent-role definition.

MyAgent should support:

- default model backend;
- runtime-specific backend;
- model-specific backend;
- future policy-based model selection;
- capability-driven routing;
- cost/latency constraints;
- retry/fallback policies that do not change agent identity.

Potential future strategy:

```text
Planner Agent
  -> reasoning-oriented model

Research Agent
  -> retrieval-efficient model

UI Coder
  -> model strong at frontend generation

Reviewer
  -> separate model/provider when useful
```

But the durable step still says:

```text
role = reviewer
```

not:

```text
role = provider-X-model-Y
```

Model failure, rate limits, and provider outages should produce typed runtime/model failures and remain observable without leaking sensitive payloads.

---

# 18. Reliability and Recovery

MyAgent must assume process death can occur at any point.

Recovery invariants should eventually cover:

```text
PlanStep RUNNING
execution_session_id = session-X
        |
        v
load durable Session
        |
        +-- resumable -> resume
        |
        +-- successful terminal -> reconcile step COMPLETED
        |
        +-- failed terminal -> reconcile step FAILED
        |
        +-- inconsistent -> explicit recovery decision
```

No unresolved runtime/tool side effect should be automatically replayed unless replay safety is proven.

Approval state must remain durable.

The system must prefer explicit reconciliation over guessing.

---

# 19. Whole-Plan Execution — Future Architecture

Automatic multi-step execution is intentionally deferred.

It should be added only after single-step execution, terminal reconciliation, security gates, review gates, and restart recovery are proven.

Future flow:

```text
ACTIVE Plan
   |
   v
find next executable PENDING step
   |
   v
choose owning specialist role
   |
   v
start exactly one execution
   |
   v
wait for terminal result
   |
   +-- success -> Test/Security/Review gates
   |                 |
   |                 +-- approved -> COMPLETE step
   |                 |
   |                 +-- rejected -> rework/replan policy
   |
   +-- runtime failure -> FAIL step / recovery policy
   |
   v
next step only after control-plane decision
```

The whole-plan loop must have safeguards for:

- maximum steps;
- retry budgets;
- token/cost budgets;
- execution time budgets;
- repeated failure detection;
- replan limits;
- human approval boundaries;
- destructive action boundaries;
- security gates;
- no infinite delegation loops.

---

# 20. Target Module Boundaries

A possible future structure:

```text
core/
├── task.py
├── plan.py
├── session.py
├── context.py
├── model.py
├── events.py
├── tools.py
├── protocols.py
├── agent_definition.py          # future
├── agent_role.py                # future
├── agent_contracts.py           # future
├── execution_result.py          # future
├── security.py                  # future domain contracts
└── evaluation.py                # future domain contracts

services / coordinators
├── TaskService
├── PlanService
├── PlanningCoordinator
├── PlanStepExecutionCoordinator
├── ExecutionResultCoordinator   # future
├── RecoveryCoordinator          # future
├── HandoffCoordinator           # future
└── PlanExecutionCoordinator     # future, after single-step proof

agents/
├── planner
├── coder
├── researcher                   # future
├── architect                    # future
├── ui_coder                     # future
├── tester                       # future
├── security_reviewer            # future
└── reviewer                     # future

integrations/
├── model backends
├── runtime backends
├── sandbox backends
├── browser backends
├── memory backends
├── knowledge backends
├── security scanners
├── policy backends
├── telemetry backends
└── external MCP/ACP adapters
```

This is conceptual; implementation should evolve incrementally rather than forcing the repository into this layout immediately.

---

# 21. Committed MyAgent Boundaries

- `AgentRuntime`: engine/session/event abstraction.
- `ModelBackend`: model generation abstraction independent from agent role.
- `MemoryBackend`: Chat Memory abstraction.
- `KnowledgeBackend`: Wiki/CodeGraph/project knowledge abstraction.
- `SandboxBackend`: CubeSandbox production candidate; Local explicit dev-only; Fake for tests.
- `ToolDefinition`: one source of truth for schema, permissions, handler, exposure.
- MCP/ACP: external adapters, not mandatory internal hops.
- Session is not Workspace; storage may outlive sessions.
- Authentication: credential -> principal -> authorization -> scope -> tool permission.
- Network execution is default-deny and resource-budgeted.
- Raw prompts, memory, code, credentials, tool output, and traces are not telemetry by default.
- Agent role is enforced by capability, tool, input, output, scope, and lifecycle boundaries; not by prompt alone.
- Agents do not directly mutate durable Task/Plan lifecycle state.
- Inter-agent work passes through typed artifacts coordinated by MyAgent.
- Agents do not communicate or delegate freely; handoff is an explicit coordinator operation.
- Each execution step has one primary owning agent role.
- Tool exposure follows least privilege per agent role.
- Model identity, agent identity, runtime identity, and execution role are separate concepts.
- Specialist agents do not become additional durable sources of truth.
- Review/Test/Security agents may reject work but do not silently rewrite production code outside an explicitly assigned repair step.
- UI generation is constrained by the registered project design system, component catalog, and accessibility policy.
- Deterministic tools are preferred over model speculation for tests, security, dependency analysis, and policy checks.
- Secret values are redacted before model exposure whenever possible.
- Security scanners and policy engines are evidence/policy sources; AI reviewers interpret their outputs but do not replace them.

---

# 22. Research Priority

Do not clone every possible repository at once.

Research should be prioritized by the next architectural risk.

## P0 — before autonomous whole-plan execution

- MetaGPT — role/SOP architecture
- OpenAI Agents SDK — handoff/guardrail/agent-tool patterns
- LangGraph — durable workflow/recovery concepts
- Semgrep — code security scanning
- Gitleaks — secret scanning
- Trivy — dependency/SBOM scanning
- Promptfoo — agent/prompt security evaluation
- Storybook — UI component governance
- Radix — accessibility primitives

## P1

- CrewAI — role/task/tool ownership and flow concepts
- Pydantic AI — typed specialist contracts
- OpenHands — coding runtime patterns
- Cline — coding harness and IDE/runtime boundary
- OSV-Scanner — dependency vulnerability reference
- Open Policy Agent — enterprise policy concepts
- Langfuse — LLM observability/evaluation concepts
- garak — LLM vulnerability scanning concepts

## P2

- Sigstore/Cosign — release provenance/signing
- Temporal — durable workflow reference if MyAgent later requires more complex distributed execution

Each upstream should receive a small audit entry containing:

```text
repo
license
why it matters
selected modules/patterns
adaptation boundary
patterns to avoid
integration risk
whether code may be reused or reference-only
```

---

# 23. Roadmap from Current State to Final Goal

## Phase A — Finish Single PlanStep Execution

Goal: prove one PlanStep can execute safely and durably from start to terminal reconciliation.

Tasks:

1. finish partial-start failure cleanup edge cases;
2. preserve original setup error when cancel cleanup also fails;
3. map successful coder session terminal event -> PlanStep COMPLETED;
4. map failed coder session -> PlanStep FAILED;
5. define cancellation semantics for a bound PlanStep;
6. recover/reconcile bound PlanSteps after restart;
7. run broad regression;
8. establish new full-suite baseline.

Exit criteria:

```text
one PlanStep
-> exactly one durable execution ownership relation
-> survives restart
-> reaches correct terminal state
-> leaves no orphan RUNNING session/step
```

## Phase B — Specialist Agent Foundation

Goal: prevent AI role confusion structurally.

Tasks:

1. add `AgentRole` / `AgentDefinition` domain model;
2. define role-specific capability/tool policies;
3. define typed input/output contracts;
4. separate Planner/Coder/Tester/Reviewer responsibilities;
5. add explicit handoff coordinator;
6. make each PlanStep declare/resolve its owning role;
7. keep model/runtime selection independent from role.

Initial roles:

```text
Planner
Coder
Tester
Reviewer
```

Then add:

```text
Researcher
Architect
UI Coder
Security Reviewer
```

Exit criteria:

- a role cannot call tools outside its capability set;
- a reviewer cannot write production code by default;
- a coder cannot complete its own durable step;
- planner output is typed and materialized only by control-plane services.

## Phase C — Deterministic Verification Gates

Goal: separate implementation from acceptance.

Tasks:

1. standardized test-result contract;
2. project-native test runner adapters;
3. Playwright verification path;
4. lint/type/build verification where configured;
5. reviewer gate consumes evidence;
6. acceptance/rework policy remains deterministic.

Exit criteria:

```text
Coder output != accepted work
```

Acceptance requires verification evidence.

## Phase D — Security Foundation

Goal: autonomous coding cannot bypass core security controls.

Tasks:

1. add security finding/result domain types;
2. Semgrep adapter;
3. Gitleaks adapter with redaction;
4. dependency vulnerability adapter using Trivy/OSV concepts;
5. SecurityReviewerAgent consumes deterministic findings;
6. security blocking policy;
7. adversarial agent evaluation using Promptfoo/garak concepts;
8. confirm sandbox/network/credential boundaries.

Exit criteria:

- secrets are not sent to model by default;
- critical deterministic findings can block acceptance;
- security reviewer cannot disable the scanners or policy gate.

## Phase E — UI Specialist and Design-System Governance

Goal: UI generation is consistent, reusable, and accessible.

Tasks:

1. discover/register project UI framework and component catalog;
2. define UI-system context contract;
3. UI Agent searches/reuses existing components;
4. Storybook integration where applicable;
5. accessibility verification;
6. Playwright visual/interaction checks;
7. structured `UiImplementationResult`.

Exit criteria:

- no arbitrary duplicate component creation when reusable components exist;
- UI agent obeys project design tokens/patterns;
- deterministic UI tests can reject the work.

## Phase F — Memory, Knowledge, and Context Engineering

Goal: each specialist receives the smallest useful context.

Tasks:

1. reinforce MemoryBackend vs KnowledgeBackend separation;
2. task/step scoped retrieval;
3. Code Graph context selection;
4. progressive retrieval;
5. context budgets;
6. role-specific context policies;
7. avoid passing whole repository/history by default.

Exit criteria:

- agents receive scoped relevant context;
- context provenance is inspectable;
- no hidden global mutable memory between agents.

## Phase G — Observability and Evaluation

Goal: measure the system without leaking sensitive content.

Tasks:

1. TelemetryBackend abstraction;
2. metadata-only tracing defaults;
3. role/model/runtime/tool timing;
4. execution cost metrics;
5. benchmark/evaluation harness;
6. compare routing strategies and agent policies;
7. failure taxonomy.

Exit criteria:

- execution can be explained from durable state + safe trace metadata;
- raw code/prompts are opt-in, not default telemetry.

## Phase H — Whole-Plan Execution Coordinator

Goal: safely sequence multiple PlanSteps.

Prerequisites:

- Phase A complete;
- role isolation complete;
- verification gates exist;
- restart recovery proven;
- retry/cost/time budgets defined.

Tasks:

1. choose next executable step;
2. resolve owning specialist role;
3. launch exactly one step;
4. wait for terminal outcome;
5. run test/security/review gates;
6. complete/fail/request rework;
7. optionally replan within bounded policy;
8. continue to next step only after control-plane decision.

Exit criteria:

- no uncontrolled agent-to-agent loops;
- no concurrent conflicting step ownership unless explicitly supported;
- bounded retries/replans;
- durable recovery after process restart.

## Phase I — Advanced Reliability and Cost Routing

Goal: operate across multiple providers/runtimes safely.

Tasks:

- model capability registry;
- role-to-model policy;
- cost/latency budgets;
- provider health and fallback;
- retry classification;
- rate-limit handling;
- sandbox capability routing;
- optional distributed execution.

Exit criteria:

- provider outage does not change agent responsibility;
- fallback is explicit and observable;
- durable lifecycle remains provider-independent.

## Phase J — Release / Supply Chain

Goal: controlled delivery after implementation is proven.

Potential tasks:

- artifact provenance;
- release approval workflow;
- signing/verification;
- dependency/SBOM report;
- changelog/documentation agent;
- deployment adapter behind explicit approval.

This phase is intentionally late. Coding agents should not receive unrestricted deployment authority during early development.

---

# 24. Final End-to-End Target

A mature MyAgent execution should look like:

```text
USER
 |
 v
TaskService creates durable Task
 |
 v
Planner Agent
 |
 v
validated PlanProposal
 |
 v
PlanService materializes + activates Plan
 |
 v
Execution Coordinator selects next PlanStep
 |
 v
Role Resolver chooses one owner
 |
 +--------------------------------------------+
 |                                            |
 v                                            v
Research / Architecture                 Specialist Coder
                                              |
                                              v
                                     isolated execution
                                              |
                                    typed ImplementationResult
                                              |
                                              v
                                           Test Agent
                                              |
                                  deterministic TestResult
                                              |
                                              v
                                       Security scanners
                                              |
                                      Security Reviewer
                                              |
                                              v
                                         Reviewer Agent
                                              |
                              APPROVE / REWORK / REJECT
                                              |
                                              v
                                  Execution Coordinator
                                              |
                       +----------------------+----------------------+
                       |                                             |
                       v                                             v
             PlanStep COMPLETED                           PlanStep FAILED/REWORK
                       |
                       v
                 next PlanStep
                       |
                       v
                Plan COMPLETED
                       |
                       v
                Task COMPLETED
```

At every point:

- durable state survives restart;
- agents cannot exceed their assigned role;
- tools are least-privilege;
- dangerous actions require explicit policy/approval;
- execution is isolated;
- deterministic evidence gates acceptance;
- telemetry is privacy-conscious;
- model/runtime/provider are replaceable;
- upstream implementations remain behind adapters;
- MyAgent retains ownership of orchestration and lifecycle.

---

# 25. What MyAgent Is Not

MyAgent is not intended to become:

- a wrapper around one provider SDK;
- one giant autonomous coding prompt;
- a loose chat room of agents;
- an MCP-only internal architecture;
- a UI-framework-specific backend;
- a cloud-vendor-specific runtime;
- a system that silently executes on the host when sandboxing fails;
- a system that uploads source/prompts/secrets as telemetry by default;
- a duplicate implementation of every upstream project;
- a second-source-of-truth workflow engine layered on top of its own durable stores.

---

# 26. Definition of Success

MyAgent reaches its intended final architecture when it can reliably demonstrate all of the following:

1. A user creates a durable software task.
2. A planner produces a typed, auditable plan.
3. Each PlanStep has exactly one primary execution owner.
4. Each specialist receives only permitted tools and scoped context.
5. A coder can modify code only inside its authorized workspace/sandbox policy.
6. Sensitive operations require policy approval.
7. Test, security, and review are independent acceptance gates.
8. Reviewer/Test/Security roles cannot silently rewrite production code.
9. A crash at any point can be recovered from durable Task/Plan/Step/Session state.
10. No runtime/model provider is embedded as the meaning of an agent role.
11. UI tasks follow the target project's design system and accessibility rules.
12. Security findings come from deterministic scanners where available, with AI used for interpretation rather than fabrication.
13. Secrets and sensitive content are not exposed to models or telemetry without explicit need.
14. Whole-plan execution is bounded by retry, time, cost, and replan budgets.
15. Upstreams can be replaced without rewriting MyAgent domain/control-plane architecture.
16. The system can explain who did what, with which role/model/runtime/tool, and why a durable lifecycle transition occurred.

That is the final architectural goal of MyAgent.

---

# 27. Immediate Resume Point

Current implementation should resume from **Phase A — Finish Single PlanStep Execution**.

The next concrete test should cover:

```text
shared coder-session setup fails
        +
orchestrator.cancel() also fails
        |
        v
original setup exception remains visible
        +
durable SessionState == FAILED
        +
PlanStep eventually becomes FAILED through coordinator handling
```

After that:

```text
Coder Session successful STOP
        -> bound PlanStep COMPLETED

Coder Session runtime failure
        -> bound PlanStep FAILED
```

Only after restart reconciliation and a new full-suite baseline should MyAgent begin the specialist-agent foundation and whole-plan execution work.
