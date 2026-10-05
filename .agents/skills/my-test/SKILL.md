---
name: my-test
description: Execute deterministic automated test suites (pytest, npm test, go test, cargo test) and evaluate the Verification Gate for plan steps.
---

# Test Runner & Deterministic Verification Workflow

Activate the **Tester Agent (`AgentRole.TESTER`)** to execute objective test suites and evaluate deterministic verification gates.

## Execution Guidelines

1. **Automated Test Runner Detection**:
   - The engine automatically detects the appropriate test framework for the active workspace:
     - Python: `pytest` / `unittest`
     - JavaScript / TypeScript: `npm test` / `jest` / `vitest`
     - Go: `go test`
     - Rust: `cargo test`

2. **Verification Gate Evaluation**:
   - Use the MCP tool `verify_step(step_id, workspace_path)` to execute the test suite combined with the security scanner.
   - Evaluates to one of three deterministic verdicts:
     - `APPROVED`: 100% tests pass cleanly with zero regressions and zero security gate failures.
     - `REQUEST_REWORK`: Failures detected; returns structured failure logs and rework instructions to the Coder.
     - `REJECTED`: Critical architecture or security violation requiring re-planning.

