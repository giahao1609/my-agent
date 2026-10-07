"""Project-authoritative selection of small, sanitized context bundles."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from core.agent_work_result import TestExecutionResult
from core.code_graph import CodeGraphBackend
from core.context import ExecutionContext
from core.context_disclosure import (
    BridgeError, BridgeErrorCode, ContextItem, DisclosureEnvelope, DisclosureLevel,
    DisclosureManifest, DisclosurePolicy, DisclosedSource, SensitiveDataGate, serialized,
)
from core.durable_memory_service import DurableMemoryService
from core.project_store import ProjectStore
from core.step_execution import StepExecutionEvidence
from core.tool_executor import ToolExecutor
from tools.workspace_tools import _workspace_path


@dataclass(frozen=True, slots=True)
class ProjectScope:
    project_id: str = field(repr=False)
    workspace_path: str = field(repr=False)
    external_id: str


class ProjectScopeValidator:
    def __init__(self, projects: ProjectStore) -> None:
        self._projects = projects
        self._aliases: dict[tuple[str, str], str] = {}

    async def resolve(self, project_id: str | None = None) -> ProjectScope:
        try:
            active = await self._projects.get_active()
            if active is None:
                raise BridgeError(BridgeErrorCode.PROJECT_CONTEXT_REQUIRED)
            if project_id is not None and project_id != active.project_id:
                raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
            registered = await self._projects.get(active.project_id)
            if registered is None or registered.workspace_path != active.workspace_path:
                raise BridgeError(BridgeErrorCode.PROJECT_CONTEXT_REQUIRED)
            root = Path(registered.workspace_path).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise BridgeError(BridgeErrorCode.PROJECT_CONTEXT_REQUIRED)
            key = (registered.project_id, str(root))
            alias = self._aliases.setdefault(key, "proj_" + uuid4().hex)
            return ProjectScope(registered.project_id, str(root), alias)
        except BridgeError:
            raise
        except Exception:
            raise BridgeError(BridgeErrorCode.PROJECT_CONTEXT_REQUIRED) from None

    async def revalidate(self, scope: ProjectScope) -> None:
        if await self.resolve(scope.project_id) != scope:
            raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)

    @staticmethod
    def source_path(scope: ProjectScope, relative_path: str) -> str:
        path = SensitiveDataGate.validate_path(relative_path)
        try:
            root = Path(scope.workspace_path)
            # Reject all symlink components and nested repositories before reading.
            candidate = root
            for part in Path(path).parts:
                candidate /= part
                if candidate.is_symlink():
                    raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
                if candidate.is_dir() and (candidate / ".git").exists():
                    raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
            _, resolved = _workspace_path(
                ExecutionContext(workspace_id=scope.workspace_path), path, must_exist=True,
            )
            if not resolved.is_file():
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
            return SensitiveDataGate.validate_path(resolved.relative_to(root).as_posix())
        except BridgeError:
            raise
        except Exception:
            raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED) from None


@dataclass(frozen=True, slots=True)
class SourceRange:
    relative_path: str
    start_line: int
    end_line: int
    relevance: str
    priority: int = 0


@dataclass(frozen=True, slots=True)
class GraphSelection:
    node_id: str
    relevance: str
    depth: int = 0
    priority: int = 0


@dataclass(frozen=True, slots=True)
class SelectedEvidence:
    """Server-selected task evidence, never a provider-controlled lookup."""

    project_id: str
    task_id: str
    relevance: str
    relative_path: str | None = None
    git_diff: str | None = field(default=None, repr=False)
    test_result: TestExecutionResult | None = field(default=None, repr=False)
    execution: StepExecutionEvidence | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ContextRequest:
    task_id: str
    project_id: str | None = None
    disclosure_level: DisclosureLevel = DisclosureLevel.SELECTED_EVIDENCE
    source_ranges: tuple[SourceRange, ...] = ()
    memory_query: str | None = None
    graph_selections: tuple[GraphSelection, ...] = ()
    evidence: tuple[SelectedEvidence, ...] = ()


class ContextBroker:
    def __init__(
        self, *, scope_validator: ProjectScopeValidator,
        memory: DurableMemoryService, graph: CodeGraphBackend,
        workspace_tools: ToolExecutor, policy: DisclosurePolicy = DisclosurePolicy(),
    ) -> None:
        self.scope_validator = scope_validator
        self.policy = policy
        self._memory = memory
        self._graph = graph
        self._tools = workspace_tools
        self.gate = SensitiveDataGate()

    async def build(
        self, request: ContextRequest, *, request_id: str, external_task_id: str,
    ) -> DisclosureEnvelope:
        try:
            return await self._build(request, request_id, external_task_id)
        except BridgeError:
            raise
        except Exception:
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED) from None

    async def _build(self, request, request_id, external_task_id):
        policy, budget = self.policy, self.policy.budget
        policy.validate_level(request.disclosure_level)
        scope = await self.scope_validator.resolve(request.project_id)
        if not request.task_id:
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        for identifier in (request_id, external_task_id):
            # Caller supplies generated IDs, not arbitrary source values.
            if not isinstance(identifier, str) or not identifier.isascii() or not (
                identifier.replace("_", "").isalnum()
            ) or len(identifier) > 64:
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        selections = (request.source_ranges, request.graph_selections, request.evidence)
        if sum(map(len, selections)) > budget.max_selection_items:
            raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
        has_evidence = any(selections) or request.memory_query is not None
        if has_evidence and request.disclosure_level < DisclosureLevel.SELECTED_EVIDENCE:
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        items: list[ContextItem] = []
        redactions = omissions = 0

        def add(category, source_id, content, start=None, end=None, cap=None):
            nonlocal redactions, omissions
            safe, count = self.gate.sanitize(content, max_input_chars=budget.max_input_chars)
            redactions += count
            if cap is not None and len(safe) > cap:
                safe = safe[:cap]
                omissions += 1
            if safe:
                items.append(ContextItem(category, source_id, safe, start, end))

        # Explicit relevance and stable priority are supplied by trusted task selection.
        ranges = [r for r in request.source_ranges if r.relevance.strip()]
        omissions += len(request.source_ranges) - len(ranges)
        ranges.sort(key=lambda r: (-r.priority, r.relative_path, r.start_line, r.end_line))
        omissions += max(0, len(ranges) - budget.max_code_snippets)
        for selected in ranges[:budget.max_code_snippets]:
            if (type(selected.start_line) is not int or type(selected.end_line) is not int
                    or selected.start_line < 1 or selected.end_line < selected.start_line):
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
            if budget.max_lines_per_snippet == 0:
                omissions += 1
                continue
            path = self.scope_validator.source_path(scope, selected.relative_path)
            safe_path, _ = self.gate.sanitize(path, max_input_chars=budget.max_input_chars)
            if safe_path != path:
                raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
            end = min(selected.end_line, selected.start_line + budget.max_lines_per_snippet - 1)
            omissions += int(end < selected.end_line)
            await self.scope_validator.revalidate(scope)
            result = await self._tools.execute("read_file", {
                "path": path, "start_line": selected.start_line, "end_line": end,
                "max_chars": budget.max_input_chars,
            }, ExecutionContext(workspace_id=scope.workspace_path, project_id=scope.project_id))
            if result.get("status") != "ok" or result.get("path") != path:
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
            add("code", path, result["content"], selected.start_line, result["end_line"])

        if request.memory_query is not None:
            if not request.memory_query.strip():
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
            self.gate.sanitize(request.memory_query, max_input_chars=budget.max_input_chars)
            if budget.max_memory_matches:
                await self.scope_validator.revalidate(scope)
                matches = await self._memory.retrieve(
                    scope.project_id, request.memory_query,
                    limit=budget.max_memory_matches, include_global=False,
                )
                for i, match in enumerate(matches[:budget.max_memory_matches]):
                    if match.project_id != scope.project_id:
                        raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
                    add("memory", f"memory:{i + 1}", match.content)

        graph_count = 0
        graph_selections = sorted(request.graph_selections, key=lambda g: (-g.priority, g.node_id))
        seen_nodes: set[str] = set()
        for selection in graph_selections:
            if not selection.relevance.strip() or graph_count >= budget.max_graph_neighbors:
                omissions += 1
                continue
            if type(selection.depth) is not int or selection.depth < 0:
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
            await self.scope_validator.revalidate(scope)
            root_node = await self._graph.get_node(scope.project_id, selection.node_id)
            if root_node is None:
                raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
            if root_node.project_id != scope.project_id:
                raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
            nodes = [root_node]
            depth = min(selection.depth, budget.max_graph_depth)
            omissions += int(depth < selection.depth)
            remaining = budget.max_graph_neighbors - graph_count - 1
            if depth and remaining:
                nodes.extend(await self._graph.semantic_dependencies(
                    scope.project_id, root_node.node_id, depth=depth, limit=remaining,
                ))
            for node in nodes[:budget.max_graph_neighbors - graph_count]:
                if node.project_id != scope.project_id:
                    raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
                if node.node_id in seen_nodes:
                    continue
                seen_nodes.add(node.node_id)
                if not node.path:
                    omissions += 1
                    continue
                path = self.scope_validator.source_path(scope, node.path)
                safe_path, _ = self.gate.sanitize(path, max_input_chars=budget.max_input_chars)
                if safe_path != path:
                    raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
                add("graph", path, node.name, node.line_start, node.line_end)
                graph_count += 1

        remaining_git, remaining_test = budget.max_git_diff_chars, budget.max_test_output_chars
        for evidence in request.evidence:
            if evidence.project_id != scope.project_id or evidence.task_id != request.task_id:
                raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
            if not evidence.relevance.strip():
                omissions += 1
                continue
            if sum(x is not None for x in (
                evidence.git_diff, evidence.test_result, evidence.execution,
            )) != 1:
                raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
            if evidence.git_diff is not None:
                if evidence.relative_path is None:
                    raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
                path = self.gate.validate_path(evidence.relative_path)
                safe_path, _ = self.gate.sanitize(path, max_input_chars=budget.max_input_chars)
                if safe_path != path:
                    raise BridgeError(BridgeErrorCode.SENSITIVE_CONTEXT_DENIED)
                # Only a selected hunk, not a multi-file patch/history bundle.
                if any(line.startswith(("diff --git ", "+++ ", "--- ", "commit "))
                       for line in evidence.git_diff.splitlines()):
                    raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
                add("git", path, evidence.git_diff, cap=remaining_git)
                remaining_git = max(0, remaining_git - len(items[-1].content)) if items else 0
            elif evidence.test_result is not None:
                test = evidence.test_result
                text = serialized({"status": test.status, "passed": test.passed,
                                   "failed": test.failed, "summary": test.summary,
                                   "failures": test.failure_details})
                add("test", "test:selected", text, cap=remaining_test)
                remaining_test = max(0, remaining_test - len(items[-1].content)) if items else 0
            else:
                execution = evidence.execution
                # Exclude raw tool arguments, paths, notes, and command transcripts.
                add("execution", "execution:selected", serialized({
                    "is_real": execution.is_real,
                    "execution_success": execution.execution_success,
                    "verification_performed": execution.verification_performed,
                    "verification_passed": execution.verification_passed,
                }))

        await self.scope_validator.revalidate(scope)
        # Drop lowest-ranked trailing items; no fallback retrieval or wider dump.
        while True:
            manifest = DisclosureManifest(
                request_id, external_task_id, scope.external_id, request.disclosure_level,
                tuple(sorted({i.category for i in items})), len(items),
                tuple(DisclosedSource(i.category, i.source_id, i.start_line, i.end_line)
                      for i in items),
                sum(i.category == "memory" for i in items), redactions, omissions, 0,
            )
            envelope = DisclosureEnvelope(tuple(items), manifest)
            # Fixed point accounts for the digits of the size field itself.
            while True:
                size = len(serialized(envelope.to_dict()))
                if size == envelope.manifest.budget_usage_chars:
                    break
                envelope = replace(envelope, manifest=replace(envelope.manifest, budget_usage_chars=size))
            if size <= budget.max_total_context_chars:
                return envelope
            if not items:
                raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
            items.pop()
            omissions += 1
