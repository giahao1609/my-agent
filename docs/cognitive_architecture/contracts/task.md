# Contract: Task

**Frozen at Phase 00**  
**Source:** core/task.py

## Domain Model

```python
class TaskState(StrEnum):
    CREATED   = "created"
    PLANNING  = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"

@dataclass(slots=True)
class TaskRecord:
    task_id:        str
    project_id:     str
    objective:      str
    state:          TaskState = TaskState.CREATED
    active_plan_id: str | None = None
    created_at:     datetime
    updated_at:     datetime
```

## State Machine

```
CREATED -> PLANNING, FAILED, CANCELLED
PLANNING -> EXECUTING, FAILED, CANCELLED
EXECUTING -> PLANNING, COMPLETED, FAILED, CANCELLED
COMPLETED -> (terminal)
FAILED    -> (terminal)
CANCELLED -> (terminal)
```

## Invariants

- task_id must not be empty
- project_id must not be empty
- objective must not be empty
- active_plan_id must not be empty if set (non-None)
- TaskState transitions enforced by _ALLOWED_TRANSITIONS dict

## Persistence

Table: tasks  
Primary key: task_id  
Index: idx_tasks_project on (project_id, updated_at DESC)

## SQLite Schema

```sql
CREATE TABLE tasks (
    task_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    objective     TEXT NOT NULL,
    state         TEXT NOT NULL,
    active_plan_id TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
```

## Contract Notes for Future Phases

- DO NOT add new terminal states without updating _ALLOWED_TRANSITIONS
- DO NOT rename existing state values (stored as strings in DB)
- New fields MUST be additive migrations with backward-compatible defaults
- TaskRecord.terminal property used by WholePlanCoordinator for completion detection
