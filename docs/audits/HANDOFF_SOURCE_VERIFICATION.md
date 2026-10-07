# PHASE 04.5H-A2 — HANDOFF SOURCE VERIFICATION REPORT

**Author:** Houhou (Front Agent of MyAgent)  
**Date:** 2026-10-07  
**Branch:** `audit/houhou-04-5h-handoff-continuity`  
**Base Commit / Current HEAD:** `d3f785051820623349fa84fda74d41fc849d01c5`  
**Status:** VERIFIED (Strict Evidence-Grounded Audit)

---

## 1. VERIFIED_FINDINGS

The following claims from `CURRENT_HANDOFF_REALITY_AUDIT.md` are **100% verified** by direct source code inspection and test execution:

1. **Coder Resume Reloads Unbounded Raw History (`limit=None`):**
   - Source: [`my_agent_mcp/server.py:2324-2327`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2324-L2327) in `resume_coder_session`:
     ```python
     history_records = await conversations.history(
         project.project_id,
         session_id,
     )
     ```
   - In [`persistence/sqlite_conversation_store.py:198`](file:///Users/haohg/Project/my-agent/persistence/sqlite_conversation_store.py#L198), `limit` defaults to `None`.
   - In [`persistence/sqlite_conversation_store.py:217-226`](file:///Users/haohg/Project/my-agent/persistence/sqlite_conversation_store.py#L217-L226), `limit is None` executes `SELECT * FROM messages WHERE project_id = ? AND conversation_id = ? ORDER BY created_at ASC, rowid ASC`.
   - In [`my_agent_mcp/server.py:2343-2348`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2343-L2348), the resulting `durable_history` tuple is passed directly to `stack.orchestrator.set_execution_target(session_id, ..., history=durable_history)`.
   - In [`my_agent_mcp/server.py:2264-2267`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2264-L2267), `handoff_coder_session` delegates unconditionally to `resume_coder_session`.
   - In [`my_agent_mcp/server.py:2403-2422`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2403-L2422), `set_coder_execution_target` executes the identical reload and pushes all historical messages upon every runtime/model switch.

2. **`HandoffCoordinator` History is 100% Ephemeral in Production:**
   - Source: [`my_agent_mcp/server.py:139-141`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L139-L141):
     ```python
     _handoff_coordinator = HandoffCoordinator(
         guardrail_engine=None
     )
     ```
   - Parameter `history_store` is omitted, defaulting to `None`.
   - In [`core/handoff_coordinator.py:291-292`](file:///Users/haohg/Project/my-agent/core/handoff_coordinator.py#L291-L292), decisions are stored strictly in `self._history: list[HandoffDecision]`.
   - No concrete class implements `_HandoffHistoryStore` anywhere in the repository.

3. **CodeGraph and Memory are Completely Ignored in Handoff and Resume:**
   - Search across `core/agent_handoff_context.py`, `core/handoff_coordinator.py`, `my_agent_mcp/server.py` (`resume_coder_session`, `handoff_coder_session`, `resume_project`) shows zero references to `CodeGraph`, `SQLiteCodeGraphStore`, `DurableMemoryService`, or `SQLiteMemoryStore`.
   - `CODEGRAPH_USED_IN_RESUME = NO`
   - `MEMORY_USED_IN_RESUME = NO`
   - `AGENT_HANDOFF_CONTEXT_USED_IN_CODER_RESUME = NO`

4. **Dead Dependency in ControlPlane:**
   - Source: [`core/control_plane.py:92`](file:///Users/haohg/Project/my-agent/core/control_plane.py#L92) and [`core/control_plane.py:104`](file:///Users/haohg/Project/my-agent/core/control_plane.py#L104): `agent_handoff_builder: AgentHandoffContextBuilder | None = None` is assigned to `self._agent_handoff_builder` but is never called in any method.

5. **Approval Durability Reconstructed Dynamically from Messages:**
   - Source: [`my_agent_mcp/server.py:2351-2355`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2351-L2355) and [`agents/coder_agent.py:161-231`](file:///Users/haohg/Project/my-agent/agents/coder_agent.py#L161-L231): `restore_pending_approvals` parses `TOOL_USE` events without matching `TOOL_RESULT` to revive unapproved calls.
   - [`persistence/sqlite_pending_approval_store.py`](file:///Users/haohg/Project/my-agent/persistence/sqlite_pending_approval_store.py) is **never instantiated in production**.

---

## 2. CORRECTED_FINDINGS & OVERCLAIMED_FINDINGS

The initial audit report contained four significant overclaims that are corrected below based on direct source evidence:

### Correction 1: External Safety of `AgentHandoffContext`
- **Initial Claim:** `AgentHandoffContext` is `SAFE_FOR_EXTERNAL_PROVIDER` because it applies `SecretRedactor`.
- **Source Truth:**
  - In [`core/agent_handoff_context.py:176-196`](file:///Users/haohg/Project/my-agent/core/agent_handoff_context.py#L176-L196), `AgentHandoffContextBuilder` reads **raw object attributes** directly from `r.result`:
    - `latest_summary = r.result.summary` (raw, unredacted)
    - `changed_files.add(f.path)` (raw, unredacted)
    - `handoff_notes.append(r.result.handoff_notes)` (raw, unredacted)
  - `AgentHandoffContextBuilder` does **NOT** invoke `SecretRedactor`.
  - It does **NOT** invoke `PiiSanitizer`.
  - It does **NOT** apply `SensitiveDataGate`.
  - It does **NOT** enforce `DisclosurePolicy`.
  - It does **NOT** enforce `ContextBudget`.
  - It does **NOT** perform prompt injection inspection or trust tagging on summaries or notes.
- **Corrected Status:** **`AGENT_HANDOFF_EXTERNAL_SAFETY = INTERNAL_ONLY / PARTIAL`**. It is NOT safe for untrusted external providers.

### Correction 2: Cross-Project Isolation in Handoff Context
- **Initial Claim:** Cross-project isolation is fully implemented across all subsystems.
- **Source Truth:**
  - In [`core/agent_handoff_context.py:131-140`](file:///Users/haohg/Project/my-agent/core/agent_handoff_context.py#L131-L140), `build_context` accepts `task_id, target_role, plan_id, step_id`. It does **NOT accept or filter by `project_id`**.
  - In [`my_agent_mcp/server.py:3099-3123`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L3099-L3123), MCP tool `get_agent_handoff_context` does not accept or validate `project_id`.
  - Direct behavioral tests proving cross-project isolation in `conversations.history` or `resume_coder_session` do not exist (only `cancel_coder_session` has a test).
- **Corrected Status:** **`CROSS_PROJECT_ISOLATION = PARTIAL`**.

### Correction 3: Machine Restart Continuity
- **Initial Claim:** Session continuity survives machine restart.
- **Source Truth:**
  - Automated tests prove `PERSISTED_ON_DISK` and `BEHAVIORALLY_RESUMED_AFTER_PROCESS_RESTART`.
  - Durability across a physical OS reboot / power cut is an **architectural inference** from SQLite WAL journal mode semantics, not a directly proven automated test result.
- **Corrected Status:** `BEHAVIORALLY_RESUMED_AFTER_PROCESS_RESTART` is `DIRECTLY_PROVEN`; `BEHAVIORALLY_RESUMED_AFTER_MACHINE_RESTART` is `INFERRED`.

### Correction 4: Unbounded Approval Replay
- **Source Truth:**
  - In [`agents/coder_agent.py:177-231`](file:///Users/haohg/Project/my-agent/agents/coder_agent.py#L177-L231), `restore_pending_approvals` does not check creation timestamps or expiration TTL.
  - An unresolved destructive tool call from an abandoned session days or weeks prior will be resurrected as pending upon resume.
- **Corrected Status:** `APPROVAL_DURABILITY = PARTIAL / NO_TTL`.

---

## 3. UNKNOWN_FINDINGS

1. **Behavior on Partial Message Corruption:** It is unknown how `restore_pending_approvals` behaves if SQLite contains a syntactically valid JSON message with a malformed `arguments` payload (lines 203-217 catch `JSONDecodeError`, but schema-invalid arguments will be dropped silently without logging an alert).
2. **Behavior Under Concurrent Multi-Host Resume:** If two hosts (e.g. Antigravity and Codex) simultaneously invoke `resume_coder_session` on the same `session_id`, `Orchestrator._sessions` locks are in-process only; concurrent cross-process collision behavior on SQLite WAL is untested.

---

## 4. EXECUTION_TRUTH_PROPAGATION_TABLE

Detailed field-by-field propagation trace across the entire execution pipeline:

| Execution Field | `StepExecutionEvidence` | `ImplementationResult` | `AgentWorkResult` | `AgentRunRecord` | `AgentHandoffContext` | `AgentHandoffMessage` |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| `is_mocked` | Inverse (`is_real=True`) | PRESERVED (`is_mocked: bool`) | DROPPED from typed fields (in `metadata["is_real"]`) | PRESERVED (in `result_json`) | **DROPPED** | **DROPPED** |
| `execution_evidence` | Self (Core Object) | PRESERVED (`execution_evidence: dict`) | DROPPED from typed fields (in `metadata`) | PRESERVED (in `result_json`) | **DROPPED** | **DROPPED** |
| `execution_success` | PRESERVED (`execution_success: bool`) | PRESERVED (`success: bool`) | PRESERVED (`status: str`) | PRESERVED (`state: AgentRunState`) | **DROPPED** (Aggregates without checking success) | PRESERVED (`status: str`) |
| `verification_performed` | PRESERVED (`verification_performed: bool`) | EMBEDDED in `execution_evidence` | EMBEDDED in `metadata` | PRESERVED (in `result_json`) | **DROPPED** | **DROPPED** |
| `verification_passed` | PRESERVED (`verification_passed: bool`) | EMBEDDED in `execution_evidence` | EMBEDDED in `metadata` | PRESERVED (in `result_json`) | **DROPPED** | **DROPPED** |
| `commands_executed` | PRESERVED (`tuple[str, ...]`) | DROPPED | PRESERVED (`CommandExecution`) | PRESERVED (in `result_json`) | **DROPPED** | **DROPPED** |
| `tests` | DROPPED | DROPPED | PRESERVED (`TestExecutionResult`) | PRESERVED (in `result_json`) | PRESERVED (`recent_tests`, max 3) | PRESERVED (Count summary) |
| `findings` | DROPPED | DROPPED | PRESERVED (`AgentFinding`) | PRESERVED (in `result_json`) | PRESERVED (`open_findings`, max 5) | PRESERVED (Count only) |

**Conclusion on Execution Truth:**
Truthful execution evidence (`is_real`, `execution_evidence`, `verification_performed`, `verification_passed`) is faithfully recorded in SQLite `agent_runs.result_json`, but is **completely stripped** when projecting into `AgentHandoffContext` and `AgentHandoffMessage`. Downstream agents receive only file paths and summary strings.

---

## 5. APPROVAL_DURABILITY_RESULT

- **Production Store:** `SqlitePendingApprovalStore` is **NOT INSTANTIATED** in `my_agent_mcp/server.py` and is **NOT WIRED** in `core/control_plane.py`.
- **Reconstruction Path:** Approvals are resurrected by parsing raw conversation message history in [`agents/coder_agent.py:161-231`](file:///Users/haohg/Project/my-agent/agents/coder_agent.py#L161-L231).
- **Restart Survival:** Survives Python process restart (proven by [`tests/test_mcp_coder_tools.py:1618-1652`](file:///Users/haohg/Project/my-agent/tests/test_mcp_coder_tools.py#L1618-L1652)), but depends entirely on the integrity of the conversation message stream.
- **TTL / Expiry:** **NONE**. Stale tool approvals never expire and can be approved at any future time.

---

## 6. RESTART_CONTINUITY_RESULT

| Entity | Persisted On Disk | Reloadable After Process Restart | Behaviorally Resumed After Process Restart | Behaviorally Resumed After Machine Restart |
|:---|:---:|:---:|:---:|:---:|
| Coder Sessions | DIRECTLY_PROVEN | DIRECTLY_PROVEN | DIRECTLY_PROVEN (`test_mcp_coder_tools.py:1622`) | INFERRED (SQLite WAL) |
| Conversation History | DIRECTLY_PROVEN | DIRECTLY_PROVEN | DIRECTLY_PROVEN (`test_mcp_coder_tools.py:1633`) | INFERRED (SQLite WAL) |
| Checkpoints | DIRECTLY_PROVEN | DIRECTLY_PROVEN | DIRECTLY_PROVEN (`test_task_continuity.py:45`) | INFERRED (SQLite WAL) |
| Agent Runs (Ledger) | DIRECTLY_PROVEN | DIRECTLY_PROVEN | DIRECTLY_PROVEN (`test_sqlite_agent_run_store.py`) | INFERRED (SQLite WAL) |
| Pending Approvals | DIRECTLY_PROVEN (as msg) | DIRECTLY_PROVEN | DIRECTLY_PROVEN (`test_mcp_coder_tools.py:1640`) | INFERRED (SQLite WAL) |
| Handoff Decisions | **NO** (RAM only) | **NO** | **NO** | **NO** |

---

## 7. EXTERNAL_SAFETY_RESULT

- **Classification:** **`AGENT_HANDOFF_EXTERNAL_SAFETY = INTERNAL_ONLY / PARTIAL`**
- **Sanitizers Applied in `AgentHandoffContextBuilder`:**
  - `SecretRedactor`: NO (reads raw model fields).
  - `PiiSanitizer`: NO.
  - `SensitiveDataGate`: NO.
  - `DisclosurePolicy`: NO.
  - `ContextBudget`: NO.
  - `PromptInjectionDefense`: NO.
- **Sanitizers Applied in Coder Session History:**
  - NONE. Raw text of commands, outputs, and prompts is replayed directly.

---

## 8. CROSS_PROJECT_RESULT

- **Classification:** **`CROSS_PROJECT_ISOLATION = PARTIAL`**
- **Where Enforced in Code:**
  - `resume_coder_session`: [`my_agent_mcp/server.py:2299`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2299) (`if durable.project_id != project.project_id: return "project_mismatch"`).
  - `cancel_coder_session`: [`my_agent_mcp/server.py:2848`](file:///Users/haohg/Project/my-agent/my_agent_mcp/server.py#L2848).
- **Where Missing in Code:**
  - `AgentHandoffContextBuilder`: Does not accept or check `project_id`.
  - `get_agent_handoff_context`: Does not accept or check `project_id`.
- **Direct Test Evidence:**
  - Proven for `cancel_coder_session` ([`tests/test_mcp_coder_tools.py:2912`](file:///Users/haohg/Project/my-agent/tests/test_mcp_coder_tools.py#L2912)).
  - Direct tests asserting that `conversations.history` or `resume_coder_session` rejects cross-project reads do **NOT** exist.

---

## 9. CODEGRAPH_MEMORY_RESULT

- `CODEGRAPH_USED_IN_RESUME = NO`
- `MEMORY_USED_IN_RESUME = NO`
- `AGENT_HANDOFF_CONTEXT_USED_IN_CODER_RESUME = NO`
