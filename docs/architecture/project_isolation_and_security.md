# Architecture & Security: Project & Workspace Isolation Governance

This document specifies the architecture, security authorization model, verification procedures, and operational guidelines for project and workspace isolation in MyAgent.

---

## 1. Core Security Principle & Invariants

```text
"An agent may forget context rather than retrieve unauthorized context.
Data isolation strictly takes precedence over continuity, convenience, speed, and token efficiency."
```

Invariants enforced across all specialist agents, tools, and MCP surfaces:
1. **Zero Cross-Project IDOR:** No tool caller may access, read, write, start, cancel, or complete tasks, plans, runs, decisions, checkpoints, or memories belonging to another project.
2. **Deterministic Workspace Binding:** The active project identity is strictly derived from the server process's verified filesystem workspace root (`_server_workspace_root`), never from unauthenticated client arguments or ambient global singletons.
3. **Filesystem Confinement:** All file reads, writes, searches, and AST scans are strictly confined within the resolved workspace root of the authorized project (`validate_path_containment`). Path traversal (`../`) and symlink escapes outside the boundary fail closed.
4. **Context & Semantic Alignment:** Prompt context assembled for an authorized project never includes private memories, preferences, or credentials belonging to or declaring association with a different project.

---

## 2. Multi-Tier Security Boundaries

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        APPLICATION-LEVEL LAYER                         │
│  - MCP Tools, Task/Plan Stores, Decision/Run Stores, Code Graph,       │
│    Memory L0-L3, User Model Engine, Context Builder.                   │
│  - Fail-Closed: Strict IDOR checks, tenant token attestation.          │
├────────────────────────────────────────────────────────────────────────┤
│                        FILESYSTEM-LEVEL LAYER                          │
│  - validate_path_containment() on all path parameters.                 │
│  - Realpath resolution, symlink boundary checks, traversal rejection.   │
├────────────────────────────────────────────────────────────────────────┤
│                        OS-LEVEL BOUNDARY & LIMITATIONS                 │
│  - Antigravity / Claude / Codex run under the host macOS user account.  │
│  - Application checks prevent tool-level leakage, but do not replace   │
│    kernel-level containerization (Docker/cgroups) for multi-tenancy.   │
└────────────────────────────────────────────────────────────────────────┘
```

### Remaining OS-Level Limitations
- **Shared OS User Identity:** In local development environments, all AI host processes run under the developer's macOS user account (`haohg`). Application-level checks prevent MCP tools from accessing unauthorized projects, but arbitrary shell execution outside MCP could read files accessible to the OS user.
- **Enterprise Multi-Tenant Prerequisite:** When hosting multiple untrusted customer repositories, workloads must be isolated into lightweight virtual machines or OCI containers (Docker / Firecracker / gVisor) with dedicated OS user accounts and separate SQLite database files.

---

## 3. Security Authorization Engine (`SecurityAuthorizationManager`)

Located in [`core/security_authorization.py`](file:///Users/haohg/Project/my-agent/core/security_authorization.py).

### Key Responsibilities
1. **`set_server_workspace(path)`:** Validates that the workspace is not a system root (`/`, `/private`, `/tmp`, `/System`, `/etc`, etc.) and sets the physical boundary.
2. **`authorize_project(project_id, required_scope)`:**
   - Resolves all registered projects whose `workspace_path` contains or matches the server workspace.
   - If `project_id` is passed, verifies that `project_id` matches the physical owner project. Any discrepancy raises `SecurityAuthorizationError(violation_type="CROSS_PROJECT_IDOR")`.
   - If `project_id` is omitted, defaults to the physically bound project.
   - If no project matches, fails closed with `UNAUTHORIZED_WORKSPACE`. Global fallback to `app_state.active_project_id` is disabled.
3. **`validate_path_containment(target_path, project)`:** Resolves `target_path` and `project.workspace_path` via `.expanduser().resolve()`. If `target_path` is not inside or equal to the workspace root, raises `PATH_TRAVERSAL` violation.
4. **`validate_global_memory_payload(content)`:** Rejects project-specific paths (`/Users/.../Project/...`), customer identifiers, or private keys before writing to global L3 memory.

---

## 4. Semantic Alignment & Checkpoint Consistency

### User Preferences Semantic Alignment
1. **Write-time (`set_user_preference`):** When storing a preference with `scope="project"`, the content is checked for declarations of association with other projects (e.g. `gắn với dự án <other>` or `for project <other>`). Storing credentials or settings targeting another project is rejected with `ProjectSemanticMismatchError`.
2. **Context-time (`_retrieve_user_preferences` in `UnifiedContextBuilder`):** When assembling context for `project_id`, any preference item explicitly declaring association with another project is filtered out and audited.

### Checkpoint Lifecycle Consistency
1. **Project vs Task Scoping:**
   - **Project-Level Checkpoint (`task_id=None`):** Captures repository architecture, baseline, or release milestone. Created when `task_id=""` or no active task is bound.
   - **Task-Level Checkpoint (`task_id="task-xxx"`):** Captures progress, changed files, and tests for a specific task. Foreign `task_id` values trigger `TASK_IDOR_VIOLATION`.
2. **Metadata Linkage:** Calling `save_checkpoint` automatically updates `project.last_checkpoint_id` in the `projects` store so metadata returned by `resume_project` and `_project_payload` is synchronized.
3. **Fallback Recovery:** If an active task does not yet have a task-specific checkpoint, `resume_project` falls back to the latest project-level checkpoint.

---

## 5. Security Verification & Test Commands

### 1. Full Regression Suite under Default Resource Limits
Verifies that all SQLite connections are cleanly closed via context managers and zero file descriptors leak under macOS default `ulimit -n 256`:
```bash
cd /Users/haohg/Project/my-agent
bash -c "ulimit -n 256 && .venv/bin/python -m pytest"
```
*Expected:* 1,105+ passed, 0 failures, 0 resource leaks.

### 2. Dedicated Security Test Suite
Tests all boundary scenarios (IDOR, path traversal, global memory, SQLite inspector safety, UAT reconciliation):
```bash
cd /Users/haohg/Project/my-agent
.venv/bin/python -m pytest tests/security/
```
*Expected:* 33 passed in ~6 seconds.

### 3. Multi-Process Stdio JSON-RPC Subprocess Verification
Launches two independent MCP server subprocesses sharing a single SQLite database and verifies complete inter-process isolation across 11 scenarios:
```bash
cd /Users/haohg/Project/my-agent
.venv/bin/python scripts/verify_two_mcp_subprocesses.py
```
*Expected:* `Overall Result: ALL SCENARIOS PASSED (100%)`.

---

## 6. Safe Operational Guidelines

1. **MCP Inspector Port Allocation:**
   - Always run the MCP Inspector on dedicated ports `18674` (Client) and `18675` (Sandbox) to prevent port collisions with web application development servers:
     ```bash
     bash scripts/launch_mcp_inspector.sh
     ```
2. **Isolated SQLite Snapshot:**
   - Write testing in MCP Inspector must use an ephemeral SQLite snapshot created via `core/inspector_db_safety.py`. Never point `MYAGENT_DB_PATH` to production `data/my_agent.db` during manual or automated penetration testing.
3. **Git Governance:**
   - Work strictly on dedicated feature branches (`feat/...` or `fix/...`).
   - Stop execution at `AWAITING_USER_LOCAL_ACCEPTANCE` prior to any Git commit or push.
   - Strictly require explicit user authorization before any commit, push, PR, or merge.
