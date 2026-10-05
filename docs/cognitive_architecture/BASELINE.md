# BASELINE - MyAgent Cognitive Architecture

**Phase:** 00 - Baseline and Contract Freeze  
**Base Commit:** aa93381  
**Branch:** main  
**Date:** 2026-10-05  
**Test Baseline:** 696 passed, 0 failed, 0 errors (11.40s)

---

## 1. Repository Structure

```
my-agent/
├── agents/           # Specialist agent implementations (coder, model composition)
├── core/             # Domain models, services, protocols, coordinators
├── integrations/     # External adapters (SQLite backends, model backends, browser)
├── my_agent_mcp/     # MCP server (3301 lines) - public tool API
├── persistence/      # SQLite stores (per domain)
├── tools/            # Tool implementations (workspace, memory, coder)
├── eval/             # Evaluation harness
└── tests/            # Test suite (169 test files)
```

---

## 2. Test Baseline

| Metric | Value |
|--------|-------|
| Total tests | 696 |
| Passed | 696 |
| Failed | 0 |
| Errors | 0 |
| Duration | 11.40s |
| Command | python -m pytest tests/ --tb=no -q |
| Python | CPython 3.13 |

**Test files (169):** All located in `tests/test_*.py`.

---

## 3. SQLite Schema Inventory

**Database path:** `data/my_agent.db`

### 3.1 Tables

| Table | Primary Key | Purpose |
|-------|-------------|---------|
| projects | project_id | Project registry |
| tasks | task_id | Task lifecycle records |
| plans | plan_id | Multi-step plan records |
| plan_steps | step_id | Individual plan step records |
| checkpoints | checkpoint_id | Task/session recovery points |
| memories | memory_id | Persistent memory (L0-L3 tiers) |
| conversations | conversation_id | Conversation history |
| messages | (rowid) | Per-conversation messages |
| coder_sessions | session_id | CoderAgent session lifecycle |
| decisions | decision_id | Decision records (human-in-the-loop) |
| agent_runs | run_id | Durable execution ledger per agent run |
| code_nodes | node_id | Code graph symbol nodes (AST) |
| code_edges | (project_id, source_id, target_id, kind) | Code graph dependency edges |
| code_graph_files | (project_id, path) | Code graph file tracking |
| code_graph_status | project_id | Code graph sync status |
| app_state | key | Global key-value app state |
| code_nodes_fts | FTS5 virtual | Full-text search over code_nodes |

### 3.2 Notable Additive Migrations

- plan_steps.execution_session_id - added after initial schema
- plan_steps.assigned_role TEXT NOT NULL DEFAULT 'backend_coder' - added after initial schema
- decisions.plan_id - added after initial schema
- decisions.session_id - added after initial schema
- decisions.expires_at - added after initial schema (with partial index idx_decisions_state_expires)

### 3.3 Indexes

| Index | Table | Columns |
|-------|-------|---------|
| idx_tasks_project | tasks | (project_id, updated_at DESC) |
| idx_plans_task | plans | (task_id, revision ASC) |
| idx_plan_steps_plan | plan_steps | (plan_id, step_index ASC) |
| idx_checkpoints_project_task | checkpoints | (project_id, task_id, created_at DESC) |
| idx_memories_project | memories | (project_id, created_at) |
| idx_memories_lookup | memories | (project_id, kind, importance) |
| idx_coder_sessions_project | coder_sessions | (project_id, updated_at) |
| idx_decisions_task | decisions | (task_id, created_at DESC) |
| idx_decisions_state | decisions | (state, created_at DESC) |
| idx_decisions_state_expires | decisions | (state, expires_at) WHERE state='open' |
| idx_agent_runs_step | agent_runs | (step_id, created_at DESC) |
| idx_agent_runs_task | agent_runs | (task_id, created_at DESC) |
| idx_agent_runs_plan | agent_runs | (plan_id, created_at DESC) |
| idx_agent_runs_project | agent_runs | (project_id, created_at DESC) |
| idx_agent_runs_session | agent_runs | (session_id, created_at DESC) |
| idx_code_edges_source | code_edges | (project_id, source_id, kind) |
| idx_code_edges_target | code_edges | (project_id, target_id, kind) |
| idx_code_nodes_name | code_nodes | (project_id, name, kind) |

---

## 4. Public Services and Interfaces Inventory

### 4.1 MCP Public Tool API (my_agent_mcp/server.py)

| Category | Tools |
|----------|-------|
| System Status | my_agent_status |
| Project Lifecycle | list_projects, register_project, switch_project, resume_project, open_workspace |
| Task Lifecycle | create_task, start_task, get_active_task, list_tasks, cancel_task, complete_task |
| Plan Lifecycle | propose_task_plan, create_plan, list_plans, add_plan_step, activate_plan, start_plan_step, complete_plan_step, complete_plan |
| Plan Step Execution | start_plan_step_execution |
| Coder Session | start_coder_session, get_latest_resumable_coder_session, cancel_coder_session, handoff_coder_session, resume_coder_session, set_coder_execution_target, get_coder_history, publish_coder_event, next_coder_command, reconcile_coder_tool_result, resolve_coder_approval |
| Decision Subsystem | create_decision, get_decision, list_open_decisions, resolve_decision, cancel_decision |
| Memory | save_checkpoint, consolidate_memory, search_docs |
| Code Graph | sync_code_graph, code_graph_status, find_code_symbol, code_dependencies, code_dependents, impact_analysis, code_context |
| Agent Run / Handoff | start_agent_run, complete_agent_run, fail_agent_run, get_agent_run, list_agent_runs, get_agent_handoff_context, request_agent_handoff, get_allowed_handoff_targets, get_handoff_history |
| Verification | verify_step, run_whole_plan, verify_coverage, run_benchmark |
| Security | scan_security, run_red_team_assessment |
| UI Governance | generate_ui_design_profile, audit_ui_design, compare_visual_diff |
| PR Review | fetch_pr_review_threads, draft_review_reply, post_review_reply, post_pr_review_summary, post_batch_review_replies, submit_pr_review |
| Repository | get_repo_map, check_goal_drift |
| Release | generate_sbom, check_release_readiness, create_release_attestation |
| Prompt | inspect_prompt, rewrite_prompt |
| Code Hygiene | detect_circular_dependencies, detect_dead_code |
| PII | sanitize_pii |
| Browser | browser_navigate |
| Budget | get_task_budget, set_task_budget |
| Health | get_provider_health_status |
| Front Agent | front_agent_request |

### 4.2 Core Service Layer

| Service | File | Responsibility |
|---------|------|----------------|
| TaskService | core/task_service.py | Task CRUD + state machine |
| PlanService | core/plan_service.py | Plan/Step CRUD + state machine |
| DecisionService | core/decision_service.py | Decision lifecycle + TTL expiry |
| AgentRunService | core/agent_run_service.py | Durable execution ledger |
| PlanStepExecutionCoordinator | core/plan_step_execution_coordinator.py | Step to CoderSession binding |
| WholePlanCoordinator | core/whole_plan_coordinator.py | Automated multi-step execution loop |
| PlanningCoordinator | core/planning_coordinator.py | Plan decomposition orchestration |
| MyAgentControlPlane | core/control_plane.py | Layer 2 deterministic facade |
| FrontAgent | core/front_agent.py | User-facing interface layer |
| MemoryConsolidator | core/memory_consolidation.py | L0 to L3 memory promotion |
| HandoffCoordinator | core/handoff_coordinator.py | Cross-agent/runtime handoff |
| VerificationGateCoordinator | core/verification_gate_coordinator.py | Multi-gate verification |
| TestRunnerRegistry | core/test_runner.py | Pytest/npm/go test detection and execution |
| SecurityScannerRegistry | core/security_scanner.py | Security scanning |
| GoalDriftMonitor | core/goal_drift_monitor.py | Out-of-scope file detection |
| ErrorReflexionEngine | core/error_reflexion.py | Test/security failure diagnosis |
| PromptRewriter | core/prompt_rewriter.py | Prompt optimization |

### 4.3 Persistence Layer

| Store | File | Durability |
|-------|------|-----------|
| SQLiteProjectStore | persistence/sqlite_project_store.py | DURABLE |
| SQLiteTaskStore | persistence/sqlite_task_store.py | DURABLE |
| SQLitePlanStore | persistence/sqlite_plan_store.py | DURABLE |
| SQLiteCheckpointStore | persistence/sqlite_checkpoint_store.py | DURABLE |
| SQLiteMemoryStore | persistence/sqlite_memory_store.py | DURABLE |
| SQLiteDecisionStore | persistence/sqlite_decision_store.py | DURABLE |
| SQLiteAgentRunStore | persistence/sqlite_agent_run_store.py | DURABLE |
| SQLiteCoderSessionStore | persistence/sqlite_coder_session_store.py | DURABLE |
| SQLiteConversationStore | persistence/sqlite_conversation_store.py | DURABLE |
| SQLiteCodeGraphStore | persistence/sqlite_code_graph_store.py | DURABLE |
| SQLitePendingApprovalStore | persistence/sqlite_pending_approval_store.py | DURABLE |
| InMemoryPendingApprovalStore | core/control_plane.py | NON-DURABLE (default fallback) |

---

## 5. Mocked and Simulated Capabilities

### 5.1 MOCKED - WholePlanCoordinator default step executor

Class: _NoOpStepExecutor in core/whole_plan_coordinator.py

When WholePlanCoordinator is instantiated without a concrete StepExecutor,
it falls back to _NoOpStepExecutor which produces synthetic ImplementationResult
objects. It does NOT execute any real agent or modify the workspace.

Status: MOCKED - suitable for unit tests only.
Production requires injecting a real StepExecutor.

### 5.2 PARTIAL - InMemoryPendingApprovalStore

Class: InMemoryPendingApprovalStore in core/control_plane.py

Default approval store is in-memory (non-durable). Does not survive process restarts.
A SqlitePendingApprovalStore exists and should be injected in production.

Status: PARTIAL - durable store exists but injection requires explicit wiring.

### 5.3 PARTIAL - MemoryConsolidator (no backing store integration)

Class: MemoryConsolidator in core/memory_consolidation.py

The consolidation logic produces MemoryEntry objects with a DIFFERENT data model
from the persistence-layer MemoryRecord. MemoryConsolidator does NOT write to
SQLiteMemoryStore. Consolidation results are returned as plain Python objects
with no persistence path wired.

Status: PARTIAL - consolidation logic works in isolation; no production persistence path.

### 5.4 PARTIAL - Memory Search (LIKE-based, not semantic)

Class: SQLiteMemoryStore._search_sync in persistence/sqlite_memory_store.py

Memory search is implemented as SQL LIKE %query% substring match.
No vector embeddings, no semantic similarity.

Status: PARTIAL - functional keyword search; no semantic retrieval.

---

## 6. Memory Implementation

### 6.1 Primary Memory Model

File: core/memory.py
- MemoryLevel: L0, L1, L2, L3 (StrEnum)
- MemoryRecord: frozen dataclass - memory_id, project_id, level, kind, content, importance (float 0-1), metadata (Mapping), created_at

File: core/memory_consolidation.py
- MemoryLevel: L0_EPISODIC, L1_WORKING, L2_SEMANTIC, L3_LONG_TERM (separate StrEnum)
- MemoryEntry: mutable dataclass with recall_count, key, source
- MemoryConsolidator.consolidate_session(): maps L0 session messages to L1 working memory
- MemoryConsolidator.promote_memories(): promotes by recall_count threshold

WARNING: Two parallel MemoryLevel enumerations exist with incompatible string values:
- core/memory.py:MemoryLevel         -> values: "l0", "l1", "l2", "l3"
- core/memory_consolidation.py:MemoryLevel -> values: "L0_episodic", "L1_working", "L2_semantic", "L3_long_term"

These are NOT interchangeable. The SQLite store uses core/memory.py:MemoryLevel.

### 6.2 Memory Backend

File: integrations/sqlite_memory_backend.py wraps SQLiteMemoryStore

- SQLiteMemoryBackend.recall(context, query, limit) - LIKE-based search
- SQLiteMemoryBackend.capture(context, records) - batch insert

---

## 7. Task / Plan / Checkpoint APIs

### 7.1 TaskRecord (core/task.py)

```
TaskState: CREATED -> PLANNING -> EXECUTING -> COMPLETED/FAILED/CANCELLED
TaskRecord fields: task_id, project_id, objective, state, active_plan_id, created_at, updated_at
```

### 7.2 PlanRecord / PlanStepRecord (core/plan.py)

```
PlanState:     DRAFT -> ACTIVE -> COMPLETED/FAILED/SUPERSEDED
PlanStepState: PENDING -> RUNNING -> COMPLETED/FAILED/SKIPPED
PlanStepRecord fields: step_id, plan_id, step_index, title, instruction, state,
                       execution_session_id, assigned_role (AgentRole), created_at, updated_at
```

### 7.3 CheckpointRecord (core/checkpoint.py)

```
CheckpointRecord fields: checkpoint_id, project_id, summary, next_action,
                         conversation_id, session_id, task_id,
                         files_changed (tuple[str,...]), tests (tuple[str,...]), created_at
```

No state machine - checkpoints are immutable snapshots.

---

## 8. WholePlan Execution Path

File: core/whole_plan_coordinator.py

```
execute_whole_plan(plan_id, workspace_path)
  -> plan_service.activate_plan(plan_id)
  -> plan_store.list_steps(plan_id)
  -> for each PENDING step:
      plan_service.start_step(step_id)
      _run_gates_for_step(...)  [up to 1 + max_auto_repair_attempts times]
        -> step_executor.execute_step(step_id, ...)    <- REAL or NoOp
        -> goal_drift_monitor.evaluate_drift(...)
        -> test_runner_registry.run_tests(workspace_path, step_id)
        -> security_scanner_registry.scan_workspace(workspace_path, step_id)
        -> gate_coordinator.evaluate(...)
      if APPROVED: plan_service.complete_step(step_id)
      else:        plan_service.fail_step(step_id); break
  -> if all steps done: plan_service.complete_plan(plan_id)
                        task_service.complete_task(plan.task_id)
```

CRITICAL: step_executor defaults to _NoOpStepExecutor if not injected.
Production requires a real StepExecutor implementation.

---

## 9. Subsystem Dependency Map

```
FrontAgent (UI Layer)
    | FrontAgentRequest
MyAgentControlPlane (Control Plane)
    |-- TaskService -> SQLiteTaskStore
    |-- PlanService -> SQLitePlanStore
    |-- DecisionService -> SQLiteDecisionStore
    |-- AgentRunService -> SQLiteAgentRunStore
    |-- PlanningCoordinator -> (ModelBackend, PlanService)
    |-- PlanStepExecutionCoordinator -> (CoderAgentStack)
    +-- PendingApprovalStore (SQLite or InMemory)

WholePlanCoordinator (Autonomous loop)
    |-- StepExecutor (REAL: CoderRuntimeWorker | MOCKED: _NoOpStepExecutor)
    |-- TestRunnerRegistry (real subprocess: pytest/npm/go)
    |-- SecurityScannerRegistry
    |-- VerificationGateCoordinator
    +-- GoalDriftMonitor

Memory
    |-- SQLiteMemoryBackend -> SQLiteMemoryStore (durable, LIKE search)
    +-- MemoryConsolidator (in-memory only, no persistence wiring)

Code Graph
    +-- SQLiteCodeGraphStore (code_nodes, code_edges, code_graph_files, FTS5)

MCP Server (my_agent_mcp/server.py)
    +-- All services above, initialized at module load time
        DB: data/my_agent.db
```

---

## 10. AgentRole System

File: core/agent_role.py

Defined roles with active policy rules:
- PLANNER          - read-only, no file writes
- ARCHITECT        - read-only, code graph analysis
- RESEARCHER       - read-only, web search allowed
- BACKEND_CODER    - read + write, no destructive ops
- UI_CODER         - read + write, generate_image allowed
- TESTER           - read + run, no writes
- SECURITY_REVIEWER - read + run, no writes
- REVIEWER         - read + run, no writes

Defined roles with NO policy rules in RolePolicyEngine.ROLE_RULES:
- DB_MIGRATION     - NOT IMPLEMENTED (passes through without explicit allow/deny)
- PERFORMANCE      - NOT IMPLEMENTED (passes through without explicit allow/deny)
- DOCUMENTATION    - NOT IMPLEMENTED (passes through without explicit allow/deny)
- RELEASE          - NOT IMPLEMENTED (passes through without explicit allow/deny)
- USER_INTERFACE   - Front Agent role (no ROLE_RULES entry)

---

## 11. Mocked Capabilities Summary

| Capability | Status | Location | Notes |
|------------|--------|----------|-------|
| WholePlan step execution (default) | MOCKED | core/whole_plan_coordinator._NoOpStepExecutor | Synthetic results; no real agent runs |
| PendingApprovalStore (default) | PARTIAL | core/control_plane.InMemoryPendingApprovalStore | Non-durable; SQLite version exists |
| Memory consolidation persistence | PARTIAL | core/memory_consolidation.MemoryConsolidator | Returns objects; not written to DB |
| Memory search | PARTIAL | persistence/sqlite_memory_store.SQLiteMemoryStore | LIKE-based; no vector/semantic |
| DB_MIGRATION role policy | NOT IMPLEMENTED | core/agent_role.RolePolicyEngine | Enum value exists; no policy rules |
| PERFORMANCE role policy | NOT IMPLEMENTED | core/agent_role.RolePolicyEngine | Enum value exists; no policy rules |
| DOCUMENTATION role policy | NOT IMPLEMENTED | core/agent_role.RolePolicyEngine | Enum value exists; no policy rules |
| RELEASE role policy | NOT IMPLEMENTED | core/agent_role.RolePolicyEngine | Enum value exists; no policy rules |
