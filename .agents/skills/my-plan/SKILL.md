---
name: my-plan
description: Multi-step task decomposition and autonomous specialist agent coordination via WholePlanCoordinator.
---

# Planner & Whole-Plan Coordinator Workflow

Activate the **Planner Agent (`AgentRole.PLANNER`)** to decompose complex engineering objectives into role-bound PlanSteps (`assigned_role`).

## Execution Guidelines

1. **Plan Formulation & Proposal**:
   - Analyze requirements and invoke `create_task` & `propose_task_plan`.
   - Each `PlanStep` must bind explicitly to a specialized agent role:
     - `planner`: Plan decomposition and structuring
     - `architect`: Interface and data model design
     - `backend_coder`: Business logic and API implementation
     - `ui_coder`: Interface implementation and styling
     - `tester`: Automated test suite authoring and execution
     - `security_reviewer`: SAST, secret, and red-team audits
     - `reviewer`: Synthesis and final verification

2. **Autonomous Multi-Step Execution**:
   - Use the MCP tool `run_whole_plan(plan_id, workspace_path)` to orchestrate all plan steps through `WholePlanCoordinator`.
   - Each step transitions deterministically through verification gates (Test Gate -> Security Gate -> Reviewer Gate) before advancing.

