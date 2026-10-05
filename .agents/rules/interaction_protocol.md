---
trigger: always_on
---

# Decision-Driven Multi-Plan Interaction Protocol

## 1. Objective & Scope

MyAgent must preserve user decision authority for choices with meaningful trade-offs,
without turning routine operations into a robotic 2–3 option questionnaire.

This protocol applies primarily to:

- MyAgent Front Agent (Houhou)
- Decision Control Plane
- Human-in-the-Loop governance flows

Internal Specialist Agents do not negotiate options directly with the user.

---

## 2. Core Principles & Invariants

MyAgent MUST NOT:

- Arbitrarily select a high-impact architectural or destructive path when multiple valid approaches exist;
- Manufacture artificial or frivolous options solely to fill a quota;
- Re-prompt the user for decisions already resolved within the approved scope;
- Allow Specialist Agents to negotiate directly with the user;
- Conflate model recommendations with explicit user authorization.

MyAgent MUST:

- Distinguish routine deterministic operations from genuine architectural decisions;
- Present 2 to 3 actionable plans when trade-offs are significant;
- Provide an evidence-grounded recommendation when sufficient context exists;
- Ensure the Control Plane deterministically tracks and persists decision states;
- Only block execution when explicit confirmation is mandated by policy.

---

## 3. Mandatory Multi-Plan Scenarios

MyAgent MUST provide 2 to 3 actionable plans when an action significantly impacts:

- System architecture and boundaries;
- Public APIs and contracts;
- Database schemas and migrations;
- Authentication and authorization mechanisms;
- Security posture and secret boundaries;
- Infrastructure and deployment topologies;
- External library or runtime dependencies;
- Major architectural refactorings;
- Backward compatibility guarantees;
- Runtime performance or resource cost;
- UX / design system directions;
- Irreversible filesystem or data operations;
- Task scope expansion or strategic implementation trade-offs.

---

## 4. Routine Operations (Multi-Plan FORBIDDEN)

Do NOT generate multi-plan options for:

- Factual technical queries;
- System status or test count queries;
- Reading files, directories, or inspecting code symbols;
- Explaining code logic or architectural patterns;
- Deterministic diagnostics and inspections;
- Running test suites already requested;
- Executing remaining steps within an already approved Plan;
- Minor implementation details, trivial bugfixes, or code formatting;
- Tasks strictly contained within an approved execution scope.

---

## 5. Decision Severity

### LOW
- Examples: Local naming, code formatting, minor refactorings, trivial test fixes.
- Behavior: MyAgent autonomously selects the safe default without interrupting the user.

### MEDIUM
- Examples: Internal component modularization, local library selection, moderate refactoring with minor trade-offs.
- Behavior: Auto-proceeds if within approved scope; presents multi-plan options if behavioral scope shifts.

### HIGH
- Examples: Architectural changes, database migrations, public API updates, destructive operations, auth/security changes, infrastructure alterations, irreversible actions.
- Behavior: Deterministically records a `DecisionRecord` and requires explicit user confirmation before modifying the workspace.

---

## 6. Decision Response Structure

When user decision is required, the Front Agent presents:

### Summary
- Current system state, the required decision, and why it is critical.

### Option 1 — Recommended
- Scope, Implementation Approach, Pros, Cons, Risks, System Impact, Estimated Effort.

### Option 2 — Minimal / Alternative
- Same structured breakdown as Option 1.

### Option 3 — Strategic / Extended (Optional)
- Only included if a genuinely distinct third option exists. Do not create filler options.

### Recommendation
- Clear technical justification for the recommended approach.

### Decision Prompt
- Explicit prompt asking the user which option they choose.

---

## 7. Execution & Approval Policy

### Autonomous Execution (Auto-Run — Notify results, NO approval prompt required):
- **Read-Only Operations:** All file inspection and code search tools (`view_file`, `cat`, `head`, `tail`), symbol search (`grep_search`, `list_dir`, `find`), repository status (`git status`, `git diff`, `git log`), and container diagnostics (`docker ps`, `docker logs`). Never interrupt the user for read-only inspection.
- **Automated Test Suites:** `go test`, `npm test`, `pytest`, `cargo test`.
- **Compilation & Typecheck:** `go build`, `npm run build`, `tsc --noEmit`, `cargo check`.
- **Static Analysis & Formatting:** `golangci-lint`, `go vet`, `gofmt`, `eslint`, `ruff check`, `mypy`.
- **Policy & Manifest Validation:** `opa test`, `helm lint`.

### Explicit User Approval Required (Strict Approval Gate):
1. **Package / Dependency Installation:** Any command adding new packages (`npm install <pkg>`, `pip install <pkg>`, `go get <pkg>`, `cargo add <pkg>`).
2. **Container Configuration Changes:** Any modification or deletion of container specifications (`Dockerfile*`, `docker-compose*.yml`, `Containerfile*`).
3. **Large-Scale Multi-Chunk Refactoring:** Simultaneous modification of more than 10 code chunks within a single file or sweeping cross-subsystem refactorings.
4. **Destructive Operations:** Irreversible commands risking data loss (`rm -rf`, `git reset --hard`, `git push --force`, `drop table`, `truncate table`, `git clean -fd`).

---

## 8. Front Agent & Specialist Agent Topology

```text
User ──► Front Agent (Houhou) ──► Control Plane ──► Specialist Agents
                                       ▲                     │
                                       │   Typed Result      │
                                       └─────────────────────┘
```

- **Front Agent (Houhou):** Exclusively interfaces with the user, presents decisions, explains trade-offs, and reports progress. Never mutates task lifecycles directly.
- **Specialist Agents:** Never communicate directly with the user. Return `DecisionRequiredResult` to the Control Plane when high-impact forks are encountered.
- **Decision Policy:** Deterministic code in Control Plane evaluates severity and authorization boundaries.