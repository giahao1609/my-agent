# Contract: Execution Integrity and Step Execution

**Established at Phase 02**  
**Source:** `core/step_execution.py`, `core/whole_plan_coordinator.py`, `core/verification_gate_coordinator.py`, `core/handoff_contracts.py`

## 1. Core Invariant

```text
NO REAL EXECUTION -> NO REAL COMPLETION
```

A `PlanStep` or `Plan` must NEVER reach `COMPLETED` unless real execution has occurred, verifiable evidence has been captured, and all configured verification gates have passed. Synthetic, mock, or simulated results are strictly forbidden from fulfilling production completion.

---

## 2. StepExecutor Protocol & Semantics

Defined in `core/whole_plan_coordinator.py`:

```python
class StepExecutor(Protocol):
    async def execute_step(
        self,
        step_id: str,
        step_title: str,
        step_role: AgentRole,
        repair_hint: str = "",
    ) -> ImplementationResult: ...
```

### Production Semantics
- Concrete production execution is provided by `CoderRuntimeStepExecutor` (`core/step_execution.py`).
- Bridges `WholePlanCoordinator` directly to the active `CoderAgentStack` (`CoderAgent`, `Orchestrator`, `RuntimeBridge`, workspace tools).
- Spawns and supervises in-process or attached runtime sessions.
- Accurately tracks workspace tool activity (`write_file`, `edit_file`, `delete_path`, `run_command`).
- Records an `AgentRunRecord` via `AgentRunService` including session IDs, step metadata, and structured execution evidence.

---

## 3. Real vs. Simulated Execution

| Dimension | Real Execution (`CoderRuntimeStepExecutor`) | Simulated Execution (`_NoOpStepExecutor` / Mocks) |
|---|---|---|
| **Executor Identity** | `CoderRuntimeStepExecutor` | `_NoOpStepExecutor` / mock |
| **`is_mocked` flag** | `False` | `True` |
| **`is_real_executor` attr** | `True` | `False` |
| **Workspace Activity** | Real tool operations, actual file modifications | No workspace mutation |
| **Execution Evidence** | Populated `StepExecutionEvidence` dictionary | Synthetic / empty evidence (`is_real: False`) |
| **Completion Gate** | Eligible for `ReviewStatus.APPROVED` | Strictly rejected (`ReviewStatus.REJECTED`) |
| **Production Plan Completion** | Can complete step & plan if gates pass | Cannot complete step or plan |

---

## 4. Execution Evidence Requirements

Every completed step must produce structured evidence conforming to `StepExecutionEvidence` (`core/step_execution.py`):

```python
@dataclass(frozen=True, slots=True)
class StepExecutionEvidence:
    step_id: str
    executor_identity: str
    runtime_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    started_at: str = ""
    finished_at: str = ""
    is_real: bool = True
    evidence_source: str = "coder_runtime"
    tool_calls: tuple[dict[str, Any], ...] = ()
    files_changed: tuple[str, ...] = ()
    files_created: tuple[str, ...] = ()
    files_deleted: tuple[str, ...] = ()
    commands_executed: tuple[str, ...] = ()
    execution_success: bool = True
    verification_performed: bool = False
    verification_passed: bool = False
    notes: str = ""
```

Evidence must be persisted in:
1. `ImplementationResult.execution_evidence`
2. `AgentRunRecord.result.metadata["execution_evidence"]`

---

## 5. Completion Gate

A `PlanStep` may transition to `PlanStepState.COMPLETED` if and only if all five conditions are satisfied:
1. **Real Execution Ran:** The executor is real (`is_mocked is False`, `execution_evidence["is_real"] is True`).
2. **Executor Reported Success:** `ImplementationResult.success is True`.
3. **Verification Gates Passed:** `VerificationGateCoordinator` evaluates `ReviewStatus.APPROVED` (test runner passed, security scanner found no critical vulnerabilities).
4. **Outcome Matches Objective:** Verified file changes or task-specific verification prove the requested outcome.
5. **Durable Evidence Captured:** `AgentRun` or plan step telemetry captures non-empty execution evidence.

If any condition fails:
- The gate rejects with `ReviewStatus.REJECTED` or requests `ReviewStatus.NEEDS_REWORK`.
- The step does NOT reach `COMPLETED`.

---

## 6. Failure Propagation

- **Missing Executor:** If no step executor is injected or no worker is available, the step transitions to `PlanStepState.FAILED`. Execution does not falsely succeed.
- **Executor Failure:** An unhandled error or `success=False` results in `ReviewStatus.REJECTED` and plan failure unless repaired within bounded attempts (`max_auto_repair_attempts`).
- **Verification Failure:** Test failure or security violation causes `ReviewStatus.REJECTED`/`NEEDS_REWORK`. If repairs are exhausted, step becomes `FAILED`.
- **Approval Required / Paused:** Tool actions requiring human authorization emit `TOOL_CONFIRM`. The executor terminates the autonomous turn cleanly with `waiting_approval=True` and marks execution as blocked (`notes="Approval required"`). It never auto-approves.

---

## 7. NoOp Policy

- `_NoOpStepExecutor` is strictly for unit testing and explicit isolated simulation fixtures.
- `_NoOpStepExecutor` produces `ImplementationResult(is_mocked=True, execution_evidence={"is_real": False})`.
- The verification gate coordinator rejects any result where `is_mocked is True` or `summary` contains `[noopstepexecutor]`.
- NoOp results CANNOT satisfy the production completion gate under any circumstances.

---

## 8. MCP `run_whole_plan` Behavior

In `my_agent_mcp/server.py`:
- `run_whole_plan` resolves the active project and task, then wires `CoderRuntimeStepExecutor`.
- If the plan does not exist, it returns `{"plan_completed": False, "error": "plan not found: ..."}`.
- If no real coder worker is available (e.g., `EXTERNAL` mode without an active worker session), execution halts truthfully with `plan_completed: False` and `steps_completed: 0`.
- The MCP tool never returns a fake `plan_completed: True`.

---

## 9. Capability Truth Relationship

- In accordance with Phase 01 (`core/capabilities.py`), `whole_plan_autonomous_step_execution` reports:
  - `Availability.MOCKED` when `_NoOpStepExecutor` or simulated executors are active.
  - `Availability.AVAILABLE` only when a concrete real executor (`is_real_executor = True`, e.g., `CoderRuntimeStepExecutor`) is wired and verified.
- The capability contract ensures callers and specialist agents know whether whole-plan execution will perform genuine workspace mutations.
