# PHASE 04.5A HANDOFF — External Reasoning Bridge Foundation

## BASE_COMMIT
`2f121bff035bbdb9f24baed0f6e9a6d74bcf0c74`

## CURRENT_HEAD
`2f121bff035bbdb9f24baed0f6e9a6d74bcf0c74`

## BRANCH
`feat/houhou-04-5a-reasoning-bridge-foundation`

---

## PYTHON_ENVIRONMENT
- **PYTHON_VERSION:** Python 3.13.11
- **PYTHON_EXECUTABLE:** `/Users/haohg/Project/my-agent/.venv/bin/python`
- **PYTHON_RUNTIME_CHANGED:** False

---

## AUDIT_SUMMARY
Phase 04.5A delivers the foundational, provider-independent External Reasoning Bridge for MyAgent.
The implementation establishes a clean, bounded disclosure boundary (`ContextBroker`, `SensitiveDataGate`), deterministic capability assessment and routing (`ReasoningBridgeService`), fail-closed secret/PII filtering where tested false-positive cases pass on legitimate hashes/identifiers, secret-bearing URL query/userinfo redaction with public documentation URL preservation, strict project isolation, and backward-compatible extensions to `_read_file` (`max_chars`) and `CodeGraphBackend` (`limit`).

In strict accordance with the Phase 04.5A architecture invariants:
- **ZERO external network calls are performed.**
- **ZERO provider-specific code or SDKs are added.**
- **All external reasoning proposals are treated strictly as `UNVERIFIED_EXTERNAL_REASONING`.**
- **Reassessment independently evaluates proposals against MyAgent capability, security, and approval policy.**
- **No commits, pushes, tags, or merges were performed; all changes remain in the working tree for explicit human review.**

---

## FILES_CHANGED
- `core/context_disclosure.py` (New): Data gate, disclosure levels, context budget, and deterministic redaction.
- `core/context_broker.py` (New): Project-authoritative bounded context selection across code, memory, graph, and evidence.
- `core/reasoning_bridge.py` (New): Task capability assessment, execution routing, request assembly, and proposal governance.
- `core/code_graph.py` (Modified): Added optional `limit` parameter to `semantic_dependencies` and `semantic_dependents`.
- `persistence/sqlite_code_graph_store.py` (Modified): Implemented SQL query-level `LIMIT` traversal.
- `tools/workspace_tools.py` (Modified): Added optional `max_chars` bounded read to `_read_file`.
- `docs/architecture/external_reasoning_bridge.md` (Modified): Architecture specifications and verification record.
- `tests/test_reasoning_bridge_foundation.py` (New): 60 test functions collected as 88 automated test cases covering all 76 behavioral requirements.
- `docs/handoffs/PHASE_04_5A_HANDOFF.md` (New): Authoritative completion and governance handoff artifact.

---

## NEW_PUBLIC_CONTRACTS
- `core.context_disclosure.BridgeErrorCode(StrEnum)`: Canonical boundary error vocabulary (`PROJECT_CONTEXT_REQUIRED`, `PROJECT_SCOPE_DENIED`, `DISCLOSURE_DENIED`, `CONTEXT_BUDGET_EXCEEDED`, `SENSITIVE_CONTEXT_DENIED`, `CAPABILITY_UNAVAILABLE`, `APPROVAL_REQUIRED`, `EXTERNAL_REASONING_INVALID`, `EXECUTION_BLOCKED`).
- `core.context_disclosure.BridgeError(ValueError)`: Opaque exception carrying only standard `BridgeErrorCode`.
- `core.context_disclosure.DisclosureLevel(IntEnum)`: Hierarchical gate (`SAFE_METADATA=0`, `SUMMARY=1`, `SELECTED_EVIDENCE=2`, `EXPANDED_PROJECT_CONTEXT=3`, `PROJECT_WIDE_OR_SENSITIVE=4`).
- `core.context_disclosure.ContextBudget(dataclass)`: Hard limits on code snippets, lines, memory matches, graph neighbors, and serialized characters.
- `core.context_disclosure.DisclosurePolicy(dataclass)`: Enforces level ceilings and expanded context justifications.
- `core.context_disclosure.SensitiveDataGate`: Deterministic redaction and fail-closed secret/PII inspection.
- `core.context_broker.ContextBroker`: Orchestrator for small-first, sanitized context extraction.
- `core.context_broker.ProjectScopeValidator`: ProjectStore resolution and opaque outbound alias isolation.
- `core.reasoning_bridge.ReasoningBridgeService`: Single entry point for capability assessment, routing, request creation, and reassessment.
- `core.reasoning_bridge.TaskRequirements` & `TaskCapabilityAssessment`: Deterministic task capability contracts.
- `core.reasoning_bridge.ExecutionRoute(StrEnum)`: `NATIVE`, `REASONING_HANDOFF`, `CODEX`, `APPROVAL`, `STOP`.
- `core.reasoning_bridge.ReasoningRequest` & `ReasoningDecision`: Wire-safe payload contracts.
- `core.reasoning_bridge.ReassessedDecision`: Proposals stamped with `trust="UNVERIFIED_EXTERNAL_REASONING"`.

---

## NEW_INTERNAL_CONTRACTS
- `core.context_broker.ProjectScope`: Slotted internal binding `(project_id, workspace_path, external_id)`.
- `core.context_broker.SourceRange`, `GraphSelection`, `SelectedEvidence`, `ContextRequest`: Typed context selection inputs.
- `core.context_disclosure.ContextItem`, `DisclosedSource`, `DisclosureManifest`, `DisclosureEnvelope`: Structured audit-manifest payloads.

---

## TASK_CAPABILITY_ASSESSMENT
`ReasoningBridgeService.assess()` evaluates tasks against real, active capabilities registered in `CapabilityRegistry`:
- `SELF_EXECUTABLE`: Required capabilities available natively; safe deterministic operations.
- `NEEDS_REASONING`: Architectural trade-offs or unstructured planning required.
- `NEEDS_CODEX`: Code synthesis exceeding native workspace tools required.
- `NEEDS_APPROVAL`: Destructive, permission-gated, or high-risk operations.
- `BLOCKED`: Missing registered capabilities, missing project context, or server execution denied.

---

## PROJECT_CONTEXT_AUTHORITY & ISOLATION
- **Authority:** `ProjectScopeValidator` derives authority strictly from `ProjectStore.get_active()` and `ProjectStore.get()`.
- **Project Enumeration Policy:** Zero project enumeration APIs exist on the bridge. Missing active project context raises `PROJECT_CONTEXT_REQUIRED` with payload `{"error": "PROJECT_CONTEXT_REQUIRED"}` without disclosing candidate projects or directory structures.
- **Cross-Project Policy:** Requests targeting a project ID differing from the active project are immediately rejected with `PROJECT_SCOPE_DENIED`. Project A cannot inspect Project B source, memory, CodeGraph, or Git diffs.
- **Opaque Project Alias:** Internal workspace paths and real project IDs are masked behind a stable random alias (`proj_<uuid>`). The alias is strictly a display/disclosure identifier and cannot be forged or used as an authorization identity.

---

## CONTEXT_BROKER & BUDGET
- **Disclosure Levels:** Enforced via `DisclosurePolicy`. Default ceiling is `SELECTED_EVIDENCE` (2). `EXPANDED_PROJECT_CONTEXT` (3) requires explicit non-empty justification. `PROJECT_WIDE_OR_SENSITIVE` (4) is unconditionally rejected.
- **Context Budget Defaults:**
  - `max_code_snippets`: 6
  - `max_lines_per_snippet`: 120
  - `max_memory_matches`: 5
  - `max_graph_neighbors`: 12
  - `max_graph_depth`: 1
  - `max_git_diff_chars`: 8000
  - `max_test_output_chars`: 6000
  - `max_total_context_chars`: 24000
  - `max_input_chars`: 48000
- **Small-First Retrieval:**
  - Code: `_read_file` utilizes `max_chars` bounded scanning without materializing the entire file.
  - Graph: `SQLiteCodeGraphStore.semantic_dependencies` enforces SQL query-level `LIMIT`.
  - Memory: `DurableMemoryService.retrieve` applies `limit` parameter.

---

## MEMORY_ACCESS_PATH
- `ContextBroker` connects exclusively to `DurableMemoryService.retrieve()`.
- Zero direct SQLite memory access or raw queries in the bridge.
- `include_global=False` by default; queries targeting other projects are filtered out.
- Existing Phase 04 contracts preserved: canonical global `project_id=None`, reserved `"__global__"` writes rejected.

---

## CODE_CONTEXT_PATH, GIT_CONTEXT_PATH, TEST_CONTEXT_PATH
- **Code:** Read via `ToolExecutor.execute("read_file", ...)` under active session `ExecutionContext` with path traversal and symlink prevention.
- **Git Context:** Sanitized diff excerpts capped at `max_git_diff_chars`.
- **Test Context:** Sanitized test outputs capped at `max_test_output_chars`.

---

## SECRET_FILTERING & PROMPT_INJECTION_POLICY
- `SensitiveDataGate.validate_path`: Fails closed on absolute paths, directory traversal (`..`, `.`, `~`), backslashes, sensitive directories (`.env*`, `.git`, `.ssh`, `.aws`, `.gnupg`, `.venv`), credential files (`credentials*`, `secrets*`, `id_rsa*`), and sensitive extensions (`.pem`, `.key`, `.p12`, `.sqlite*`).
- `SensitiveDataGate.sanitize`: Deterministic redaction of passwords, API keys, tokens, authorization headers, bearer tokens, and PII.
- **Entropy & False Positive Mitigation:** Detects high-entropy tokens (Shannon entropy >= 4.5). Excludes legitimate hex hashes (Git commit SHAs, SHA-256 checksums, UUIDs, `proj_<uuid>`), ordinary identifiers, safe public URLs, and source code. Secret-bearing query parameters (`?token=`, `?sig=`, `?signature=`, `?X-Amz-Signature=`, `?X-Goog-Signature=`) and URL userinfo are redacted to `[REDACTED_SECRET]` and `[REDACTED_CREDENTIAL]`, while non-secret public URLs remain intact.
- **Fail-Closed Gate:** Null bytes (`\x00`) and private key blocks (`-----BEGIN`) immediately fail closed with `SENSITIVE_CONTEXT_DENIED`. Unclassified high-entropy strings in URLs or content also fail closed.

---

## REASONING_REQUEST & REASONING_DECISION CONTRACTS
- **Request:** Opaque `request_id` (`req_<uuid>`), external `task_id` (`task_<uuid>`), external `project_id` (`proj_<uuid>`), sanitized objective, problem summary, constraints, unknowns, attempted actions, available capabilities, and audited `DisclosureEnvelope`. Total serialized wire size strictly bounded <= 24,000 characters.
- **Decision:** Validated payload with required fields (`request_id`, `diagnosis`, `proposed_plan`, `constraints`, `acceptance_criteria`, `verification_plan`, `recommended_execution_class`). Payload exceeding `max_input_chars` is rejected with `EXTERNAL_REASONING_INVALID`.

---

## EXTERNAL_REASONING_TRUST_MODEL & REASSESSMENT SEMANTICS
- `ReassessedDecision.trust` is immutably set to `"UNVERIFIED_EXTERNAL_REASONING"`.
- External proposals **CANNOT** directly authorize an operation, alter project scope, grant approval, invoke Codex, create `ExecutionEvidence`, or mark work complete.
- `ReasoningBridgeService.reassess()` independently re-evaluates the proposed plan:
  - Blocked tasks remain `BLOCKED` (Route: `STOP`).
  - High-risk, approval-gated, or destructive steps (`rm -rf`, `delete_path`, etc.) force `NEEDS_APPROVAL` (Route: `APPROVAL`).
  - Proposals requiring code synthesis resolve to `NEEDS_CODEX` (Route: `CODEX`).
  - Safe, deterministic proposals with verified native capabilities resolve truthfully to `SELF_EXECUTABLE` (Route: `NATIVE`).
- The correlation request ID is consumed upon reassessment; duplicate or forged IDs are rejected.

---

## BOUNDARY ASSIGNMENTS
- **Native Execution Boundary:** Governed exclusively by `ToolPolicy` and `ToolExecutor`.
- **Codex Boundary:** Routed via `ExecutionRoute.CODEX` to `CoderRuntimeStepExecutor`.
- **Execution Evidence Boundary:** Reasoning proposals cannot produce execution evidence; only real runtime executions generate `StepExecutionEvidence`.
- **Verification Gate Boundary:** WholePlan verification continues to require deterministic test execution via `VerificationGateCoordinator`.

---

## NETWORK & PROVIDER AUDIT
- **NETWORK_CALLS_ADDED = NONE**
- **CHATGPT_SPECIFIC_CODE_ADDED = NONE**
- Verified via AST static analysis in `TestZeroNetworkAndProviderIndependence`.

---

## REQUIREMENT-TO-TEST TRACEABILITY MATRIX (01–76)

| REQ | SPECIFICATION REQUIREMENT | TEST FUNCTION | RESULT |
|:---|:---|:---|:---|
| 01 | Task capability status enum definition | `TestReasoningBridgeService.test_assess_routes` | PASS |
| 02 | Task capability SELF_EXECUTABLE assessment | `TestReasoningBridgeService.test_assess_routes` | PASS |
| 03 | Task capability NEEDS_REASONING assessment | `TestReasoningBridgeService.test_assess_routes` | PASS |
| 04 | Task capability NEEDS_CODEX assessment | `TestReasoningBridgeService.test_assess_routes` | PASS |
| 05 | Task capability NEEDS_APPROVAL assessment | `TestReasoningBridgeService.test_assess_routes` | PASS |
| 06 | Task capability BLOCKED on missing capabilities | `TestReasoningBridgeService.test_assess_routes` | PASS |
| 07 | Task capability BLOCKED on denied execution | `TestReasoningBridgeService.test_assess_execution_denied_is_blocked` | PASS |
| 08 | Task capability BLOCKED on missing project context | `TestReasoningBridgeService.test_assess_missing_project_context_is_blocked` | PASS |
| 09 | ExecutionRoute mapping for all statuses | `TestReasoningBridgeService.test_execution_route_mapping_for_all_statuses` | PASS |
| 10 | Route to STOP on blocked task | `TestReasoningBridgeService.test_route_blocked_task_returns_stop` | PASS |
| 11 | Active project resolution from ProjectStore | `TestProjectScopeValidator.test_resolve_active_project` | PASS |
| 12 | ProjectStore strict validation | `TestProjectScopeValidator.test_resolve_active_project` | PASS |
| 13 | Mismatched project ID rejection | `TestProjectScopeValidator.test_resolve_mismatched_project_denied` | PASS |
| 14 | Missing active project returns PROJECT_CONTEXT_REQUIRED | `TestProjectIsolationAudit.test_missing_project_context_fails_closed_without_enumeration` | PASS |
| 15 | No project enumeration or candidate leakage | `TestProjectIsolationAudit.test_missing_project_context_fails_closed_without_enumeration` | PASS |
| 16 | Cross-project isolation between Project A and B | `TestProjectIsolationAudit.test_cross_project_isolation` | PASS |
| 17 | Symlink source path rejection | `TestProjectScopeValidator.test_source_path_symlink_denied` | PASS |
| 18 | Nested .git submodule source path rejection | `TestProjectScopeValidator.test_source_path_nested_git_denied` | PASS |
| 19 | Opaque project alias generation (proj_<uuid>) | `TestProjectScopeValidator.test_resolve_active_project` | PASS |
| 20 | Opaque alias stability across repeat queries | `TestProjectScopeValidator.test_resolve_active_project` | PASS |
| 21 | Opaque alias is display-only and cannot be forged | `TestProjectIsolationAudit.test_opaque_alias_is_display_only_not_authorizer` | PASS |
| 22 | Path validation rejects absolute paths | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 23 | Path validation rejects directory traversal (..) | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 24 | Path validation rejects tilde (~) expansion | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 25 | Path validation rejects Windows backslashes | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 26 | Path validation rejects .env and secret filenames | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 27 | Path validation rejects private keys (.pem, .key) | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 28 | Path validation rejects sqlite database files | `TestSensitiveDataGate.test_validate_path_rejected` | PASS |
| 29 | Path validation allows valid safe project paths | `TestSensitiveDataGate.test_validate_path_valid` | PASS |
| 30 | Credential redaction in text content | `TestSensitiveDataGate.test_sanitize_credentials_and_bearer` | PASS |
| 31 | Bearer and Basic token redaction | `TestSensitiveDataGate.test_sanitize_bearer_and_basic_auth_tokens` | PASS |
| 32 | Local filesystem path redaction | `TestSensitiveDataGate.test_sanitize_local_filesystem_paths` | PASS |
| 33 | PII email and phone redaction | `TestSensitiveDataGate.test_sanitize_pii` | PASS |
| 34 | Fail closed on PEM private key blocks | `TestSensitiveDataGate.test_sanitize_pem_and_null_bytes_fail_closed` | PASS |
| 35 | Fail closed on null bytes in text | `TestSensitiveDataGate.test_sanitize_pem_and_null_bytes_fail_closed` | PASS |
| 36 | Fail closed on genuine high-entropy secrets (entropy >= 4.5) | `TestSafeHighEntropyContentAudit.test_genuine_high_entropy_secret_denied` | PASS |
| 37 | Tested false-positive cases pass on Git commit SHAs | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 38 | Tested false-positive cases pass on SHA-256 checksums | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 39 | Tested false-positive cases pass on UUID strings | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 40 | Tested false-positive cases pass on proj_<uuid> aliases | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 41 | Tested false-positive cases pass on long class and function names | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 42 | Tested false-positive cases pass on package lock and version strings | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 43 | Tested false-positive cases pass on standard source code | `TestSafeHighEntropyContentAudit.test_legitimate_content_not_denied` | PASS |
| 44 | ContextBudget parameters validation | `TestContextBudgetAndPolicy.test_context_budget_validation` | PASS |
| 45 | ContextBudget rejection of negative limits | `TestContextBudgetAndPolicy.test_context_budget_validation` | PASS |
| 46 | DisclosurePolicy level hierarchy validation | `TestContextBudgetAndPolicy.test_disclosure_policy_validation` | PASS |
| 47 | DisclosurePolicy rejection of EXPANDED without justification | `TestContextBudgetAndPolicy.test_disclosure_policy_validation` | PASS |
| 48 | DisclosurePolicy acceptance of EXPANDED with justification | `TestContextBudgetAndPolicy.test_disclosure_policy_validation` | PASS |
| 49 | DisclosurePolicy unconditional rejection of PROJECT_WIDE | `TestContextBudgetAndPolicy.test_disclosure_policy_validation` | PASS |
| 50 | ContextBroker assembly of code snippet items | `TestContextBroker.test_build_context_bundle` | PASS |
| 51 | ContextBroker assembly of memory items | `TestContextBroker.test_build_context_bundle` | PASS |
| 52 | ContextBroker assembly of CodeGraph items | `TestContextBroker.test_context_broker_codegraph_evidence` | PASS |
| 53 | ContextBroker audit manifest accounting | `TestContextBroker.test_context_broker_manifest_accounting` | PASS |
| 54 | Bounded reading via _read_file max_chars parameter | `TestBoundedReadFile.test_bounded_read_shallow_range` | PASS |
| 55 | Bounded read failure when scan budget exceeded | `TestBoundedReadFile.test_bounded_read_exceeding_budget_fails_safely` | PASS |
| 56 | CodeGraph semantic traversal with query-level limit | `TestCodeGraphSemanticLimit.test_semantic_dependencies_with_limit` | PASS |
| 57 | ContextBroker accesses memory via DurableMemoryService only | `TestMemoryBoundaryAudit.test_context_broker_memory_isolation_and_no_global` | PASS |
| 58 | Memory retrieval defaults to include_global=False | `TestMemoryBoundaryAudit.test_context_broker_memory_isolation_and_no_global` | PASS |
| 59 | Memory retrieval enforces project isolation | `TestMemoryBoundaryAudit.test_context_broker_memory_isolation_and_no_global` | PASS |
| 60 | ReasoningRequest generation with opaque request_id | `TestReasoningBridgeService.test_build_request_and_reassess_flow` | PASS |
| 61 | ReasoningRequest generation with opaque task_id | `TestReasoningBridgeService.test_build_request_and_reassess_flow` | PASS |
| 62 | ReasoningRequest sanitized objective and summary | `TestReasoningBridgeService.test_build_request_sanitizes_objective_and_summary_with_secrets` | PASS |
| 63 | ReasoningRequest wire size budget enforcement (<= 24,000 chars) | `TestReasoningBridgeService.test_build_request_wire_budget_enforcement_with_oversized_context` | PASS |
| 64 | ReasoningDecision payload schema validation | `TestReasoningBridgeService.test_reassess_rejects_invalid_schemas` | PASS |
| 65 | Reassessment sets trust to UNVERIFIED_EXTERNAL_REASONING | `TestReasoningBridgeService.test_build_request_and_reassess_flow` | PASS |
| 66 | Reassessment evaluates safe deterministic plan to SELF_EXECUTABLE | `TestReasoningBridgeService.test_build_request_and_reassess_flow` | PASS |
| 67 | Reassessment evaluates destructive plan to NEEDS_APPROVAL | `TestReasoningBridgeService.test_reassess_destructive_proposal_forces_approval` | PASS |
| 68 | Reassessment evaluates code synthesis to NEEDS_CODEX | `TestReasoningBridgeService.test_reassess_code_synthesis_resolves_to_codex` | PASS |
| 69 | Reassessment consumes correlation ID once | `TestReasoningBridgeService.test_build_request_and_reassess_flow` | PASS |
| 70 | Reassessment rejects duplicate or unknown request IDs | `TestReasoningBridgeService.test_build_request_and_reassess_flow` | PASS |
| 71 | Discard request releases correlation state cleanly | `TestReasoningBridgeService.test_discard_request_removes_pending_correlation` | PASS |
| 72 | Backward compatibility: _read_file without max_chars | `TestBackwardCompatibilityAudit.test_read_file_omitted_max_chars_preserves_behavior` | PASS |
| 73 | Backward compatibility: CodeGraph without limit | `TestBackwardCompatibilityAudit.test_code_graph_omitted_limit_preserves_behavior` | PASS |
| 74 | Zero network calls or client libraries added | `TestZeroNetworkAndProviderIndependence.test_no_network_libraries_in_bridge` | PASS |
| 75 | Zero ChatGPT/provider-specific logic in bridge modules | `TestZeroNetworkAndProviderIndependence.test_no_network_libraries_in_bridge` | PASS |
| 76 | Global memory contract preserved (None vs reserved sentinel) | `TestGlobalMemoryContract.test_global_memory_contract_all_four_facets` | PASS |

---

## TEST_RESULTS
- **TARGETED_TEST_RESULT:** `88 passed in 1.57s` (`tests/test_reasoning_bridge_foundation.py`: 60 test functions, 88 collected cases)
- **PHASE04_MEMORY_TEST_RESULT:** `57 passed in 4.37s` (`tests/test_unified_memory_model.py`, `tests/test_durable_memory_matrix.py`)
- **FULL_TEST_RESULT:** `880 passed in 17.68s` (0 failures, 100% pass rate)
- **GIT_DIFF_CHECK:** Clean (no whitespace or conflict errors)

---

## KNOWN_LIMITATIONS
- Phase 04.5A is strictly the foundation layer; it establishes zero network transports or external model API integrations.
- Provider client implementations (e.g. ChatGPT / OpenAI / Claude transports) belong exclusively to subsequent phases (Phase 04.5B+).

---

## BLOCKED_DEPENDENCIES
- None.

---

## PROVIDER_HANDOFF
```yaml
previous_provider: Codex
current_provider: Gemini
handoff_reason: QUOTA_EXHAUSTED
existing_work_preserved: true
```

---

## PHASE_04_5A_STATUS
`READY_FOR_FINAL_REVIEW`
