# Contract: Persistence

**Frozen at Phase 00**  
**Source:** persistence/*.py

## 1. Architecture

All persistence uses SQLite via asyncio.to_thread() for non-blocking I/O.
Single database file: data/my_agent.db
WAL journal mode enabled on all stores.

## 2. Store Pattern

Each store follows this pattern:

```python
class SQLiteXxxStore:
    def __init__(self, database_path: str | Path) -> None: ...
    async def initialize(self) -> None: ...  # CREATE TABLE IF NOT EXISTS
    def _connect(self) -> sqlite3.Connection: ...
    def _initialize_sync(self) -> None: ...
```

## 3. All Durable Stores

| Store | Table(s) | Primary Key |
|-------|----------|-------------|
| SQLiteProjectStore | projects | project_id |
| SQLiteTaskStore | tasks | task_id |
| SQLitePlanStore | plans, plan_steps | plan_id, step_id |
| SQLiteCheckpointStore | checkpoints | checkpoint_id |
| SQLiteMemoryStore | memories | memory_id |
| SQLiteDecisionStore | decisions | decision_id |
| SQLiteAgentRunStore | agent_runs | run_id |
| SQLiteCoderSessionStore | coder_sessions | session_id |
| SQLiteConversationStore | conversations, messages | conversation_id |
| SQLiteCodeGraphStore | code_nodes, code_edges, code_graph_files, code_graph_status, code_nodes_fts | node_id |
| SQLitePendingApprovalStore | (pending_approvals) | tool_call_id |

## 4. Migration Policy

- All schema changes MUST be additive (ADD COLUMN with DEFAULT)
- NEVER DROP TABLE, DROP COLUMN, or rename columns
- All new tables use CREATE TABLE IF NOT EXISTS
- Old records MUST remain readable after migrations
- Migration is implicit at store initialization (no migration runner exists)

## 5. Serialization Conventions

- datetimes: ISO-8601 strings (TEXT columns)
- tuples/lists: JSON arrays (TEXT columns, suffix _json)
- dicts/mappings: JSON objects (TEXT columns, suffix _json)
- enums: stored as their string value

## 6. Non-Durable Store

InMemoryPendingApprovalStore (core/control_plane.py):
- dict[str, dict[str, Any]] in-process
- Lost on process restart
- Use SQLitePendingApprovalStore for production

## Contract Notes for Future Phases

- Any new SQLite table MUST follow CREATE TABLE IF NOT EXISTS pattern
- Store initialization MUST be idempotent
- No foreign key constraints enforced at SQLite level (application-level integrity)
- All stores share the same database file path
- DO NOT introduce a second database file without explicit phase approval
