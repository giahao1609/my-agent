# Auto-Progress Continuity Policy

> **Scope:** Enforced across all workspaces and repositories when operating with the user  
> **Enforcement Level:** Strict Invariant

---

## 1. Core Principle: Auto-Save by Default

When executing tasks across any repository or project:

1. **Continuous Auto-Save Without Prompting**:
   - MyAgent **MUST AUTOMATICALLY PERSIST** task progress upon completion of every step:
     - Automatically update status of `Task`, `PlanStep`, and `Session`.
     - Automatically record `Checkpoint` entities within the SQLite durable database.
     - Automatically consolidate memory across the tiered architecture (`L0` session memory promoted to `L1` working and `L2` project memory).
   - **NEVER wait for user reminders or manual save triggers**.

2. **Cross-Restart & Cross-Model Task Continuity**:
   - When switching repositories, host runtimes, or models (Antigravity, Claude, Codex, GPT), the system must seamlessly hydrate the entire persisted task context without interruption or requiring the user to re-explain background details.

3. **Standard Execution Flow**:
   ```text
   Receive User Task
          │
          ▼
   Execute Work Step
          │
          ▼
   AUTOMATICALLY PERSIST PROGRESS & CHECKPOINT
   (Never wait for user reminder)
          │
          ▼
   Deliver Clear Verification Report to User
   ```
