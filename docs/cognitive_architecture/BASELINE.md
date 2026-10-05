# BASELINE - MyAgent Cognitive Architecture
# Phase 00 - Baseline and Contract Freeze

---

## BASE_COMMIT

```
aa93381
feat: initial commit for my-agent architecture and core system
Branch: main (origin/main, origin/HEAD)
```

---

## FINAL_COMMIT

```
20f0822
docs(phase-00): baseline freeze - schema, contracts, and capability audit
Branch: feat/houhou-00-baseline-freeze
```

No runtime code was changed. Only documentation files were added.

---

## CURRENT TEST BASELINE

```
Command:  python -m pytest tests/ --tb=no -q
Date:     2026-10-05
Python:   CPython 3.13
Venv:     .venv

Result:   696 passed in 11.40s
Failed:   0
Errors:   0
Warnings: 0
```

Test files: 169 files under tests/test_*.py

Confirmed twice:
- Run 1 (pre-documentation): 696 passed in 11.40s
- Run 2 (post-documentation): 696 passed in 10.99s

---

## CURRENT DATABASE SCHEMA

Database: data/my_agent.db
Engine:   SQLite (WAL journal mode)

### Tables

| Table | Primary Key | Description |
|-------|-------------|-------------|
| projects | project_id | Project registry |
| tasks | task_id | Task lifecycle records |
| plans | plan_id | Multi-step plan records |
| plan_steps | step_id | Individual plan step records |
| checkpoints | checkpoint_id | Task/session recovery points |
| memories | memory_id | Persistent memory (L0-L3 tiers) |
| conversations | conversation_id | Conversation history |
| messages | (rowid) | Per-conversation messages |
| coder_sessions | session_id | CoderAgent session lifecycle |
| decisions | decision_id | Decision records (HITL gate) |
| agent_runs | run_id | Durable execution ledger |
| code_nodes | node_id | Code graph AST symbol nodes |
| code_edges | (project_id, source_id, target_id, kind) | Code graph dependency edges |
| code_graph_files | (project_id, path) | Code graph file tracking |
| code_graph_status | project_id | Code graph sync status |
| app_state | key | Global key-value app state |
| code_nodes_fts | (FTS5 virtual) | Full-text search over code_nodes |

### Key Column Details

tasks:
  task_id TEXT PK, project_id TEXT, objective TEXT, state TEXT,
  active_plan_id TEXT, created_at TEXT, updated_at TEXT

plans:
  plan_id TEXT PK, task_id TEXT, revision INTEGER, state TEXT,
  created_at TEXT, updated_at TEXT

plan_steps:
  step_id TEXT PK, plan_id TEXT, step_index INTEGER, title TEXT,
  instruction TEXT, state TEXT, created_at TEXT, updated_at TEXT,
  execution_session_id TEXT,                     -- additive migration
  assigned_role TEXT NOT NULL DEFAULT 'backend_coder'  -- additive migration

decisions:
  decision_id TEXT PK, task_id TEXT, step_id TEXT, severity TEXT,
  state TEXT, prompt TEXT, options_json TEXT, selected_option_id TEXT,
  rationale TEXT, created_at TEXT, resolved_at TEXT,
  plan_id TEXT,      -- additive migration
  session_id TEXT,   -- additive migration
  expires_at TEXT    -- additive migration

checkpoints:
  checkpoint_id TEXT PK, project_id TEXT, task_id TEXT, summary TEXT,
  next_action TEXT, conversation_id TEXT, session_id TEXT,
  files_changed_json TEXT, tests_json TEXT, created_at TEXT

memories:
  memory_id TEXT PK, project_id TEXT, level TEXT, kind TEXT,
  content TEXT, importance REAL, metadata_json TEXT, created_at TEXT

coder_sessions:
  session_id TEXT PK, workspace_id TEXT, user_id TEXT, agent_id TEXT,
  context_session_id TEXT, task_id TEXT, project_id TEXT,
  runtime_id TEXT, model_id TEXT, state TEXT, created_at TEXT, updated_at TEXT

agent_runs:
  run_id TEXT PK, project_id TEXT, task_id TEXT, plan_id TEXT, step_id TEXT,
  agent_id TEXT, execution_role TEXT, model_id TEXT, runtime_id TEXT,
  session_id TEXT, state TEXT, started_at TEXT, finished_at TEXT,
  created_at TEXT, updated_at TEXT, result_json TEXT, metadata_json TEXT

---

## CURRENT PUBLIC CONTRACTS

### Task Domain (core/task.py)

```
TaskState:    CREATED | PLANNING | EXECUTING | COMPLETED | FAILED | CANCELLED
Transitions:  CREATED -> PLANNING, FAILED, CANCELLED
              PLANNING -> EXECUTING, FAILED, CANCELLED
              EXECUTING -> PLANNING, COMPLETED, FAILED, CANCELLED
              COMPLETED, FAILED, CANCELLED -> terminal (no further transitions)
TaskRecord:   task_id, project_id, objective, state, active_plan_id, created_at, updated_at
```

### Plan Domain (core/plan.py)

```
PlanState:     DRAFT | ACTIVE | COMPLETED | FAILED | SUPERSEDED
Transitions:   DRAFT -> ACTIVE, SUPERSEDED
               ACTIVE -> COMPLETED, FAILED, SUPERSEDED
               COMPLETED, FAILED, SUPERSEDED -> terminal

PlanStepState: PENDING | RUNNING | COMPLETED | FAILED | SKIPPED
Transitions:   PENDING -> RUNNING, SKIPPED
               RUNNING -> COMPLETED, FAILED
               COMPLETED, FAILED, SKIPPED -> terminal

PlanStepRecord: step_id, plan_id, step_index, title, instruction, state,
                execution_session_id, assigned_role (AgentRole), created_at, updated_at
```

### Checkpoint Domain (core/checkpoint.py)

```
CheckpointRecord: checkpoint_id, project_id, summary, next_action,
                  conversation_id, session_id, task_id,
                  files_changed (tuple[str,...]), tests (tuple[str,...]), created_at
Note: Immutable snapshot - no state machine
```

### Decision Domain (core/decision.py)

```
DecisionSeverity: LOW | MEDIUM | HIGH
DecisionState:    OPEN | RESOLVED | CANCELLED | EXPIRED
TTL:              LOW=24h, MEDIUM=72h, HIGH=never-auto-expire
DecisionRecord:   decision_id, task_id, step_id, severity, state, prompt,
                  options (tuple[DecisionOption,...]), selected_option_id, rationale,
                  created_at, resolved_at, plan_id, session_id, expires_at
```

### Memory Domain (core/memory.py)

```
MemoryLevel:  L0 | L1 | L2 | L3  (string values: "l0", "l1", "l2", "l3")
MemoryRecord: memory_id, project_id, level, kind, content,
              importance (float 0-1), metadata (Mapping), created_at
```

### Session Domain (core/session.py)

```
SessionState: CREATED | STARTING | RUNNING | WAITING_APPROVAL | CANCELLING | STOPPED | FAILED
SessionRecord: session_id, context (ExecutionContext), runtime_id, model_id, state
```

### Agent Execution (core/protocols.py, core/handoff_contracts.py)

```
AgentRuntime Protocol:
  start(context) -> str
  resume(session_id, context) -> None
  send(session_id, message, context, retrieval_context) -> None
  set_execution_target(session_id, runtime_id, model_id, history) -> None
  cancel(session_id) -> None
  submit_tool_result(session_id, tool_call_id, result, context) -> None
  stream_events(session_id) -> AsyncIterator[AgentEvent]
  capabilities() -> Sequence[CapabilityStatus]

StepExecutor Protocol (core/whole_plan_coordinator.py):
  execute_step(step_id, step_title, step_role, repair_hint) -> ImplementationResult

ImplementationResult: step_id, summary, modified_files, created_files, deleted_files, success
ReviewStatus: APPROVED | REJECTED | REQUEST_REWORK

AgentRole enum (core/agent_role.py):
  planner, architect, researcher, backend_coder, ui_coder,
  tester, security_reviewer, reviewer, db_migration,
  performance, documentation, release, user_interface
```

### MCP Public Tool API (my_agent_mcp/server.py - 3301 lines)

Categories (see contracts/agent_execution.md for full list):
  Project, Task, Plan, Coder Session, Decision, Memory, Code Graph,
  Agent Run, Handoff, Verification, Security, UI Governance, PR Review,
  Release, Prompt, Budget, Health, Front Agent

---

## KNOWN MOCKS

| Capability | Status | Class / Location | Evidence |
|------------|--------|-----------------|---------|
| PlaywrightBrowserAdapter / PlaywrightBrowserSession | MOCKED | `integrations/playwright_browser.py` | No Playwright runtime or headless browser imported; writes hardcoded 1x1 PNG header bytes (`b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"`) as fake screenshots; returns simulated navigation/click/fill strings (`"Navigated to ... successfully."`); claims READY status without a browser engine |
| ContainerSandboxBackend | MOCKED | `integrations/container_sandbox_backend.py` | No Docker, Podman, or container runtime; maintains state in-memory dicts; `exec()` returns simulated string `"Simulated exec output for: {' '.join(argv)}"`; claims container isolation, egress security, and snapshot/rollback without container engine |
| WholePlan step execution | MOCKED | `core/whole_plan_coordinator._NoOpStepExecutor` | Returns synthetic ImplementationResult (`"[NoOpStepExecutor] Simulated execution of step ..."` ); fallback step executor used when no real agent runtime is injected |
| PendingApprovalStore (default) | PARTIAL | `core/control_plane.InMemoryPendingApprovalStore` | dict in-process; lost on restart; SQLitePendingApprovalStore exists but not injected by default |
| Memory consolidation persistence | PARTIAL | `core/memory_consolidation.MemoryConsolidator` | `consolidate_session()` returns MemoryEntry objects; not written to SQLiteMemoryStore |
| Memory search | PARTIAL | `persistence/sqlite_memory_store.SQLiteMemoryStore` | LIKE %query% substring match; no embeddings, no vector similarity |
| DB_MIGRATION role policy | NOT IMPLEMENTED | `core/agent_role.RolePolicyEngine` | Role enum exists; zero policy rules in ROLE_RULES dict |
| PERFORMANCE role policy | NOT IMPLEMENTED | `core/agent_role.RolePolicyEngine` | Role enum exists; zero policy rules in ROLE_RULES dict |
| DOCUMENTATION role policy | NOT IMPLEMENTED | `core/agent_role.RolePolicyEngine` | Role enum exists; zero policy rules in ROLE_RULES dict |
| RELEASE role policy | NOT IMPLEMENTED | `core/agent_role.RolePolicyEngine` | Role enum exists; zero policy rules in ROLE_RULES dict |

Confirmed REAL (not mocked):

| Capability | Evidence |
|------------|---------|
| LocalSandboxBackend | Real local process execution via `asyncio.create_subprocess_exec` in `integrations/local_sandbox_backend.py` |
| VisualDiffValidator | Real PNG header and zlib IDAT chunk decompression with pixel-level comparison in `core/visual_diff.py` |
| SQLiteTaskStore / PlanStore / CheckpointStore / MemoryStore / DecisionStore | Real SQLite reads/writes, tested in test suite |
| TestRunnerRegistry (pytest/npm/go) | Real asyncio.create_subprocess_exec calls |
| DecisionService + TTL expiry | Full lifecycle tested |
| CoderSession durable lifecycle | SQLite backed coder_sessions table |
| AgentRunService execution ledger | SQLite backed agent_runs table |
| SecurityScannerRegistry | Regex-based real scanning |

---

## KNOWN EXISTING FAILURES

None.

All 696 tests pass at base commit aa93381.
No pre-existing failures were found before this phase began.

Pre-flight test run output:
  696 passed in 11.40s  (exit code 0)

Post-documentation test run output:
  696 passed in 10.99s  (exit code 0)

---

## FILES CREATED

All new files are documentation only. No source files were modified.

```
docs/cognitive_architecture/BASELINE.md          (this file)
docs/cognitive_architecture/contracts/task.md
docs/cognitive_architecture/contracts/plan.md
docs/cognitive_architecture/contracts/checkpoint.md
docs/cognitive_architecture/contracts/agent_execution.md
docs/cognitive_architecture/contracts/workspace_tools.md
docs/cognitive_architecture/contracts/approvals.md
docs/cognitive_architecture/contracts/persistence.md
docs/handoffs/PHASE_00_HANDOFF.md
```

Committed at: 20f0822  
Branch: feat/houhou-00-baseline-freeze

---

## NO RUNTIME BEHAVIOR CHANGED

CONFIRMED.

Files changed by this phase:
- 0 files in core/
- 0 files in agents/
- 0 files in integrations/
- 0 files in persistence/
- 0 files in tools/
- 0 files in my_agent_mcp/
- 0 files in eval/
- 0 files in tests/

Only new untracked documentation files were added under docs/.
No existing file was modified.
Test suite result is identical before and after this phase (696 passed).
