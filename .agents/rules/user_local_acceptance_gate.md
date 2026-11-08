# Mandatory User Local Acceptance Gate & Strict Git Approval Policy

## 1. Core Objective & Fundamental Invariants
Automated tests, lint checks, compilation, and smoke tests are necessary baseline verifications, but they **NEVER constitute user acceptance**.

The following invariants are strictly enforced across ALL projects and ALL AI agents:
1. `AUTOMATED TEST PASS != USER ACCEPTANCE.`
2. `LOCAL UI/API MUST BE AVAILABLE FOR USER TESTING.`
3. `NO USER ACCEPTANCE -> NO COMMIT / PUSH.`
4. `NO EXPLICIT GIT APPROVAL -> NO PR / MERGE / DEPLOY.`

---

## 2. Standard 8-Stage Development Lifecycle (Updated with UAT Gate)

```text
Project Discovery ──► Git Audit ──► Feature Branch Checkout ──► Implementation
                                                                      │
Approved Merge ◄── Strict Git Approval ◄── USER LOCAL ACCEPTANCE ◄── Local Server
                                          (Awaiting User UAT)     (Testing)
```

1. **Stage 1 — Project Discovery:** Auto-detect actual repository root (`git rev-parse --show-toplevel`). Never assume repository is `my-agent`.
2. **Stage 2 — Git Audit:** Check `git status -sb`, branch conventions, uncommitted working tree state.
3. **Stage 3 — Dedicated Feature Branch Checkout:** Branch off from current base branch (`feat/<task-name>`). Never develop directly on `main` or protected branches.
4. **Stage 4 — Implementation:** Execute localized edits respecting architecture boundaries.
5. **Stage 5 — Automated Testing:** Execute unit, integration, and lint tests.
6. **Stage 6 — Local Runtime Verification (Server Startup):**
   - Spin up the local frontend, backend, or API service.
   - Confirm application is actually running and accessible.
   - Provide exact localhost URL and port (e.g., `http://localhost:3000`, `http://127.0.0.1:8000`).
   - Provide test credentials, mock data, and functional checklist.
   - Keep the local server process running for user inspection.
7. **Stage 7 — Mandatory User Local Acceptance Gate (UAT):**
   - The agent MUST STOP execution.
   - Transition state to: `AWAITING_USER_LOCAL_ACCEPTANCE`.
   - Never auto-transition state to `APPROVED`.
   - The user directly tests UI, interactions, APIs, and evaluates data flow.
   - If bugs are reported: Agent fixes on the feature branch, restarts local runtime, and resubmits for UAT.
8. **Stage 8 — Strict Git Approval & Merge Gate:**
   - User acceptance of UI/API DOES NOT authorize automatic Git push or merge.
   - Each Git action (commit, push, PR, merge) requires explicit human permission.
   - Strictly forbidden from auto-commit, auto-push, auto-PR, auto-merge, or auto-deploy.

---

## 3. Universal Applicability
- **Projects:** MyAgent, Teachora AI, AI Protein Design System, VietRAG, and all current and future repositories.
- **AI Agents / Hosts:** Google Antigravity, OpenAI Codex, Anthropic Claude Code, Houhou Front Agent, and all internal Specialist Agents.
- **Persistence:** Stored in L3 Long-Term Memory (`mem-l3-user-local-acceptance-gate`) in `data/my_agent.db`.
