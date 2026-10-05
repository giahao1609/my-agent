# Contract: Agent Execution

**Frozen at Phase 00**  
**Source:** core/protocols.py, core/agent_role.py, core/handoff_contracts.py

## 1. AgentRuntime Protocol (core/protocols.py)

```python
class AgentRuntime(Protocol):
    async def start(self, context: ExecutionContext) -> str: ...
    async def resume(self, session_id: str, context: ExecutionContext) -> None: ...
    async def send(self, session_id: str, message: str, context: ExecutionContext,
                   *, retrieval_context: Mapping[str, object] | None = None) -> None: ...
    async def set_execution_target(self, session_id: str, *,
                                   runtime_id: str | None, model_id: str | None,
                                   history: tuple[Mapping[str, object], ...] | None = None) -> None: ...
    async def cancel(self, session_id: str) -> None: ...
    async def submit_tool_result(self, session_id: str, tool_call_id: str,
                                  result: Mapping[str, object], context: ExecutionContext) -> None: ...
    def stream_events(self, session_id: str) -> AsyncIterator[AgentEvent]: ...
    async def capabilities(self) -> Sequence[CapabilityStatus]: ...
```

## 2. StepExecutor Protocol (core/whole_plan_coordinator.py)

```python
class StepExecutor(Protocol):
    async def execute_step(self, step_id: str, step_title: str,
                           step_role: AgentRole, repair_hint: str = "") -> ImplementationResult: ...
```

Concrete implementations:
- _NoOpStepExecutor: MOCKED - returns synthetic ImplementationResult
- CoderRuntimeWorker (injected): REAL production executor

## 3. AgentRole Enum (core/agent_role.py)

```python
class AgentRole(StrEnum):
    PLANNER           = 'planner'
    ARCHITECT         = 'architect'
    RESEARCHER        = 'researcher'
    BACKEND_CODER     = 'backend_coder'
    UI_CODER          = 'ui_coder'
    TESTER            = 'tester'
    SECURITY_REVIEWER = 'security_reviewer'
    REVIEWER          = 'reviewer'
    DB_MIGRATION      = 'db_migration'
    PERFORMANCE       = 'performance'
    DOCUMENTATION     = 'documentation'
    RELEASE           = 'release'
    USER_INTERFACE    = 'user_interface'
```

## 4. Key Result Types (core/handoff_contracts.py)

```python
@dataclass(frozen=True, slots=True)
class ImplementationResult:
    step_id:        str
    summary:        str
    modified_files: tuple[str, ...] = ()
    created_files:  tuple[str, ...] = ()
    deleted_files:  tuple[str, ...] = ()
    success:        bool = True

@dataclass(frozen=True, slots=True)
class ReviewResult:
    step_id:          str
    status:           ReviewStatus  # APPROVED / REJECTED / REQUEST_REWORK
    summary:          str
    comments:         tuple[str, ...] = ()
    required_repairs: tuple[str, ...] = ()

class ReviewStatus(StrEnum):
    APPROVED       = 'approved'
    REJECTED       = 'rejected'
    REQUEST_REWORK = 'request_rework'
```

## 5. AgentRunRecord (core/agent_run.py)

Used for durable execution ledger:

```python
class AgentRunState(StrEnum):
    PENDING  = "pending"
    RUNNING  = "running"
    COMPLETED = "completed"
    FAILED   = "failed"
    CANCELLED = "cancelled"
```

## Contract Notes for Future Phases

- DO NOT change AgentRole string values (stored in plan_steps.assigned_role)
- DO NOT change ImplementationResult fields without backward compat wrapper
- StepExecutor injection is the ONLY approved way to connect real agent runtimes to WholePlanCoordinator
- ReviewStatus.APPROVED is the gate pass condition in WholePlanCoordinator
- AgentRuntime.set_execution_target() is the cross-model handoff mechanism
