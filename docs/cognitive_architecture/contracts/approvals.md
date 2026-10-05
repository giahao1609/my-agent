# Contract: Approvals

**Frozen at Phase 00**  
**Source:** core/control_plane.py, persistence/sqlite_pending_approval_store.py

## 1. PendingApprovalStore Protocol (core/control_plane.py)

```python
class PendingApprovalStore(Protocol):
    async def save(self, approval: dict[str, Any]) -> None: ...
    async def get(self, tool_call_id: str) -> dict[str, Any] | None: ...
    async def list_by_task(self, task_id: str) -> list[dict[str, Any]]: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def delete(self, tool_call_id: str) -> dict[str, Any] | None: ...
```

## 2. Approval Record Structure (dict[str, Any])

Required keys:
- tool_call_id: str  - unique identifier for the pending tool call
- tool_name:    str  - name of the tool requiring approval
- arguments:    dict - tool arguments (may be empty)
- reason:       str  - reason approval is required
- risk_explanation: str  - risk details
- prompt:       str  - human-readable approval prompt
- task_id:      str | None - associated task context

## 3. Implementations

### InMemoryPendingApprovalStore (DEFAULT - NON-DURABLE)

Status: PARTIAL - does not survive process restarts  
Location: core/control_plane.py  
Used by default when no approval_store is injected.

### SQLitePendingApprovalStore (PRODUCTION - DURABLE)

Status: IMPLEMENTED  
Location: persistence/sqlite_pending_approval_store.py  
Must be explicitly injected into MyAgentControlPlane for production use.

## 4. Approval Lifecycle

```
register_pending_approval(tool_call_id, tool_name, ...)
    -> approval_store.save(approval_dict)

Control plane detects pending approval:
    -> list_by_task(task_id) -> returns non-empty list
    -> ResponseKind.APPROVAL_REQUIRED returned to FrontAgent

User resolves:
    -> resolve_user_approval(tool_call_id, approved=True/False)
        -> approval_store.delete(tool_call_id)
        -> returns {tool_call_id, approved, rationale, status, previous_approval}
```

## 5. Integration with SessionState

SessionRecord has state WAITING_APPROVAL which corresponds to a pending approval gate.
SessionState.WAITING_APPROVAL -> SessionState.RUNNING when resolved.

## Contract Notes for Future Phases

- DO NOT change the approval dict structure keys without version adapter
- SQLitePendingApprovalStore injection MUST be documented in server.py initialization
- Approval resolution MUST atomically delete the record (no partial states)
- list_by_task MUST filter by task_id; None task_id approvals apply globally
