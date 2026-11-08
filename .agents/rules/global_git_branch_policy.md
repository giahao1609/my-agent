# Global Git Branch & Development Governance Policy

## 1. Objective & Universal Scope
This policy enforces strict development lifecycle discipline across all software projects (including MyAgent, Teachora AI, AI Protein Design System, VietRAG, and all current and future repositories) and across all AI execution hosts (Google Antigravity, OpenAI Codex, Anthropic Claude Code, Houhou Front Agent, and internal Specialist Agents).

---

## 2. Mandatory Rules

### 2.1 Project Auto-Discovery
Before implementing any net-new feature, refactoring, or code change:
1. Identify the actual physical repository path and Git root (`git rev-parse --show-toplevel`).
2. Read the local project structure, documentation, and existing conventions.
3. Inspect `git status`, current branch, HEAD commit, and uncommitted working tree changes.
4. Strictly forbidden from assuming the current project is `my-agent`.
5. Strictly forbidden from using hard-coded paths or static branch names across projects.

### 2.2 Mandatory Feature Branch
Before modifying any source code for implementing a feature:
1. Create and checkout a dedicated feature branch matching the task scope (`feat/<scope>-<task-description>`).
2. Strictly prohibited from developing directly on `main`, `master`, or protected production branches.
3. Strictly prohibited from running `git reset --hard`, `git clean -fd`, `git stash drop`, or silently overwriting existing changes in the working tree.
4. Strictly prohibited from auto-merging into `main` without explicit human review and approval.
5. Read-only audits, repository inspections, and diagnostics do not require creating a branch if no files are modified.

### 2.3 Standard 8-Stage Development Lifecycle (with Mandatory User Local Acceptance Gate)
Every agent must execute in accordance with the standard lifecycle:
```text
Project Discovery ──► Git Audit ──► Feature Branch Checkout ──► Implementation
                                                                      │
Approved Merge ◄── Strict Git Approval ◄── USER LOCAL ACCEPTANCE ◄── Local Server
                                          (Awaiting User UAT)     (Testing)
```
1. **Project Discovery:** Auto-detect repository root and active workspace context. Never assume repository is `my-agent`.
2. **Git Audit:** Inspect git branch, HEAD, uncommitted diffs, and branch naming style.
3. **Feature Branch Checkout:** Branch off from current base branch to a dedicated feature branch.
4. **Implementation:** Edit code, respecting architecture and domain boundaries.
5. **Automated Testing:** Run automated unit/integration suites; evaluate Verification Gate.
6. **Local Server Verification:** Spin up frontend/backend/API locally, confirm accessibility, provide exact localhost URL/port and test instructions.
7. **Mandatory User Local Acceptance Gate (UAT):** Stop execution at `AWAITING_USER_LOCAL_ACCEPTANCE`. Never auto-transition to `APPROVED`. The user directly tests UI, API, and features.
8. **Strict Git Approval Gate:** Strictly prohibited from auto-commit, auto-push, auto-PR, auto-merge, or auto-deploy without explicit user confirmation and granted Git scope.

### 2.4 Fundamental Invariants
- `AUTOMATED TEST PASS != USER ACCEPTANCE.`
- `LOCAL UI/API MUST BE AVAILABLE FOR USER TESTING.`
- `NO USER ACCEPTANCE -> NO COMMIT / PUSH.`
- `NO EXPLICIT GIT APPROVAL -> NO PR / MERGE / DEPLOY.`

### 2.5 Standard Branch Naming Convention (`<type>/<scope>-<description>`)
Before creating any branch, the agent must check existing conventions and format the name as:
`<type>/<scope>-<description>`

Allowed types:
- `feat/`: New feature implementation
- `fix/`: Bug fix or defect resolution
- `refactor/`: Code reorganization without functional behavior change
- `docs/`: Technical documentation, ADRs, or reports
- `test/`: Test suite additions, hardening, or verification scripts
- `chore/`: Configuration, maintenance, dependency updates
- `revert/`: Reverting a previous commit or PR

Formatting Invariants:
1. Use only lowercase alphanumeric characters and hyphens (`[a-z0-9-]`).
2. Strictly prohibited from using spaces, underscores, uppercase characters, or shell metacharacters.
3. Strictly prohibited from generic names (e.g., `feat/new-feature`, `fix/fix-bug`, `chore/update`).
4. Do not include arbitrary phase numbers unless explicitly mandated by the project's public roadmap.
5. Never create multiple branches with different names for the same task scope.
6. Strictly prohibited from renaming, deleting, or force-pushing existing branches in the repository.

---

## 3. Enforcement & Memory Policy
- This policy is persisted in L3 Long-Term Memory (`mem-l3-global-git-branch-policy`, `mem-l3-global-git-branch-naming-convention`, `mem-l3-user-local-acceptance-gate`).
- Loaded across Antigravity (`.agents/rules/*.md`), Codex (`AGENTS.md`), and Claude Code (`CLAUDE.md -> @AGENTS.md`).
- Any specialist agent failing to checkout a feature branch before mutating source code violates the Execution Integrity contract.
