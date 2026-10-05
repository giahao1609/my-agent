---
name: my-handoff
description: Delegate execution and transfer task context between specialist agent roles via the guardrail-enforced Explicit Handoff Coordinator.
---

# Explicit Handoff Coordinator Workflow

Execute policy-controlled handoffs (`Explicit Handoff`) between specialist AI agent roles in strict compliance with the authorization matrix.

## Execution Guidelines

1. **Discover Authorized Target Roles**:
   - Use the MCP tool `get_allowed_handoff_targets(current_role)` to retrieve the list of permissible target roles for the next transition.

2. **Submit Handoff Request**:
   - Use the MCP tool `request_agent_handoff`:
     - `source_role`: Current active role (e.g. `backend_coder`)
     - `target_role`: Destination role (e.g. `tester`)
     - `task_id`: Task identifier
     - `payload`: Contextual handoff state and artifacts
     - `reason`: Technical rationale for delegating execution

3. **Automated Guardrail Verification**:
   - The coordinator automatically sanitizes secrets/API keys from the payload and validates prompt safety.
   - Unauthorized transitions (e.g., `backend_coder` bypassing verification directly to `release`) are automatically `REJECTED`.

4. **Inspect Audit History**:
   - Use the MCP tool `get_handoff_history(task_id)` to review the full immutable handoff audit trail.

