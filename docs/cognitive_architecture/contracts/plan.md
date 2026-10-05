# Contract: Plan

**Frozen at Phase 00**  
**Source:** core/plan.py

## Domain Model

```python
class PlanState(StrEnum):
    DRAFT      = "draft"
    ACTIVE     = "active"
    COMPLETED  = "completed"
    FAILED     = "failed"
    SUPERSEDED = "superseded"

class PlanStepState(StrEnum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    SKIPPED   = "skipped"

@dataclass(slots=True)
class PlanRecord:
    plan_id:    str
    task_id:    str
    revision:   int = 1         # >= 1
    state:      PlanState = PlanState.DRAFT
    created_at: datetime
    updated_at: datetime

@dataclass(slots=True)
class PlanStepRecord:
    step_id:              str
    plan_id:              str
    step_index:           int               # >= 0
    title:                str
    instruction:          str
    state:                PlanStepState = PlanStepState.PENDING
    execution_session_id: str | None = None
    assigned_role:        AgentRole = AgentRole.BACKEND_CODER
    created_at:           datetime
    updated_at:           datetime
```

## State Machines

```
PlanState:
  DRAFT -> ACTIVE, SUPERSEDED
  ACTIVE -> COMPLETED, FAILED, SUPERSEDED
  COMPLETED -> (terminal)
  FAILED    -> (terminal)
  SUPERSEDED -> (terminal)

PlanStepState:
  PENDING  -> RUNNING, SKIPPED
  RUNNING  -> COMPLETED, FAILED
  COMPLETED -> (terminal)
  FAILED    -> (terminal)
  SKIPPED   -> (terminal)
```

## Invariants

- plan_id must not be empty
- task_id must not be empty
- revision >= 1
- step_id must not be empty
- step_index >= 0
- title must not be empty
- instruction must not be empty

## Persistence

Tables: plans, plan_steps  
plan_steps.assigned_role has default 'backend_coder' (additive migration)  
plan_steps.execution_session_id added as additive migration

## SQLite Schema

```sql
CREATE TABLE plans (
    plan_id    TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    state      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE plan_steps (
    step_id              TEXT PRIMARY KEY,
    plan_id              TEXT NOT NULL,
    step_index           INTEGER NOT NULL,
    title                TEXT NOT NULL,
    instruction          TEXT NOT NULL,
    state                TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    execution_session_id TEXT,
    assigned_role        TEXT NOT NULL DEFAULT 'backend_coder'
);
```

## Contract Notes for Future Phases

- DO NOT rename existing state string values
- DO NOT remove execution_session_id or assigned_role columns
- PlanStepRecord.terminal used by WholePlanCoordinator to detect all-done condition
- New columns MUST have DEFAULT values for backward compatibility
