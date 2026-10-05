# Architecture Contract: Capability Truth Registry

## 1. Canonical Capability Status Vocabulary

The Houhou Cognitive Architecture defines exactly one canonical vocabulary for capability status (`core.status.Availability`):

```
+-----------------------------------------------------------------------------------+
| Status Enum   | Semantics                                                         |
+---------------+-------------------------------------------------------------------+
| AVAILABLE     | A real implementation exists and runtime evidence verifies that   |
|               | the capability can currently execute its intended behavior.       |
|               | (READY is maintained as a backward-compatible alias).             |
+---------------+-------------------------------------------------------------------+
| DEGRADED      | A real implementation exists, but some required functionality is  |
|               | impaired, restricted, or operating with degraded latency/perf.    |
+---------------+-------------------------------------------------------------------+
| MOCKED        | The implementation simulates, stubs, or fabricates behavior.      |
|               | MUST NEVER be treated as a real production capability.            |
+---------------+-------------------------------------------------------------------+
| UNAVAILABLE   | The capability is implemented or known, but cannot currently be   |
|               | executed (e.g. disabled flag, unconfigured credentials, offline). |
+---------------+-------------------------------------------------------------------+
| UNKNOWN       | There is insufficient evidence to determine operational state.    |
|               | Defaults to safe non-availability.                                |
+---------------+-------------------------------------------------------------------+
```

### Invariants:
- `MOCKED != AVAILABLE`: A simulated capability evaluates `available == False`.
- `UNKNOWN != AVAILABLE`: Lack of evidence returns non-available status.
- `MOCKED != DEGRADED`: Impaired real code is degraded; synthetic simulation is mocked.
- `class_exists != capability_available`: The presence of a class in the repository is not proof of runtime availability.

---

## 2. Canonical Capability Record Contract

`CapabilityRecord` is an alias to `CapabilityStatus` (`core/status.py`), ensuring that no secondary or overlapping model is introduced:

```python
@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    name: str
    state: Availability
    reason: str | None = None
    implementation: str = ""
    provider_or_backend: str = ""
    last_verified_at: str | None = None
    verification_method: str = ""
    evidence: str = ""
    failure_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Properties and Methods:
- `.available -> bool`: `True` if and only if `state in (Availability.AVAILABLE, Availability.READY)`.
- `.is_mocked -> bool`: `True` if `state == Availability.MOCKED`.
- `.is_degraded -> bool`: `True` if `state == Availability.DEGRADED`.
- `.is_unavailable -> bool`: `True` if `state == Availability.UNAVAILABLE`.
- `.is_unknown -> bool`: `True` if `state == Availability.UNKNOWN`.
- `.__getitem__(key: str) -> Any`: Dict-like indexing for compatibility (`c["status"]`, `c["name"]`).
- `.to_dict() -> dict[str, Any]`: Machine-readable serialization for logs, MCP tools, and audits.

---

## 3. CapabilityRegistry API

The central service is `CapabilityRegistry` (`core.capabilities.CapabilityRegistry`):

```python
class CapabilityRegistry:
    def register(self, record: CapabilityRecord) -> None: ...
    def register_probe(self, name: str, probe: Callable[[], CapabilityRecord]) -> None: ...
    def register_provider(self, provider: CapabilityProvider) -> None: ...
    def get(self, name: str) -> CapabilityRecord: ...
    def is_available(self, name: str) -> bool: ...
    def require(self, name: str, *, allow_mock: bool = False) -> CapabilityRecord: ...
    def list(self) -> list[CapabilityRecord]: ...
    async def refresh_providers(self) -> list[CapabilityRecord]: ...
    async def select_provider(
        self,
        capability_name: str,
        candidate_providers: Sequence[Any],
        *,
        require_real: bool = True,
    ) -> Any: ...
```

---

## 4. Provider and Backend Semantics

Providers expose their capabilities through the `CapabilityProvider` protocol:

```python
class CapabilityProvider(Protocol):
    async def capabilities(self) -> Sequence[CapabilityStatus | Mapping[str, Any]]: ...
```

### Registered Baseline Capabilities:
1. `workspace_file_read`: `AVAILABLE` (`core.tools.read_file`)
2. `workspace_file_write`: `AVAILABLE` (`core.tools.write_file`)
3. `command_execution`: `AVAILABLE` (`LocalSandboxBackend` via `asyncio.create_subprocess_exec`)
4. `local_sandbox_execution`: `AVAILABLE` (`LocalSandboxBackend`)
5. `container_isolation`: `MOCKED` (`ContainerSandboxBackend` returns simulated string)
6. `browser_navigation`: `MOCKED` (`PlaywrightBrowserAdapter` returns canned string)
7. `browser_screenshot`: `MOCKED` (`PlaywrightBrowserAdapter` writes static 20-byte PNG header)
8. `whole_plan_autonomous_step_execution`: `MOCKED` (`_NoOpStepExecutor` active by default in `WholePlanCoordinator`)
9. `test_runner`: `AVAILABLE` (`TestRunnerRegistry` executes pytest/npm/go)
10. `code_graph`: `AVAILABLE` (`SQLiteCodeGraphStore` with AST parser)
11. `local_docs_search`: `AVAILABLE` (`DocsKnowledgeBackend` markdown keyword search)
12. `persistent_task_store`: `AVAILABLE` (`SQLiteTaskStore` SQLite database)
13. `persistent_plan_store`: `AVAILABLE` (`SQLitePlanStore` SQLite database)
14. `persistent_checkpoint_store`: `AVAILABLE` (`SQLiteCheckpointStore` SQLite database)
15. `persistent_memory_store`: `AVAILABLE` (`SQLiteMemoryStore` SQLite database)

---

## 5. Evidence Requirements

Every capability claim MUST be accompanied by executable evidence:
- **`verification_method`**: Identifies how the capability was verified:
  - `runtime_probe`: Executed an operational verification check in the environment.
  - `database_persistence_probe`: Verified schema and query capability against SQLite.
  - `source_inspection`: Analyzed code AST and imports to verify absence of real engine/daemon.
  - `executor_type_check`: Inspected injected component types at runtime.
- **`evidence`**: Human- and machine-readable text stating the concrete proof (e.g. `Real asyncio.create_subprocess_exec execution path`).
- **`last_verified_at`**: ISO 8601 UTC timestamp of the verification run.

---

## 6. Runtime Selection Rules

1. Callers requiring an operational capability MUST use `registry.require(capability_name)` or `registry.select_provider(..., require_real=True)`.
2. When `require_real=True`, any candidate whose capability status is `MOCKED`, `UNAVAILABLE`, or `UNKNOWN` is rejected with `CapabilityUnavailableError` or `CapabilityMockedError`.
3. An unknown capability query returns `UNKNOWN` status (`available == False`), never defaulting optimistically to available.

---

## 7. Compatibility with AgentRuntime.capabilities()

- `AgentRuntime.capabilities()` returns `Sequence[CapabilityStatus]`.
- Existing implementations returning `CapabilityStatus(name="...", state=Availability.READY)` continue to work without modification.
- Legacy callers inspecting `cap.state == Availability.READY` or `cap.available` remain 100% functional.
- Zero duplicate types: `CapabilityRecord` is a direct alias to `CapabilityStatus`.

---

## 8. Distinction Between Local Subprocess Execution and Container Isolation

- **Local Subprocess Execution (`local_sandbox_execution`)**:
  - Implementation: `integrations.local_sandbox_backend.LocalSandboxBackend`.
  - Status: `AVAILABLE` (when enabled).
  - Runtime Mechanism: Direct process spawn via `asyncio.create_subprocess_exec` scoped to the workspace directory.
  - Boundary: Process-level OS boundaries, without kernel namespace or network isolation.
- **Container Isolation (`container_isolation`)**:
  - Implementation: `integrations.container_sandbox_backend.ContainerSandboxBackend`.
  - Status: `MOCKED`.
  - Runtime Mechanism: In-memory simulation returning synthetic string responses.
  - Boundary: None. No container daemon, no cgroups, no network namespaces.

**Invariant:** Local subprocess execution MUST NEVER be classified as container isolation.

---

## 9. MOCKED Behavior Guarantees

1. A capability with `state=Availability.MOCKED` always evaluates `available == False`.
2. A MOCKED capability cannot satisfy an automated completion gate or release gate.
3. MOCKED capabilities must disclose their simulated nature in `reason` and `failure_reason`.

---

## 10. Rules Future Phases Must Follow

1. When adding a new capability or backend, register it in `CapabilityRegistry` with explicit `verification_method` and `evidence`.
2. Do NOT report `AVAILABLE` if the implementation is a stub, NoOp, or simulated mock. Use `MOCKED` or `UNAVAILABLE`.
3. If an implementation transitions from `MOCKED` to `AVAILABLE` (e.g., real Docker integration added in a future phase), update the status and supply the real verification probe.
