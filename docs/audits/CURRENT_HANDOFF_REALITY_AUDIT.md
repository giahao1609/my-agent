# PHASE 04.5H-A — CURRENT HANDOFF REALITY AUDIT

**Author:** Houhou (Front Agent of MyAgent)  
**Date:** 2026-10-07  
**Branch:** `audit/houhou-04-5h-handoff-continuity`  
**Base Commit / Current HEAD:** `d3f785051820623349fa84fda74d41fc849d01c5`  
**Status:** AUDIT_COMPLETE (Discovery / Verification Only)

---

## 1. EXECUTIVE_SUMMARY

This audit establishes the empirical reality of the Handoff and Continuity subsystems currently implemented in MyAgent. The investigation analyzed source code across `core/`, `agents/`, `persistence/`, `integrations/`, and `my_agent_mcp/`, cross-referencing against 880 passing unit/integration tests and historical commit records.

### Key Architectural Finding: Three Distinct Subsystems
MyAgent currently possesses **three parallel, largely unintegrated handoff/continuity subsystems**:

1. **Role-to-Role Specialist Handoff (`core/handoff_coordinator.py`, `core/handoff_contracts.py`):**
   - Coordinates handoffs between specialized functional roles (Planner, Architect, Backend Coder, UI Coder, Tester, Security Reviewer, Reviewer, Release).
   - Enforces `HandoffTransitionMatrix`, prompt injection inspection, string truncation, and secret redaction.
   - **Persistence Reality:** In the MCP server runtime (`my_agent_mcp/server.py`), `HandoffCoordinator` is initialized with `history_store=None`. All handoff decisions are cached in-memory (`self._history: list[HandoffDecision]`) and **do NOT survive process restart**.

2. **Durable Cross-Agent Execution Ledger (`core/agent_handoff_context.py`, `core/agent_handoff_message.py`):**
   - Backed by durable SQLite storage (`persistence/sqlite_agent_run_store.py`, `agent_runs` table).
   - Collects machine-readable `AgentWorkResult` records across plan steps.
   - Constructs bounded, pruned handoff contexts (`AgentHandoffContext`): capped at 15 files, 3 test suites, 5 findings, 3 notes.
   - Exposed via MCP tool `get_agent_handoff_context` and projected as Markdown summaries via `AgentHandoffMessage` during `complete_agent_run`.
   - **Persistence Reality:** 100% durable in SQLite.

3. **Coder Session & Runtime Continuity (`agents/coder_stack.py`, `core/orchestrator.py`, `my_agent_mcp/server.py`):**
   - Implements session state persistence (`persistence/sqlite_coder_session_store.py`, `coder_sessions` table) and conversation message persistence (`persistence/sqlite_conversation_store.py`, `messages` table).
   - Exposed via MCP tools `handoff_coder_session`, `resume_coder_session`, and `set_coder_execution_target`.
   - **Persistence Reality:** Session state and messages survive process and machine restarts.
   - **Critical Vulnerability / Context Bloat:** Resuming a coder session calls `conversations.history(project_id, session_id, limit=None)`. It pushes **UNBOUNDED raw message history** to the active model via `stack.orchestrator.set_execution_target(..., history=durable_history)`. It does NOT consult `AgentHandoffContextBuilder` or compact earlier phases.

---

## 2. COMPONENT_INVENTORY

The table below lists every concrete handoff/continuity component identified in the codebase, its role, callers, persistence model, test status, and active classification.

| Component | File | Role | Currently Used | Callers | Persistence | Tested | Status | Notes |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| `HandoffCoordinator` | `core/handoff_coordinator.py` | Role-to-role delegation coordinator | YES | `my_agent_mcp/server.py` (`request_agent_handoff`), tests | Ephemeral (MCP server passes `history_store=None`) | YES (`test_handoff_coordinator.py`) | PARTIAL | Has `_HandoffHistoryStore` protocol, but not wired to SQLite in production server |
| `HandoffTransitionMatrix` | `core/handoff_coordinator.py` | Static role transition whitelist | YES | `HandoffCoordinator` | Ephemeral (Static) | YES (`test_handoff_coordinator.py`) | ACTIVE | Prevents unauthorized jumps (e.g. Coder -> Release) |
| `HandoffGuardrailEngine` | `core/handoff_coordinator.py` | Transition, prompt injection, secret sanitization, payload pruning | YES | `HandoffCoordinator` | Ephemeral | YES (`test_handoff_coordinator.py`, `test_handoff_pruning_and_hooks.py`) | ACTIVE | Prunes `history` > 15 items and strings > 5000 chars |
| `HandoffRequest` / `HandoffDecision` | `core/handoff_coordinator.py` | Data contracts for handoff requests and decisions | YES | `HandoffCoordinator`, `server.py` | Ephemeral | YES | ACTIVE | Typed dataclasses |
| `HandoffContracts` (`ImplementationResult`, `ArchitectureProposal`, etc.) | `core/handoff_contracts.py` | Typed domain contracts for specialist outputs | YES | `whole_plan_coordinator.py`, `control_plane.py`, `agent_work_result.py` | Ephemeral (serialized to JSON in stores) | YES (`test_handoff_contracts.py`, `test_typed_handoff.py`) | ACTIVE | Carries `is_mocked` and `execution_evidence` |
| `AgentHandoffContextBuilder` | `core/agent_handoff_context.py` | Reconstructs bounded handoff context from prior runs | YES | `my_agent_mcp/server.py` (`get_agent_handoff_context`), tests | Persistent (reads SQLite `agent_runs`) | YES (`test_agent_handoff_context.py`, `test_e2e_antigravity_to_codex_handoff.py`) | ACTIVE | Prunes to 15 files, 3 tests, 5 findings, 3 notes |
| `AgentHandoffContext` | `core/agent_handoff_context.py` | Aggregated handoff context dataclass | YES | `AgentHandoffContextBuilder`, `server.py` | Ephemeral (generated on-demand) | YES | ACTIVE | Generates Markdown brief via `format_prompt_context()` |
| `AgentHandoffMessage` | `core/agent_handoff_message.py` | Single-run handoff projection for conversation | YES | `my_agent_mcp/server.py` (`complete_agent_run`), tests | Persistent (saved as `MessageRecord` in SQLite `messages`) | YES (`test_agent_handoff_projection.py`) | ACTIVE | Appended to session conversation upon run completion |
| `request_agent_handoff` | `my_agent_mcp/server.py` | MCP tool for explicit coordinator handoff | YES | MCP clients | Ephemeral in-memory | YES (`test_mcp_handoff_tools.py`) | ACTIVE | Validates transitions and sanitizes payload |
| `get_allowed_handoff_targets` | `my_agent_mcp/server.py` | MCP tool querying allowed destinations | YES | MCP clients | Ephemeral (Static) | YES (`test_mcp_handoff_tools.py`) | ACTIVE | Backed by `HandoffTransitionMatrix` |
| `get_handoff_history` | `my_agent_mcp/server.py` | MCP tool reading handoff audit trail | YES | MCP clients | Ephemeral (in-memory list) | YES (`test_mcp_handoff_tools.py`) | PARTIAL | Returns empty after server process restart |
| `get_agent_handoff_context` | `my_agent_mcp/server.py` | MCP tool fetching bounded ledger context | YES | MCP clients | Persistent | YES (`test_mcp_agent_run_tools.py`) | ACTIVE | Reads from `SQLiteAgentRunStore` |
| `handoff_coder_session` | `my_agent_mcp/server.py` | MCP tool for discovering and resuming latest session | YES | MCP clients (AGY, Codex, Claude) | Persistent | YES (`test_mcp_coder_tools.py`) | ACTIVE | Discovery + resume entry point |
| `resume_coder_session` | `my_agent_mcp/server.py` | MCP tool resuming durable coder session | YES | `handoff_coder_session`, MCP clients | Persistent | YES (`test_mcp_coder_tools.py`) | ACTIVE | Restores session state, but passes unbounded raw history |
| `set_coder_execution_target` | `my_agent_mcp/server.py` | MCP tool switching runtime/model | YES | MCP clients | Persistent | YES (`test_mcp_coder_tools.py`) | ACTIVE | Pushes full history to `Orchestrator` |
| `resume_project` | `my_agent_mcp/server.py` | MCP tool resuming project, checkpoint, conversation | YES | MCP clients | Persistent | YES (`test_task_continuity.py`) | ACTIVE | Bounded history (`limit=20`), separate from coder session |
| `ControlPlane._agent_handoff_builder` | `core/control_plane.py` | Field injected into `ControlPlane` | NO | Injected in `__init__`, never called | N/A | NO | DEAD_CODE | Parameter stored in `self._agent_handoff_builder` but never invoked |
| `SqlitePendingApprovalStore` | `persistence/sqlite_pending_approval_store.py` | Durable SQLite store for pending tool approvals | PARTIAL | Direct tests; docstring in `control_plane.py` | Persistent | YES (`test_sqlite_pending_approval_store.py`) | PARTIAL | Not instantiated in `my_agent_mcp/server.py`; `CoderAgent` scans raw history instead |

---

## 3. ORIGINAL_HANDOFF

In Phase 00 and the initial architecture baseline:
- `core/handoff_coordinator.py` was introduced as an in-memory coordinator enforcing `HandoffTransitionMatrix` and basic guardrails (`HandoffGuardrailEngine`).
- `core/handoff_contracts.py` defined initial result types (`ImplementationResult`, `ArchitectureProposal`, `ResearchResult`).
- Coder session execution in `core/orchestrator.py` and `integrations/runtime_bridge.py` supported switching execution targets via `RuntimeCommandType.SET_EXECUTION_TARGET`.
- Coder session state was persisted in SQLite (`persistence/sqlite_coder_session_store.py`), and messages were stored in SQLite (`persistence/sqlite_conversation_store.py`).
- No cross-agent ledger, no step execution evidence, and no bounded context projection existed.

---

## 4. LATER_ADDITIONS

Multiple major capabilities were layered onto or alongside the initial handoff code:

1. **Execution Integrity & Truthful Evidence (Phase 02):**
   - Added `is_mocked: bool` and `execution_evidence: dict[str, Any]` to `ImplementationResult` in `core/handoff_contracts.py`.
   - Wired `WholePlanCoordinator` to verify real workspace modifications and test results.
2. **Cross-Agent Durable Execution Ledger (Post-Phase 02):**
   - Added `core/agent_run.py`, `core/agent_work_result.py`, and `persistence/sqlite_agent_run_store.py`.
   - Created `core/agent_handoff_context.py` (`AgentHandoffContextBuilder`, `AgentHandoffContext`) with context pruning.
   - Created `core/agent_handoff_message.py` (`AgentHandoffMessage`) projecting structured run outcomes into conversation messages.
   - Exposed `start_agent_run`, `complete_agent_run`, `fail_agent_run`, and `get_agent_handoff_context` in `my_agent_mcp/server.py`.
3. **Approval Durability & Reconciliation (Coder Stack):**
   - Added `reconcile_coder_tool_result` and `resolve_coder_approval` in `server.py`.
   - Added `CoderAgent.restore_pending_approvals()`, which dynamically parses durable tool messages to revive unapproved calls.
   - Created `SqlitePendingApprovalStore`, though it remains unwired in `server.py`.
4. **External Reasoning Bridge Foundation (Phase 04.5A):**
   - Created `core/context_broker.py`, `core/context_disclosure.py`, and `core/reasoning_bridge.py` with `SensitiveDataGate`, category bounding, and token budgeting.
   - *Crucially:* These Phase 04.5A protections were **NOT** backported to `resume_coder_session` or `set_coder_execution_target`.

---

## 5. CURRENT_HANDOFF_FLOW

Empirical trace of actual data and control flow across the three paths:

```
[Flow A: Specialist Role Handoff (Ephemeral)]
Caller (MCP Client / Agent)
  ──► MCP request_agent_handoff(source_role, target_role, task_id, payload, reason)
        ──► HandoffCoordinator.request_handoff()
              ──► HandoffGuardrailEngine.evaluate_guardrails()
                    ├── HandoffTransitionMatrix.is_transition_allowed()
                    ├── PromptInjectionDefense.inspect(reason)
                    ├── prune_payload() (history > 15, strings > 5k)
                    └── SecretRedactor.redact()
              ──► Appends to in-memory self._history
        ◄── Returns HandoffDecision (Status: ACCEPTED / REJECTED)
(Flow terminates. No agent invocation, no SQLite write, no prompt injection to downstream runtime)

[Flow B: Cross-Agent Durable Execution Ledger (Durable)]
Agent finishes step
  ──► MCP complete_agent_run(run_id, result_dict)
        ──► SQLiteAgentRunStore.save(AgentRunRecord with AgentWorkResult)
        ──► AgentHandoffMessage.from_run_record()
        ──► SQLiteConversationStore.add_message() (renders Markdown summary into messages table)
Incoming Agent starts next step
  ──► MCP get_agent_handoff_context(task_id, target_role, plan_id, step_id)
        ──► AgentHandoffContextBuilder.build_context()
              ├── Queries SQLiteAgentRunStore (step-level -> plan-level -> task-level)
              ├── Collects changed_files, created_files, tests, findings, notes
              └── prune_context() (max 15 files, 3 tests, 5 findings, 3 notes)
        ◄── Returns AgentHandoffContext JSON + Markdown prompt brief

[Flow C: Coder Session Resume / Host Switch (Durable but Unbounded)]
Restart / Model Switch / Handoff
  ──► MCP handoff_coder_session(project_id)
        ──► get_latest_resumable_coder_session() (queries coder_sessions table)
        ──► resume_coder_session(session_id)
              ├── SQLiteCoderSessionStore.get(session_id)
              ├── SQLiteConversationStore.history(project_id, session_id, limit=None)  <-- UNBOUNDED!
              ├── CoderAgent.restore_pending_approvals(durable_history)
              ├── Orchestrator.resume_session(session_id, context)
              └── Orchestrator.set_execution_target(session_id, history=durable_history)
                    └── RuntimeBridge puts SET_EXECUTION_TARGET command on queue
Caller loops next_coder_command(session_id)
  ◄── Receives SET_EXECUTION_TARGET with entire raw conversation history
```

---

## 6. CURRENT_PAYLOAD AUDIT

### Detailed Field Classification

| Field | Source Object | Category | Status | Notes |
|:---|:---|:---|:---|:---|
| `project_id` | `AgentHandoffContext` / Session | `PROJECT_STATE` | PRESENT | Explicit in session and run records |
| `task_id` | `AgentHandoffContext` / Session | `TASK_STATE` | PRESENT | Present in all handoff objects |
| `plan_id`, `step_id` | `AgentHandoffContext` | `TASK_STATE` | PRESENT | Optional pointers to active plan step |
| `objective` | `HandoffRequest.payload` | `TASK_STATE` | PARTIAL | Only if provided by caller; not in `AgentHandoffContext` |
| `prior_runs` | `AgentHandoffContext` | `EXECUTION_STATE` | PRESENT | Up to 3-5 prior run records |
| `latest_summary` | `AgentHandoffContext` | `TASK_STATE` | PRESENT | Extracted from latest `AgentWorkResult.summary` |
| `changed_files` | `AgentHandoffContext` | `GIT_STATE` | PRESENT | Pruned to 15 files |
| `created_files` | `AgentHandoffContext` | `GIT_STATE` | PRESENT | Pruned to 15 files |
| `recent_tests` | `AgentHandoffContext` | `TEST_STATE` | PRESENT | Pruned to 3 test suites (`TestExecutionResult`) |
| `open_findings` | `AgentHandoffContext` | `PROJECT_STATE` | PRESENT | Pruned to 5 findings (`AgentFinding`) |
| `remaining_work` | `AgentHandoffContext` | `NEXT_ACTION` | PRESENT | Up to 5 items |
| `handoff_notes` | `AgentHandoffContext` | `OTHER` | PRESENT | Up to 3 notes |
| `decision_references` | `AgentHandoffContext` | `APPROVAL_STATE` | PRESENT | Up to 3 references to resolved decisions |
| `runtime_id`, `model_id` | `SessionRecord` / Runtime | `PROVIDER_STATE` | PRESENT | Tracked in `coder_sessions` |
| `history` | Coder session command | `CONVERSATION_STATE` | PRESENT | **Raw, unbounded message tuples** |
| `execution_evidence` | `ImplementationResult` | `EXECUTION_STATE` | PARTIAL | Carried in `ImplementationResult`, but **dropped** when building `AgentHandoffContext` |
| `is_mocked` | `ImplementationResult` | `EXECUTION_STATE` | PARTIAL | Present in `ImplementationResult`, but not exposed in `AgentHandoffContext` |
| `verification_gate_passed` | Gate result | `EXECUTION_STATE` | ABSENT | Not propagated into `AgentHandoffContext` |
| `code_graph_state` | N/A | `OTHER` | ABSENT | Never queried or referenced during handoff |
| `memory_references` | N/A | `MEMORY_STATE` | ABSENT | Never queried or referenced during handoff |

---

## 7. PERSISTENCE REALITY

| State Entity | Storage Mechanism | Process Restart | MCP Restart | Machine Restart | Host Switch | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| Coder Session Metadata | SQLite (`coder_sessions`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| Conversation Messages | SQLite (`messages`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| Checkpoints | SQLite (`checkpoints`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| Agent Runs & Work Results | SQLite (`agent_runs`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| Tasks & Plans | SQLite (`tasks`, `plans`, `plan_steps`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| Code Graph | SQLite (`code_graph_*`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| Memory Entries & Embeddings | SQLite (`memory_*`) | SURVIVES | SURVIVES | SURVIVES | SURVIVES | WAL mode enabled |
| `HandoffCoordinator._history` | Memory list | **LOST** | **LOST** | **LOST** | **LOST** | In-memory cache only in MCP server |
| In-flight Coder Worker Tasks | Memory dict (`_coder_worker_tasks`) | **LOST** | **LOST** | **LOST** | **LOST** | Recreated on resume |
| `CoderAgent._pending_approvals` | Memory dict | Reconstructed | Reconstructed | Reconstructed | Reconstructed | Dynamically parsed from `messages` history |

---

## 8. RESUME REALITY

There are two primary resume functions in MyAgent:

### A. `resume_project(project_id, history_limit=20)` (`my_agent_mcp/server.py` line 755)
- Loads `ProjectRecord` from SQLite.
- Loads latest `CheckpointRecord` for the active task (summary, next action, files changed, tests).
- Loads latest `ConversationRecord`.
- Loads bounded conversation history: `conversations.history(project_id, conversation_id, limit=history_limit)`.
- Returns structured JSON to the caller.
- **Evaluation:** Clean, bounded, structured, but does not rehydrate active coder execution runtime.

### B. `resume_coder_session(session_id)` (`my_agent_mcp/server.py` line 2271)
- Triggered directly or via `handoff_coder_session(project_id)`.
- Resolves session from SQLite `coder_sessions`.
- Resolves conversation and loads **all history records**:
  ```python
  history_records = await conversations.history(
      project.project_id,
      session_id,
  ) # limit is None!
  ```
- Calls `stack.orchestrator.resume_session(session_id, durable.context)`.
- Calls `stack.orchestrator.set_execution_target(session_id, runtime_id=..., model_id=..., history=durable_history)`.
- Calls `stack.agent.restore_pending_approvals(session_id, durable_history, durable.context)` if state is `WAITING_APPROVAL`.
- Spawns in-process worker task.
- **Evaluation:** Fully resumes session execution, but dumps all historical messages to the execution target without bounding or compaction.

---

## 9. RAW HISTORY / CONTEXT BEHAVIOR

- **`RAW_HISTORY_INCLUDED`:** **YES**
- **Where:** `my_agent_mcp/server.py` line 2324 (`resume_coder_session`) and line 2403 (`set_coder_execution_target`).
- **How many messages:** **ALL messages in the session.** `limit=None` in `conversations.history()`.
- **Bounded or Unbounded:** **UNBOUNDED.**
- **Persisted or Ephemeral:** Persisted in SQLite `messages` table.
- **Loaded every resume or conditionally:** Loaded unconditionally on every resume or target switch.
- **Whether old phases are re-sent:** **YES.** If a single session spans multiple phases, all messages from Phase 01, Phase 02, etc., remain in the history list and are re-transmitted to the model.

---

## 10. EXECUTION TRUTH

- `core/handoff_contracts.py` defines `ImplementationResult` with:
  - `success: bool`
  - `is_mocked: bool`
  - `execution_evidence: dict[str, Any]`
- When `WholePlanCoordinator` executes steps (`core/whole_plan_coordinator.py`), it evaluates `ImplementationResult`, runs verification gates (test runners and security scanners), and records gate outcomes.
- **The Disconnect:** When `complete_agent_run` is called, `AgentWorkResult` contains test results and command executions, but `is_mocked` and raw kernel execution evidence are **not propagated** into `AgentHandoffContext`.
- **Resumed Provider Discrimination:**
  - A resumed provider receiving raw conversation history or an `AgentHandoffContext` **CANNOT mathematically distinguish** between:
    - **MODEL CLAIM:** The model asserting in assistant messages that it tested the code.
    - **SYSTEM FACT:** Cryptographic / kernel verification evidence proving tests executed in a real sandbox.
  - While tool result messages carry `"reconciled": True` in metadata, external LLM prompts do not receive verified execution attestations.

---

## 11. CODEGRAPH & MEMORY RELATIONSHIP

### CodeGraph Relationship:
- **Status:** **IGNORED.**
- Neither `HandoffCoordinator`, `AgentHandoffContextBuilder`, `resume_project`, nor `resume_coder_session` queries, references, or embeds CodeGraph intelligence.
- CodeGraph operates exclusively as an on-demand tool (`sync_code_graph`, `find_code_symbol`, `code_dependencies`) or via `ContextBroker` in Phase 04.5A. During handoff and resume, CodeGraph is entirely ignored.

### Memory Relationship:
- **Status:** **IGNORED.**
- Resumed coder sessions and cross-agent handoffs do not retrieve L1 (working), L2 (project), or L3 (persistent) memories.
- Resumed runtimes must independently call memory tools or rely entirely on conversation history.

---

## 12. PROVIDER / RUNTIME CONTINUITY

- **Switching Runtime / Model:** Fully implemented via `set_coder_execution_target` and `handoff_coder_session`.
  - Supported: Antigravity -> Codex, Codex -> Claude, or model switching within Antigravity.
- **What is transferred:**
  - Workspace root, task ID, project ID.
  - Complete conversation history (user messages, assistant messages, tool call definitions, tool results).
- **Gaps:**
  - **No automated quota failover:** There is no autonomous reactive circuit breaker that detects HTTP 429 / quota exhaustion from provider A and triggers an automatic handoff to provider B.
  - **Context Window Asymmetry:** If switching from a 1M+ token window model (e.g. Gemini 1.5 Pro) to a model with a smaller context window (e.g. Claude 3.5 Sonnet 200k or local models), replaying unbounded raw history will cause immediate context window overflow.

---

## 13. SECURITY BOUNDARY

- **Current Coder Session History:**
  - Contains absolute filesystem paths (`/Users/haohg/Project/my-agent/...`).
  - Contains full environment details and command outputs.
  - Contains raw user instructions and tool arguments.
  - Classification: **`INTERNAL_ONLY` / `UNSAFE_FOR_EXTERNAL_PROVIDER`**.
- **Current `AgentHandoffContext`:**
  - Sanitized with `SecretRedactor` (redacts API keys, tokens, passwords).
  - Relative file paths and structured summaries only.
  - Classification: **`SAFE_FOR_EXTERNAL_PROVIDER`**, but currently not used by `resume_coder_session`.
- **Phase 04.5A Disconnect:** Phase 04.5A introduced `SensitiveDataGate` and `ContextDisclosureTracker` for external reasoning calls, but these guardrails are **not applied** when `resume_coder_session` or `set_coder_execution_target` constructs `history`.

---

## 14. TEST EVIDENCE

All 11 targeted test suites pass cleanly (**65/65 passed in 2.80s**).

| Behavior | Source Implementation | Test | Directly Proven | Result |
|:---|:---|:---|:---:|:---:|
| Role transition matrix authorization | `core/handoff_coordinator.py:70` | `tests/test_handoff_coordinator.py:15` | YES | PASS |
| Secret redaction in handoff payload | `core/handoff_coordinator.py:185` | `tests/test_handoff_coordinator.py:29` | YES | PASS |
| Unauthorized transition rejection | `core/handoff_coordinator.py:231` | `tests/test_handoff_coordinator.py:53` | YES | PASS |
| Prompt injection rejection in handoff | `core/handoff_coordinator.py:240` | `tests/test_handoff_coordinator.py:68` | YES | PASS |
| End-to-end handoff decision history | `core/handoff_coordinator.py:282` | `tests/test_handoff_coordinator.py:82` | YES | PASS |
| Payload pruning for history > 15 | `core/handoff_coordinator.py:198` | `tests/test_handoff_coordinator.py:111` | YES | PASS |
| Typed contract serialization / parsing | `core/handoff_contracts.py` | `tests/test_handoff_contracts.py` | YES | PASS |
| Agent handoff context building | `core/agent_handoff_context.py:125` | `tests/test_agent_handoff_context.py:70` | YES | PASS |
| Agent handoff context pruning limits | `core/agent_handoff_context.py:46` | `tests/test_handoff_pruning_and_hooks.py:23` | YES | PASS |
| Agent handoff projection message | `core/agent_handoff_message.py:10` | `tests/test_agent_handoff_projection.py` | YES | PASS |
| MCP handoff tools (`request_agent_handoff`) | `my_agent_mcp/server.py:1755` | `tests/test_mcp_handoff_tools.py` | YES | PASS |
| Coder session resume after restart | `my_agent_mcp/server.py:2271` | `tests/test_mcp_coder_tools.py:1061` | YES | PASS |
| Coder execution target switching | `my_agent_mcp/server.py:2386` | `tests/test_mcp_coder_tools.py:1554` | YES | PASS |
| E2E Antigravity -> Codex -> Reviewer | `core/agent_handoff_context.py` | `tests/test_e2e_antigravity_to_codex_handoff.py` | YES | PASS |
| Task continuity & bounded project resume | `my_agent_mcp/server.py:755` | `tests/test_task_continuity.py` | YES | PASS |

---

## 15. CURRENT CAPABILITY MATRIX

| Capability | Current Status | Implementation | Test Evidence |
|:---|:---:|:---|:---|
| handoff creation | IMPLEMENTED | `HandoffCoordinator.request_handoff`, `AgentHandoffContextBuilder` | `test_handoff_coordinator.py`, `test_agent_handoff_context.py` |
| handoff persistence | PARTIAL | `AgentRunStore` persists in SQLite; `HandoffCoordinator._history` is in-memory only | `test_sqlite_agent_run_store.py` |
| project continuity | IMPLEMENTED | `resume_project` in `server.py` | `test_task_continuity.py` |
| task continuity | IMPLEMENTED | `SQLiteTaskStore`, `checkpoints` | `test_task_recovery_scope.py` |
| session continuity | IMPLEMENTED | `SQLiteCoderSessionStore`, `resume_coder_session` | `test_mcp_coder_tools.py` |
| conversation continuity | IMPLEMENTED | `SQLiteConversationStore`, `messages` table | `test_session.py` |
| provider/runtime continuity | IMPLEMENTED | `RuntimeBridge.set_execution_target` | `test_runtime_bridge.py`, `test_mcp_coder_tools.py` |
| checkpoint continuity | IMPLEMENTED | `SQLiteCheckpointStore` | `test_task_continuity.py` |
| resume after process restart | IMPLEMENTED | `handoff_coder_session` rehydrates from SQLite | `test_mcp_coder_tools.py` |
| resume after machine restart | IMPLEMENTED | Disk-backed SQLite with WAL mode | `test_sqlite_agent_run_store.py` |
| changed-files continuity | IMPLEMENTED | `AgentWorkResult.changed_files`, `CheckpointRecord` | `test_agent_handoff_context.py` |
| test-result continuity | IMPLEMENTED | `AgentWorkResult.tests`, `CheckpointRecord.tests` | `test_agent_handoff_context.py` |
| execution-evidence continuity | PARTIAL | Present in `ImplementationResult`, dropped in `AgentHandoffContext` | `test_whole_plan_execution_integrity.py` |
| verification continuity | PARTIAL | Evaluated in `WholePlanCoordinator`, not carried in handoff brief | `test_verification_gate_coordinator.py` |
| approval continuity | PARTIAL | Restored dynamically from message history; `SqlitePendingApprovalStore` unwired | `test_mcp_coder_tools.py` |
| next-action continuity | IMPLEMENTED | `CheckpointRecord.next_action`, `AgentHandoffContext.remaining_work` | `test_agent_handoff_context.py` |
| CodeGraph reuse | MISSING | Not queried or embedded in handoff or resume | None |
| memory reuse | MISSING | Not queried or embedded in handoff or resume | None |
| history bounding | PARTIAL | Bounded in `resume_project` (20); **UNBOUNDED** in `resume_coder_session` (all) | `test_task_continuity.py`, `test_mcp_coder_tools.py` |
| context compaction | PARTIAL | Compaction implemented in `AgentHandoffContext`, but unused in coder session | `test_handoff_pruning_and_hooks.py` |
| cross-project isolation | IMPLEMENTED | Scoped by `project_id` across all database queries | `test_mcp_coder_tools.py` |

---

## 16. CLASSIFICATION OF FINDINGS

### A. ALREADY IMPLEMENTED WELL
1. **Durable Storage Architecture:** SQLite stores for coder sessions, conversation messages, checkpoints, agent runs, tasks, plans, and memory records survive process and machine restarts with WAL mode.
2. **Specialist Role Security Guardrails:** `HandoffTransitionMatrix` strictly blocks unauthorized role jumps. `HandoffGuardrailEngine` redacts secrets and detects prompt injection.
3. **Structured Context Aggregation:** `AgentHandoffContextBuilder` aggregates multi-run task state and applies deterministic pruning (15 files, 3 tests, 5 findings, 3 notes).
4. **Seamless Host Switching Interface:** `handoff_coder_session` provides a clean single-call discovery and resume flow across hosts (Antigravity -> Codex -> Claude).

### B. IMPLEMENTED BUT PARTIAL
1. **`HandoffCoordinator` In-Memory Lifetime:** Has a `_HandoffHistoryStore` protocol, but the production MCP server instantiates it with `history_store=None`, so decision audit logs are lost on restart.
2. **Coder Session Unbounded History:** `resume_coder_session` fetches ALL messages from `messages` table with no limit, passing unbounded raw history to the model.
3. **Approval State Durability:** Reconstructed by rescanning raw history; the dedicated `SqlitePendingApprovalStore` is not wired into `server.py` or `ControlPlane`.
4. **Execution Evidence Pass-through:** `ImplementationResult.execution_evidence` is recorded during plan step execution, but dropped when creating `AgentHandoffContext`.

### C. MISSING
1. **CodeGraph Integration:** Handoff payloads and resume flows do not query or include CodeGraph symbols or dependency structures.
2. **Memory Integration:** Resumed sessions do not automatically retrieve or attach L1/L2/L3 memory context.
3. **Autonomous Quota Failover:** Switching execution targets is purely client/tool-driven; there is no autonomous failover upon provider exhaustion.
4. **External Provider Disclosure Boundary:** Coder session history does not pass through Phase 04.5A `SensitiveDataGate` before being dispatched to external runtimes.

### D. BUG / SECURITY BLOCKER
1. **Dead Injected Dependency:** `core/control_plane.py` accepts `agent_handoff_builder: AgentHandoffContextBuilder` in `__init__`, assigns it to `self._agent_handoff_builder`, but never invokes it anywhere in the control plane.
2. **Sensitive Context Leakage on Resume:** Sending unbounded raw conversation history (`limit=None`) to third-party models leaks internal directory structures and raw tool arguments without redaction.

### E. DOCUMENTATION STALE
1. **`AGENTS.md`:** Describes `handoff_coder_session` as the sole continuity mechanism, making no mention of `AgentHandoffContextBuilder` or the durable execution ledger (`agent_runs`).
2. **`docs/cognitive_architecture/BASELINE.md`:** Does not document `HandoffCoordinator` role transition guardrails or `SqlitePendingApprovalStore`.

---

## 17. RECOMMENDED NEXT AUDIT

Prior to designing any hardening for Phase 05:
- **Phase 04.5H-B (Handoff Hardening Spec & Architecture):** Design the unification of `AgentHandoffContextBuilder` with `resume_coder_session`, enforce history bounding (e.g., sliding window + structured summary), wire `SqlitePendingApprovalStore`, and apply `SensitiveDataGate` to external provider transfers.
