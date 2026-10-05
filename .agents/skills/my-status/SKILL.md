---
name: my-status
description: Inspect holistic MyAgent system status, active project resolution, circuit breaker health, and execution token budget tracking.
---

# System Status & Budget Monitoring Workflow

Monitor and inspect the operational status of the MyAgent Control Plane and underlying infrastructure.

## Execution Guidelines

1. **System Health & Runtime Inspection**:
   - Use the MCP tool `my_agent_status` to view the active project, coder runtime mode (external vs in-process), database path, and active background workers.

2. **Provider Health & Circuit Breaker State**:
   - Use the MCP tool `get_provider_health_status` to view circuit breaker states (`CLOSED`, `OPEN`, `HALF_OPEN`) and error rates across model backends (OpenAI, Anthropic, Google, Local).

3. **Execution Budget & Cost Tracking**:
   - Use the MCP tool `get_task_budget(task_id)` to review cumulative USD spent, tokens consumed, and remaining allocated quota.
   - Use the MCP tool `set_task_budget(task_id, max_cost_usd, max_tokens)` to dynamically enforce strict execution ceilings when required.

