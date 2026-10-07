# External reasoning bridge — Phase 04.5A

## Audit before implementation

Baseline: `2f121bff035bbdb9f24baed0f6e9a6d74bcf0c74`, clean `main`;
existing `.venv` runs Python 3.13.11. No interpreter or dependency changes.

* Project authority: `ProjectStore.get_active/get` and `SQLiteProjectStore`
  hold string IDs and workspace paths. IDs need not be opaque. `register_project`
  accepts caller-provided paths; `open_workspace` can register workspaces.
  Neither operation belongs in the bridge. The bridge must resolve only the
  active registered project, reject mismatches, and use an opaque outbound alias.
* MCP: `my_agent_mcp/server.py` registers local tools with `@mcp.tool()`.
  Existing tools include project enumeration/registration, memory, CodeGraph,
  coder execution/approval, security inspection and scans. These are a broader
  local administration surface, not an external reasoning API. No transport or
  tool registration changes are needed.
* Memory: `DurableMemoryService.retrieve(project_id, query, limit=...,
  include_global=False)` already provides ranked project-only retrieval. Empty
  queries permit browsing, so the bridge must reject them. `write` rejects the
  reserved `__global__` sentinel; canonical global scope uses `None`.
* Source: `CodeGraphBackend` supports project-scoped nodes and semantic edges.
  Its existing neighbor traversal lacks a result limit. Workspace `read_file`
  resolves paths inside the workspace, but reads the entire file before slicing.
  Small optional bounded-read and neighbor-limit compatibility extensions are
  needed; preserve existing callers' behavior.
* Security: `SecurityScannerRegistry` scans workspaces; `SecretRedactor` provides
  reusable text rules, and `PiiSanitizer` handles PII. Text rules need supplemental
  authorization/header, unquoted-credential and private-path protection at the
  disclosure boundary. `ToolPolicy`/`ToolExecutor` own execution permissions.
  Existing scanner findings can carry source excerpts: do not export them raw.
* Execution: `CoderRuntimeStepExecutor` drives the existing coder stack and
  `StepExecutionEvidence`; WholePlan uses `VerificationGateCoordinator` with
  real-execution checks. Host/runtime selection already supports Codex. The
  bridge will return routing recommendations, never create another executor or
  treat reasoning as evidence.
* Handoff: `AgentHandoffContextBuilder` prunes prior runs but includes raw run
  history and lacks project/disclosure/serialized-size authority. Its output is
  not suitable for automatic external export. Reuse selected existing evidence
  types, not its full history bundle.

Phase 04.5A performs ZERO external model calls.

## Implementation details

1. **Context disclosure (`core/context_disclosure.py`)**:
   - `BridgeErrorCode` & `BridgeError`: Uniform code-only errors preventing backend/stack leak.
   - `DisclosureLevel` & `DisclosurePolicy`: Hierarchical gate enforcing maximum permitted disclosure level with mandatory justification for expanded context.
   - `ContextBudget`: Strict limits on code snippets (6), memory matches (5), lines per snippet (120), graph neighbors (12), depth (1), and total serialized characters (24,000).
   - `SensitiveDataGate`: Deterministic redaction of credentials, bearer tokens, paths, PII, embedded URL credentials, and secret-bearing query parameters (`?token=`, `?sig=`, `?signature=`, `?X-Amz-Signature=`, `?X-Goog-Signature=`), preserving safe public documentation URLs and failing closed on PEM headers, null bytes, and high-entropy secrets (Shannon entropy >= 4.5). Tested false-positive cases pass on legitimate software metadata, hex hashes, and public URLs.
   - `DisclosureManifest` & `DisclosureEnvelope`: Auditable manifest tracking request ID, task ID, opaque project alias, source categories, redaction counts, and omission counts.

2. **Context broker (`core/context_broker.py`)**:
   - `ProjectScopeValidator`: Resolves active registered project from `ProjectStore`, rejects unauthenticated/mismatched projects, and assigns stable opaque outbound alias (`proj_<uuid>`).
   - Rejects path traversal, symlinks, and nested `.git` submodules.
   - `ContextBroker`: Assembles bounded context items across code snippets, durable memory queries (`DurableMemoryService`), and semantic code graph nodes (`CodeGraphBackend`).

3. **Reasoning bridge (`core/reasoning_bridge.py`)**:
   - `TaskCapabilityAssessment`: Deterministic assessment of task requirements against `CapabilityRegistry` (SELF_EXECUTABLE, NEEDS_REASONING, NEEDS_CODEX, NEEDS_APPROVAL, BLOCKED).
   - `ExecutionRoute`: Explicit routing mapping without executing untrusted actions.
   - `ReasoningRequest`: Assembles wire-safe request with opaque IDs, sanitized objective, constraints, and bounded disclosure envelope.
   - `ReasoningDecision`: Strict payload validation ensuring required fields, bounded size, and valid execution recommendations.
   - `ReassessedDecision`: External recommendation is strictly advisory only and treated as `UNVERIFIED_EXTERNAL_REASONING`. Final routing derives exclusively from server-owned facts (`TaskRequirements`, `CapabilityRegistry`, security/policy, approvals); external recommendations may request escalation to `NEEDS_APPROVAL`, but cannot force `CODEX`, grant approval, or authorize `SELF_EXECUTABLE`.

4. **Compatibility extensions**:
   - `tools/workspace_tools.py`: Added `max_chars` bounded read capability to `_read_file` bounding the selected returned range without materializing the whole file into memory.
   - `core/code_graph.py` & `persistence/sqlite_code_graph_store.py`: Added optional `limit` parameter to `semantic_dependencies` and `semantic_dependents`.

## Validation

- **Targeted suite:** 60 test functions collected as 88 test cases in `tests/test_reasoning_bridge_foundation.py` covering path validation, redaction, budget gates, project scope isolation, context assembly, capability assessment, proposal parsing, bounded reading, prompt injection defense, and graph limits.
- **Repository regression suite:** 880 passing tests (100% pass rate, exit code 0).

