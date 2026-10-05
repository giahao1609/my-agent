# PHASE 01 HANDOFF — Capability Truth Registry

## PHASE

01 — Capability Truth Registry

## BASE_COMMIT

0f4f99aa718f3c7f497352ab6e1a65e973f64674 (HEAD -> feat/houhou-01-capability-truth, origin/main, main)
Merge pull request #1 from giahao1609/feat/houhou-00-baseline-freeze

## FINAL_COMMIT

fb8692b (HEAD -> feat/houhou-01-capability-truth)
feat(capabilities): add truthful runtime capability registry

---

## OBJECTIVE

Create one truthful, machine-readable capability system so MyAgent can distinguish:
- capabilities that really work (`AVAILABLE`),
- capabilities that are degraded (`DEGRADED`),
- capabilities that are simulated/mocked (`MOCKED`),
- capabilities that are unavailable (`UNAVAILABLE`),
- capabilities whose status is unknown (`UNKNOWN`).

Enforce that class existence does not equate to capability availability, and provide the truthful foundation for Houhou's future Self Model.

---

## FILES_CHANGED

### New files created:
- `core/capabilities/__init__.py`: Package exports for `CapabilityRegistry`, `CapabilityStatus`, `CapabilityRecord`, `Availability`, and `default_capability_registry`.
- `core/capabilities/models.py`: Model exports ensuring single canonical capability type.
- `core/capabilities/registry.py`: `CapabilityRegistry` service implementation, `CapabilityProvider` protocol, and candidate selection logic.
- `core/capabilities/builtin.py`: Built-in registration of 15 baseline capabilities with evidence and probes.
- `tests/capabilities/test_capability_truth.py`: 10 behavioral verification tests covering truth invariants.
- `docs/cognitive_architecture/contracts/capabilities.md`: Architecture contract governing capability vocabulary, invariants, and registry API.
- `docs/handoffs/PHASE_01_HANDOFF.md`: This handoff document.

### Existing files modified (within allowed scope):
- `core/status.py`: Extended `Availability` with `AVAILABLE`, `MOCKED`, `UNKNOWN`; extended `CapabilityStatus` with evidence, implementation, verification, and metadata fields; aliased `CapabilityRecord = CapabilityStatus`.
- `core/errors.py`: Added `CapabilityMockedError` subclass of `CapabilityUnavailableError`.
- `integrations/container_sandbox_backend.py`: Updated `capabilities()` to truthfully declare `Availability.MOCKED` with reason and evidence.
- `integrations/playwright_browser.py`: Updated `capabilities()` to return `CapabilityStatus` objects with `Availability.MOCKED` and evidence.
- `integrations/local_sandbox_backend.py`: Updated `capabilities()` with explicit reason when disabled, implementation, and evidence fields.
- `core/whole_plan_coordinator.py`: Added `capabilities()` method reflecting `MOCKED` when default `_NoOpStepExecutor` is active.
- `my_agent_mcp/server.py`: Added `list_capabilities` MCP tool and capability summary in `my_agent_status`.

---

## PUBLIC_INTERFACES_CREATED

1. `core.capabilities.CapabilityRegistry`:
   - `register(record: CapabilityRecord) -> None`
   - `register_probe(name: str, probe: Callable[[], CapabilityRecord]) -> None`
   - `register_provider(provider: CapabilityProvider) -> None`
   - `get(name: str) -> CapabilityRecord`
   - `is_available(name: str) -> bool`
   - `require(name: str, *, allow_mock: bool = False) -> CapabilityRecord`
   - `list() -> list[CapabilityRecord]`
   - `refresh_providers() -> list[CapabilityRecord]`
   - `select_provider(capability_name: str, candidate_providers: Sequence[Any], *, require_real: bool = True) -> Any`
2. `core.capabilities.CapabilityProvider` (runtime-checkable protocol).
3. `core.errors.CapabilityMockedError` (subclass of `CapabilityUnavailableError`).
4. MCP tool: `list_capabilities(filter_status: str | None = None) -> list[dict[str, object]]`.

---

## PUBLIC_INTERFACES_MODIFIED

1. `core.status.Availability`:
   - Added values: `AVAILABLE = 'available'`, `MOCKED = 'mocked'`, `UNKNOWN = 'unknown'`.
   - Kept backward-compatible: `READY = 'ready'` (aliased to available semantics), `DEGRADED = 'degraded'`, `UNAVAILABLE = 'unavailable'`.
2. `core.status.CapabilityStatus`:
   - Added fields with defaults: `implementation`, `provider_or_backend`, `last_verified_at`, `verification_method`, `evidence`, `failure_reason`, `metadata`.
   - Added properties: `.status`, `.is_mocked`, `.is_degraded`, `.is_unavailable`, `.is_unknown`.
   - Updated `.available`: returns `True` if and only if `state in (Availability.READY, Availability.AVAILABLE)`.
   - Added `__getitem__` mapping protocol for legacy index access (`cap["status"]`, `cap["name"]`).
   - Added `.to_dict()` serialization.
3. `core.status.CapabilityRecord`: Defined as canonical alias `CapabilityRecord = CapabilityStatus`.

---

## EXISTING_CAPABILITY_TYPES_FOUND

- `core.status.CapabilityStatus`: Dataclass with `name`, `state: Availability`, `reason: str | None`.
- `core.status.Availability`: StrEnum with `READY`, `DEGRADED`, `UNAVAILABLE`.
- `core.protocols.AgentRuntime.capabilities()`: Protocol returning `Sequence[CapabilityStatus]`.
- `integrations.playwright_browser.PlaywrightBrowserAdapter.capabilities()`: Previously returned `Sequence[dict[str, Any]]`.

---

## CANONICAL_CAPABILITY_TYPE

- Existing `CapabilityStatus` was **reused and extended backward-compatibly**.
- `CapabilityRecord` was created as a direct canonical alias: `CapabilityRecord = CapabilityStatus`.
- No duplicate capability abstraction remains.
- No adapter was necessary because all new fields have defaults and existing callers continue to operate identically.

---

## CAPABILITIES_REGISTERED

15 baseline capabilities registered in `default_capability_registry`:
1. `workspace_file_read`
2. `workspace_file_write`
3. `command_execution`
4. `local_sandbox_execution`
5. `container_isolation`
6. `browser_navigation`
7. `browser_screenshot`
8. `whole_plan_autonomous_step_execution`
9. `test_runner`
10. `code_graph`
11. `local_docs_search`
12. `persistent_task_store`
13. `persistent_plan_store`
14. `persistent_checkpoint_store`
15. `persistent_memory_store`

---

## STATUS_OF_EACH_CAPABILITY

| Capability Name | Status | Implementation | Provider / Backend | Verification Method | Available? |
|---|---|---|---|---|---|
| `workspace_file_read` | `AVAILABLE` | `core.tools.read_file` | `local_filesystem` | `runtime_probe` | Yes |
| `workspace_file_write` | `AVAILABLE` | `core.tools.write_file` | `local_filesystem` | `runtime_probe` | Yes |
| `command_execution` | `AVAILABLE` | `LocalSandboxBackend` | `local_subprocess` | `runtime_probe` | Yes |
| `local_sandbox_execution` | `AVAILABLE` | `LocalSandboxBackend` | `local_subprocess` | `runtime_probe` | Yes |
| `container_isolation` | `MOCKED` | `ContainerSandboxBackend` | `in_memory_simulation` | `source_inspection` | **No** |
| `browser_navigation` | `MOCKED` | `PlaywrightBrowserAdapter` | `synthetic_browser` | `source_inspection` | **No** |
| `browser_screenshot` | `MOCKED` | `PlaywrightBrowserAdapter` | `synthetic_browser` | `source_inspection` | **No** |
| `whole_plan_autonomous_step_execution` | `MOCKED` | `_NoOpStepExecutor` | `in_memory_mock` | `runtime_probe` | **No** |
| `test_runner` | `AVAILABLE` | `TestRunnerRegistry` | `pytest_npm_go_runners` | `runtime_probe` | Yes |
| `code_graph` | `AVAILABLE` | `SQLiteCodeGraphStore` | `sqlite_database` | `database_persistence_probe` | Yes |
| `local_docs_search` | `AVAILABLE` | `DocsKnowledgeBackend` | `docs_indexer` | `runtime_probe` | Yes |
| `persistent_task_store` | `AVAILABLE` | `SQLiteTaskStore` | `sqlite_database` | `database_persistence_probe` | Yes |
| `persistent_plan_store` | `AVAILABLE` | `SQLitePlanStore` | `sqlite_database` | `database_persistence_probe` | Yes |
| `persistent_checkpoint_store` | `AVAILABLE` | `SQLiteCheckpointStore` | `sqlite_database` | `database_persistence_probe` | Yes |
| `persistent_memory_store` | `AVAILABLE` | `SQLiteMemoryStore` | `sqlite_database` | `database_persistence_probe` | Yes |

---

## MOCKS_CONFIRMED

1. **`PlaywrightBrowserAdapter` / `PlaywrightBrowserSession`**:
   - Status: `MOCKED`
   - Evidence: No Playwright imported; navigation returns canned synthetic string; screenshots write static 20-byte PNG header (`b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"`).
2. **`ContainerSandboxBackend`**:
   - Status: `MOCKED`
   - Evidence: No Docker or Podman engine attached; execution returns simulated string `"Simulated exec output for: ..."`; state kept in Python dicts.
3. **`_NoOpStepExecutor` in `WholePlanCoordinator`**:
   - Status: `MOCKED`
   - Evidence: Fallback step executor generates simulated `ImplementationResult` (`"[NoOpStepExecutor] Simulated execution of step ..."`) without agent execution.

---

## REAL_CAPABILITIES_CONFIRMED

1. **`LocalSandboxBackend`**: Real subprocess execution via `asyncio.create_subprocess_exec` constrained to workspace directory.
2. **`SQLiteTaskStore`, `SQLitePlanStore`, `SQLiteCheckpointStore`, `SQLiteMemoryStore`, `SQLiteCodeGraphStore`**: Real persistent database operations backed by SQLite.
3. **`TestRunnerRegistry`**: Real pytest/npm/go process execution.
4. **`DocsKnowledgeBackend`**: Real markdown indexing and text retrieval.
5. **`VisualDiffValidator`**: Real binary PNG parsing and zlib decompression.

---

## RUNTIME_SELECTION_CHANGES

- `CapabilityRegistry.require(name, allow_mock=False)` raises `CapabilityMockedError` if a caller demands an operational capability that is simulated.
- `CapabilityRegistry.select_provider(..., require_real=True)` filters out candidate providers whose capabilities are mocked or unverified.
- `CapabilityStatus.available` returns `False` for `MOCKED` (previously `state is not UNAVAILABLE` allowed mocks to evaluate as available).

---

## BEHAVIOR_VERIFIED

- Calling `PlaywrightBrowserAdapter.capabilities()` now returns `MOCKED` status; `cap.available` is `False`.
- Calling `ContainerSandboxBackend.capabilities()` now returns `MOCKED` status; `cap.available` is `False`.
- Calling `LocalSandboxBackend.capabilities()` returns `READY`; `cap.available` is `True`.
- Querying unknown capability returns `UNKNOWN` status (`available == False`).
- Evidence and failure reasons are inspectable on all capability records.

---

## TESTS_ADDED

File: `tests/capabilities/test_capability_truth.py` (10 behavioral tests):
- `test_browser_mock_truth`: Mock browser implementation cannot report AVAILABLE.
- `test_container_truth`: ContainerSandboxBackend cannot report real AVAILABLE container isolation.
- `test_backend_distinction`: LocalSandboxBackend is distinguishable from container isolation.
- `test_whole_plan_truth`: _NoOpStepExecutor cannot cause real autonomous WholePlan capability to report AVAILABLE.
- `test_real_capability_verified`: Verified real providers report AVAILABLE with genuine evidence.
- `test_unknown_behavior`: Unregistered capabilities return UNKNOWN rather than optimistic status.
- `test_evidence_inspection`: Registry exposes evidence supporting capability status and serialization.
- `test_capability_status_backward_compatibility`: Existing `AgentRuntime.capabilities()` users remain compatible without duplicate types.
- `test_real_provider_requirement`: Callers requiring real capabilities reject MOCKED providers.
- `test_general_capability_semantics`: Invariants hold across generic custom providers.

---

## TESTS_RUN

1. Targeted tests:
   ```bash
   .venv/bin/pytest tests/capabilities/test_capability_truth.py -v
   Result: 10 passed in 0.37s
   ```
2. Full repository test suite:
   ```bash
   .venv/bin/pytest tests/ --tb=no -q
   Result: 706 passed in 11.41s
   ```

---

## TEST_RESULTS

| Status | Pre-Flight (Baseline) | Post-Phase 01 |
|---|---|---|
| PASSED | 696 | 706 (+10 new tests) |
| FAILED | 0 | 0 |
| ERRORS | 0 | 0 |

---

## COMPATIBILITY_WITH_AGENT_RUNTIME

- `AgentRuntime.capabilities() -> Sequence[CapabilityStatus]` remains 100% compatible.
- All existing tests using `CapabilityStatus` continue to pass without any test rewrite.
- No secondary or duplicate capability abstraction was created.

---

## KNOWN_LIMITATIONS

1. Dynamic live probing for remote/network capabilities is limited to local environment inspection in Phase 01.
2. WholePlanCoordinator default executor remains `_NoOpStepExecutor` until a real StepExecutor is wired in a future phase.
3. Container sandbox execution remains simulated until a real Docker/Podman driver is implemented.

---

## BLOCKED_DEPENDENCIES

None. Phase 01 was completed strictly within its allowed boundaries without modifying forbidden subsystems.

---

## FORBIDDEN_FUTURE_ASSUMPTIONS

1. Do NOT assume `browser_navigation` or `browser_screenshot` actually opens a browser or captures web pages until real Playwright/browser automation is implemented.
2. Do NOT assume `container_isolation` isolates processes in Linux containers; it is an in-memory simulation. Use `local_sandbox_execution` for real process execution.
3. Do NOT assume WholePlan autonomous execution is wired to real AI coding agents by default.
4. Do NOT check class names or README text to determine capability availability; use `CapabilityRegistry.get(name).available` or `CapabilityRegistry.require(name)`.

---

## NEXT_PHASE_EXPECTATIONS

Phase 02 can now safely rely on `CapabilityRegistry` to query Houhou's actual runtime capabilities, ensuring future cognitive Self Model and decision layers make plans grounded in executable truth rather than synthetic claims.
