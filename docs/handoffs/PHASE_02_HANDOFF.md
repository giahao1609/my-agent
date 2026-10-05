# PHASE 02 HANDOFF — Execution Integrity / Real WholePlan Execution

## PHASE

02 — Execution Integrity / Real WholePlan Execution

## BASE_COMMIT

`a1f1c63f2fc7a8525fa0142b77dd90242ab1a3e0` (origin/main, origin/HEAD, main)  
Merge pull request #2 from giahao1609/feat/houhou-01-capability-truth

## FINAL_COMMIT

`58b2c89630abd29daa6f064d0a21be6b22e55c23`
feat(execution): enforce truthful whole-plan execution

---

## OBJECTIVE

Eliminate false `WholePlan` completion and connect `WholePlan` execution to a real existing execution path.

Core Invariant:
```text
NO REAL EXECUTION -> NO REAL COMPLETION
```

Ensure that:
1. `_NoOpStepExecutor` and simulated executors can never cause a production `PlanStep` or `Plan` to become `COMPLETED`.
2. Missing or failing real executors propagate truthful failures without false success.
3. Real execution adapter (`CoderRuntimeStepExecutor`) reuses the existing `CoderAgentStack`, `Orchestrator`, `RuntimeBridge`, and `CoderRuntimeWorker` workspace tools.
4. Real execution produces durable, inspectable execution evidence (`StepExecutionEvidence`, `AgentRunRecord`).
5. Governance, tool policies, workspace boundaries, and approval requirements are strictly preserved during execution.

---

## PRE_PHASE_TEST_BASELINE

- 706 passed tests (from Phase 01).
- Existing test suite running clean on branch `feat/houhou-02-execution-integrity`.

---

## REAL_EXECUTION_PATH_DISCOVERED

Detailed map of the existing coder execution infrastructure:
1. **`CoderAgentStack` (`agents/coder_stack.py`):** Bundles `CoderAgent`, `Orchestrator`, `RuntimeBridge`, tool registry, memory, and knowledge backends.
2. **`Orchestrator` (`core/orchestrator.py`):** Manages sessions, tool execution dispatch via `ToolExecutor`, event streaming, and session persistence.
3. **`CoderAgent` (`agents/coder_agent.py`):** Consumes prompts and streams `AgentEvent` objects (`TOOL_USE`, `TOOL_RESULT`, `TOOL_CONFIRM`, `TEXT`, `STOP`). Intercepts actions requiring approval and pauses execution.
4. **`CoderRuntimeWorker` (`agents/coder_runtime_worker.py`):** Worker loop listening on `RuntimeBridge.next_command(session_id)`. Interacts with `ModelBackend` / LLM, generates tool calls, and publishes them back to runtime.
5. **Tool Execution:** Tools registered in `build_coder_tool_registry()` execute against `ToolPolicy` and `ToolExecutor`, guaranteeing workspace confinement and permission checks (e.g., `user_id` authenticated scope).
6. **Completion & Observation:** File changes (`write_file`, `edit_file`, `delete_path`) and commands (`run_command`) are observed from `TOOL_USE` events and persisted in `StepExecutionEvidence` and `AgentRunRecord`.

---

## FILES_CHANGED

### New files created:
- `core/step_execution.py`: Contains `StepExecutionEvidence` dataclass and `CoderRuntimeStepExecutor` adapter implementing the `StepExecutor` protocol.
- `tests/test_whole_plan_execution_integrity.py`: 11 targeted behavioral tests enforcing execution integrity, NoOp rejection, real mutation, gate evaluation, and approval preservation.
- `docs/cognitive_architecture/contracts/execution.md`: Architecture contract defining execution semantics, evidence model, completion gate, and NoOp policy.
- `docs/handoffs/PHASE_02_HANDOFF.md`: This handoff document.

### Existing files modified (within allowed scope):
- `core/handoff_contracts.py`: Added `is_mocked: bool = False` and `execution_evidence: dict[str, Any]` fields to `ImplementationResult`.
- `core/verification_gate_coordinator.py`: Added strict rejection rule rejecting any result with `is_mocked is True` or `[noopstepexecutor]` in summary.
- `core/whole_plan_coordinator.py`:
  - `_NoOpStepExecutor` explicitly marks `is_mocked = True`, `execution_evidence={"is_real": False}`, and `is_real_executor = False`.
  - Preserved `is_mocked` and `execution_evidence` through drift detection wrapping.
  - Updated `capabilities()` to report `Availability.AVAILABLE` when a real executor is injected, and `Availability.MOCKED` when using simulated executors.
- `my_agent_mcp/server.py`:
  - Wired `CoderRuntimeStepExecutor` into `run_whole_plan` tool using `_get_coder_stack()`, `_agent_run_service`, and `_ensure_in_process_coder_worker`.
  - Handled non-existent plan lookup cleanly without raising uncaught `KeyError`.
- `tests/test_whole_plan_coordinator.py`: Updated `test_no_op_executor_returns_success` to verify `result.is_mocked is True` and `is_real is False`.
- `tests/test_mcp_verification_tools.py`: Updated `test_mcp_run_whole_plan` to reflect the truthful Phase 02 contract: running without an active coder worker in `EXTERNAL` mode cannot falsely report `plan_completed: True`. Documented `OLD ASSUMPTION`, `WHY FALSE`, `NEW CONTRACT`.

---

## PUBLIC_INTERFACES_CREATED

1. `core.step_execution.StepExecutionEvidence`:
   - Structured evidence dataclass tracking `step_id`, `executor_identity`, `session_id`, `run_id`, `started_at`, `finished_at`, `is_real`, `tool_calls`, `files_changed`, `files_created`, `files_deleted`, `commands_executed`, `execution_success`, `verification_performed`, `verification_passed`, `notes`.
   - `to_dict() -> dict[str, Any]` for serialization.

2. `core.step_execution.CoderRuntimeStepExecutor`:
   - Production implementation of `StepExecutor` protocol.
   - Constructor: `__init__(coder_stack, workspace_path, project_id=None, task_id=None, plan_id=None, user_id="authorized-user", agent_run_service=None, worker_task_launcher=None)`.
   - Method: `execute_step(step_id, step_title, step_role, repair_hint="") -> ImplementationResult`.
   - Property: `is_real_executor = True`.

---

## PUBLIC_INTERFACES_MODIFIED

1. `core.handoff_contracts.ImplementationResult`:
   - Added `is_mocked: bool = False` (additive, backward-compatible).
   - Added `execution_evidence: dict[str, Any] = field(default_factory=dict)` (additive, backward-compatible).

2. `core.whole_plan_coordinator.WholePlanCoordinator`:
   - `capabilities() -> list[CapabilityStatus]` now dynamically reports `Availability.AVAILABLE` if `step_executor.is_real_executor is True`, and `Availability.MOCKED` otherwise.

---

## STEP_EXECUTOR_IMPLEMENTATION

`CoderRuntimeStepExecutor` acts as the production adapter:
- Manages an execution turn for a plan step within a durable orchestrator session.
- Creates and tracks an `AgentRunRecord` via `AgentRunService`.
- Launches the in-process worker task if available. If no worker is available, returns truthful `ImplementationResult(success=False, is_mocked=False, notes="Blocked: no active coder worker...")`.
- Iterates over `CoderAgent.run_session` event stream within a bounded timeout.
- Collects tool calls, workspace file modifications, and command executions.
- Pauses cleanly on `TOOL_CONFIRM` without hanging.
- Attaches the complete `StepExecutionEvidence` to `ImplementationResult.execution_evidence` and updates `AgentRunRecord.result`.

---

## NOOP_BEHAVIOR_BEFORE

- In Phase 00/01, `WholePlanCoordinator` used `_NoOpStepExecutor` by default when no executor was provided.
- `_NoOpStepExecutor` returned `ImplementationResult(success=True)` with no indicator of simulation.
- `VerificationGateCoordinator` accepted the synthetic result and marked the step `ReviewStatus.APPROVED`.
- Steps and plans falsely became `PlanStepState.COMPLETED` and `PlanState.COMPLETED` with zero execution evidence or workspace changes.

---

## NOOP_BEHAVIOR_AFTER

- `_NoOpStepExecutor` returns `ImplementationResult(success=True, is_mocked=True, execution_evidence={"is_real": False, "executor_identity": "_NoOpStepExecutor"})`.
- `VerificationGateCoordinator` explicitly detects `is_mocked is True` and `[NoOpStepExecutor]` in summary, immediately returning `ReviewStatus.REJECTED`.
- `WholePlanCoordinator` marks the step as `PlanStepState.FAILED`.
- `_NoOpStepExecutor` CANNOT satisfy the completion gate under any circumstance.

---

## MCP_WIRING_BEFORE

- `server.py:run_whole_plan` constructed `WholePlanCoordinator` with `step_executor=None`, silently falling back to `_NoOpStepExecutor`.
- Calling `run_whole_plan` returned `plan_completed: True` and `steps_completed: N` on completely simulated steps.

---

## MCP_WIRING_AFTER

- `server.py:run_whole_plan` resolves the active project and task IDs, and wires `CoderRuntimeStepExecutor` using `_get_coder_stack()`, `_agent_run_service`, and `_ensure_in_process_coder_worker`.
- If the plan is missing, it returns a clear error without raising `KeyError`.
- If no worker is running (such as in `EXTERNAL` mode without an external consumer), execution truthfully reports `plan_completed: False` and `steps_completed: 0`.
- Simulated completion is strictly eliminated from the MCP endpoint.

---

## EXECUTION_EVIDENCE_MODEL

Evidence is recorded in two unified locations:
1. **`ImplementationResult.execution_evidence`**: Passed directly to verification gates and step telemetry.
2. **`AgentRunRecord.result.metadata["execution_evidence"]`**: Persisted in the SQLite database via `AgentRunService`.

Evidence contains:
- `step_id`, `executor_identity`, `session_id`, `run_id`, `started_at`, `finished_at`.
- `is_real: True`
- `tool_calls`: list of all invoked tools with call IDs and arguments.
- `files_changed`, `files_created`, `files_deleted`: list of paths touched in the workspace.
- `commands_executed`: list of shell commands run.
- `verification_performed`, `verification_passed`: verification status.
- `execution_success`: boolean indicating whether the executor succeeded.

---

## COMPLETION_GATE

Enforced in `VerificationGateCoordinator.evaluate(...)`:
1. `is_mocked is True` -> `ReviewStatus.REJECTED` ("Step '{step_id}' REJECTED: implementation result is simulated/mocked. Real execution required.").
2. `[noopstepexecutor]` in summary -> `ReviewStatus.REJECTED`.
3. `implementation_result.success is False` -> `ReviewStatus.REJECTED`.
4. `test_result.failed_tests > 0` -> `ReviewStatus.NEEDS_REWORK`.
5. `security_result.critical_count > 0` -> `ReviewStatus.REJECTED`.
6. Only when all gates pass does the gate return `ReviewStatus.APPROVED`.

---

## CAPABILITY_STATUS_BEFORE

- `whole_plan_autonomous_step_execution`: `Availability.MOCKED` (registered in Phase 01 because default coordinator used `_NoOpStepExecutor`).

---

## CAPABILITY_STATUS_AFTER

- When `WholePlanCoordinator` is instantiated with `_NoOpStepExecutor` (or simulated executor): `Availability.MOCKED`.
- When `WholePlanCoordinator` is instantiated with `CoderRuntimeStepExecutor` (concrete real executor): `Availability.AVAILABLE`.
- Verified dynamically by `test_capability_status_reflection`.

---

## AGENT_RUN_INTEGRATION

`CoderRuntimeStepExecutor` integrates directly with the existing `AgentRunService`:
- Calls `create_run(...)` at the start of step execution with `execution_role`, `step_id`, `plan_id`, `task_id`, and `project_id`.
- Transitions run to `RUNNING` via `start_run(...)`.
- On completion, attaches `AgentRunResult` containing `changed_files` and `metadata["execution_evidence"]`, and transitions run to `COMPLETED`.
- On failure or approval block, transitions run to `FAILED` with explicit failure reason.

---

## SECURITY_AND_APPROVAL_BEHAVIOR

- Real execution respects `ToolPolicy` and `ToolPermission`.
- Confinement checks in `WorkspaceConfinement` and `PathResolver` prevent workspace escape.
- Unauthenticated contexts cannot perform write operations (`ToolPermission.WRITE` requires `user_id`).
- When a tool requires approval (`ToolDecision.REQUIRE_CONFIRMATION`, e.g., `rm -rf`), `CoderAgent` emits `TOOL_CONFIRM`.
- `CoderRuntimeStepExecutor` intercepts `TOOL_CONFIRM`, cleanly halts the autonomous loop without hanging, marks `waiting_approval=True`, and fails the completion gate. It NEVER bypasses approvals.

---

## TESTS_ADDED

In `tests/test_whole_plan_execution_integrity.py`:
1. `test_noop_cannot_complete_step`: Proves `_NoOpStepExecutor` cannot mark a step or plan as `COMPLETED`.
2. `test_missing_real_executor_blocks_or_fails`: Proves execution without a step executor fails truthfully.
3. `test_real_executor_success_with_workspace_mutation`: Executes real `CoderRuntimeStepExecutor`, writes `hello.txt` to workspace via model tool call, passes gates, and marks step `COMPLETED`.
4. `test_executor_failure_propagates_truthfully`: Proves model crash propagates and prevents step completion.
5. `test_verification_failure_prevents_completion`: Proves test verification failure prevents step completion even if implementation succeeded.
6. `test_mocked_result_rejected_by_verification_gate`: Proves gate strictly rejects results with `is_mocked=True` or `[NoOpStepExecutor]`.
7. `test_mcp_run_whole_plan_truth`: Proves MCP tool `run_whole_plan` returns truthful non-completion when plan does not exist or steps are not executed.
8. `test_agent_run_ledger_contains_execution_evidence`: Proves real execution populates `AgentRunRecord` with structured evidence, modified files, and session IDs.
9. `test_plan_state_transitions_preserved`: Proves `Plan` and `PlanStep` state machines are preserved across whole plan lifecycle.
10. `test_capability_status_reflection`: Proves coordinator capability status transitions between `MOCKED` and `AVAILABLE` based on real executor presence.
11. `test_governance_approval_preserved_in_real_execution`: Proves dangerous command requiring approval pauses execution and does not auto-approve or complete.

---

## TESTS_RUN

- Targeted: `tests/test_whole_plan_execution_integrity.py` (11 tests).
- Targeted: `tests/test_whole_plan_coordinator.py` (7 tests).
- Targeted: `tests/test_mcp_verification_tools.py` (13 tests).
- Full Test Suite: `python -m pytest tests/ --tb=no -q`.

---

## TEST_RESULTS

- Pre-Phase Baseline: 706 passed.
- Post-Phase Suite: **717 passed in 15.43s** (0 failures, 0 errors).
- All 706 previous tests pass with zero regressions.

---

## BEHAVIOR_VERIFIED

- [x] Production NoOp execution cannot create false `COMPLETED` steps.
- [x] Missing executor cannot create false completion.
- [x] Executor failure cannot create false completion.
- [x] Verification failure cannot create false completion.
- [x] Mock/simulated results cannot satisfy the production completion gate.
- [x] WholePlan production wiring uses a real executor or truthfully refuses execution when no worker is available.
- [x] Existing real coder/runtime infrastructure (`CoderAgentStack`, `Orchestrator`, `CoderRuntimeWorker`) is reused instead of duplicated.
- [x] Execution produces inspectable, durable evidence in `AgentRunRecord` and `ImplementationResult`.
- [x] Existing `Task` and `Plan` state machine contracts remain valid.
- [x] Existing security and approval gates are preserved.
- [x] Phase 01 capability truth semantics remain intact (`whole_plan` reports `AVAILABLE` when real executor is present).

---

## KNOWN_LIMITATIONS

- Autonomous execution in `run_whole_plan` requires an active in-process worker (`CODER_RUNTIME_MODE=in_process`). In `EXTERNAL` mode, steps truthfully block because no autonomous worker task is running in-process to consume runtime commands.
- Interactive multi-turn approval resumption during whole plan execution requires human approval resolution via MCP before the step can be retried.

---

## BLOCKED_DEPENDENCIES

None for Phase 02.

---

## FOLLOW_UP_ISSUES

- Phase 03 will introduce context injection and prompt composition into the execution loop.
- Phase 04+ will connect cognitive reasoning, reflexion repair diagnosis, and memory capture into step completion post-hooks.

---

## FORBIDDEN_FUTURE_ASSUMPTIONS

- Do NOT assume `WholePlanCoordinator` can run without a real executor in production.
- Do NOT assume `ImplementationResult(success=True)` alone is sufficient for step completion; evidence and verification gates are mandatory.
- Do NOT bypass `ToolPolicy` approvals during whole plan execution.

---

## NEXT_PHASE_EXPECTATIONS

Phase 03 (Context Assembly & Injection):
- Will consume the truthful execution pipeline established in Phase 02.
- Will assemble verified context from Code Graph, memory, and task specifications before passing prompts to the execution layer.
- Must preserve the execution integrity invariants established in this phase.
