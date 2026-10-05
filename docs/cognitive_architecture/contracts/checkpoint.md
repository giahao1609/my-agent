# Contract: Checkpoint

**Frozen at Phase 00**  
**Source:** core/checkpoint.py

## Domain Model

```python
@dataclass(slots=True)
class CheckpointRecord:
    checkpoint_id:   str
    project_id:      str
    summary:         str
    next_action:     str | None = None
    conversation_id: str | None = None
    session_id:      str | None = None
    task_id:         str | None = None
    files_changed:   tuple[str, ...] = ()
    tests:           tuple[str, ...] = ()
    created_at:      datetime
```

## Invariants

- checkpoint_id must not be empty
- project_id must not be empty
- summary must not be empty
- task_id must not be empty if set (non-None)
- CheckpointRecord is immutable (no state machine)
- Checkpoints are append-only snapshots; never mutated after creation

## Persistence

Table: checkpoints  
Primary key: checkpoint_id  
Index: idx_checkpoints_project_task on (project_id, task_id, created_at DESC)

## SQLite Schema

See persistence/sqlite_checkpoint_store.py for actual CREATE TABLE.
Key columns: checkpoint_id, project_id, task_id, summary, next_action,
             conversation_id, session_id, files_changed_json, tests_json, created_at

## Contract Notes for Future Phases

- DO NOT add mutable state to CheckpointRecord
- New fields must be nullable with None defaults for backward compatibility
- Checkpoint retrieval is by project_id + task_id ordered by created_at DESC
- files_changed and tests are serialized as JSON arrays in persistence
