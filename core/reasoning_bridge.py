"""Provider-independent reasoning handoff; no provider client or execution."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from uuid import uuid4

from core.capabilities.registry import CapabilityRegistry
from core.context_broker import ContextBroker, ContextRequest, ProjectScope
from core.context_disclosure import (
    BridgeError, BridgeErrorCode, DisclosureEnvelope, DisclosureLevel, DisclosedSource,
    serialized,
)

logger = logging.getLogger(__name__)


class TaskCapabilityStatus(StrEnum):
    SELF_EXECUTABLE = "SELF_EXECUTABLE"
    NEEDS_REASONING = "NEEDS_REASONING"
    NEEDS_CODEX = "NEEDS_CODEX"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class TaskRequirements:
    """Trusted server facts. Never construct these from repository/provider text."""

    task_id: str
    project_id: str | None
    objective: str = field(repr=False)
    required_capabilities: tuple[str, ...] = ()
    reasoning_required: bool = False
    code_synthesis_required: bool = False
    approval_required: bool = False
    execution_denied: bool = False


@dataclass(frozen=True, slots=True)
class TaskCapabilityAssessment:
    task_id: str
    status: TaskCapabilityStatus
    objective_reason: str
    required_next_action: str
    required_capabilities: tuple[str, ...]
    available_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    project_id: str | None


class ExecutionRoute(StrEnum):
    NATIVE = "existing:ToolExecutor"
    REASONING_HANDOFF = "await:ReasoningDecision"
    CODEX = "existing:CoderRuntimeStepExecutor"
    APPROVAL = "existing:ToolPolicy"
    STOP = "stop:EXECUTION_BLOCKED"


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    request_id: str
    task_id: str
    project_id: str
    objective: str = field(repr=False)
    problem_summary: str = field(repr=False)
    context: DisclosureEnvelope = field(repr=False)
    constraints: tuple[str, ...]
    unknowns: tuple[str, ...]
    attempted_actions: tuple[str, ...]
    available_capabilities: tuple[str, ...]

    def to_dict(self) -> dict:
        result = asdict(self)
        context = result.pop("context")
        result["selected_context"] = context["selected_context"]
        result["disclosure_manifest"] = context["manifest"]
        return result


@dataclass(frozen=True, slots=True)
class ReasoningDecision:
    request_id: str
    diagnosis: str = field(repr=False)
    proposed_plan: tuple[str, ...] = field(repr=False)
    constraints: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    verification_plan: tuple[str, ...]
    recommended_execution_class: TaskCapabilityStatus

    @classmethod
    def parse(cls, payload: Mapping, *, max_chars: int) -> ReasoningDecision:
        expected = {
            "request_id", "diagnosis", "proposed_plan", "constraints",
            "acceptance_criteria", "verification_plan", "recommended_execution_class",
        }
        try:
            if not isinstance(payload, Mapping) or set(payload) != expected:
                raise ValueError
            if len(serialized(dict(payload))) > max_chars:
                raise ValueError
            if any(not isinstance(payload[k], str) or not payload[k].strip()
                   for k in ("request_id", "diagnosis", "recommended_execution_class")):
                raise ValueError
            sequences = {}
            for key in ("proposed_plan", "constraints", "acceptance_criteria", "verification_plan"):
                value = payload[key]
                if not isinstance(value, (list, tuple)) or any(
                    not isinstance(v, str) or not v.strip() for v in value
                ):
                    raise ValueError
                sequences[key] = tuple(value)
            if not sequences["proposed_plan"] or not sequences["verification_plan"]:
                raise ValueError
            status = TaskCapabilityStatus(payload["recommended_execution_class"])
            if status is TaskCapabilityStatus.NEEDS_REASONING:
                raise ValueError
            return cls(payload["request_id"], payload["diagnosis"],
                       recommended_execution_class=status, **sequences)
        except Exception:
            raise BridgeError(BridgeErrorCode.EXTERNAL_REASONING_INVALID) from None


@dataclass(frozen=True, slots=True)
class ReassessedDecision:
    request_id: str
    assessment: TaskCapabilityAssessment
    route: ExecutionRoute
    trust: str = field(default="UNVERIFIED_EXTERNAL_REASONING", init=False)


@dataclass(frozen=True, slots=True)
class RoutingResult:
    assessment: TaskCapabilityAssessment
    route: ExecutionRoute
    reasoning_request: ReasoningRequest | None = None


class ReasoningBridgeService:
    """Server-side authority: proposals cannot mutate scope, evidence, or approvals.

    Routes are descriptive references to existing integrations, not executable
    dispatch tokens. Callers must still use ToolPolicy and VerificationGate.
    Pending correlations are process-local in this foundation phase.
    """

    def __init__(self, *, broker: ContextBroker, capabilities: CapabilityRegistry) -> None:
        self._broker = broker
        self._capabilities = capabilities
        self._pending: dict[str, tuple[TaskRequirements, ProjectScope]] = {}

    async def assess(self, task: TaskRequirements) -> TaskCapabilityAssessment:
        try:
            scope = await self._broker.scope_validator.resolve(task.project_id)
        except BridgeError:
            return TaskCapabilityAssessment(
                task.task_id, TaskCapabilityStatus.BLOCKED,
                "Required authoritative project context is unavailable.",
                "Establish authorized current project context.",
                task.required_capabilities, (), task.required_capabilities, None,
            )
        required = tuple(dict.fromkeys(task.required_capabilities))
        available = tuple(name for name in required if self._capabilities.is_available(name))
        missing = tuple(name for name in required if name not in available)
        if task.execution_denied:
            status, reason, action = (
                TaskCapabilityStatus.BLOCKED,
                "Server execution policy denies the requested operation.", "Stop execution.",
            )
        elif task.approval_required:
            status, reason, action = (
                TaskCapabilityStatus.NEEDS_APPROVAL,
                "Requested operation requires explicit approval.", "Use existing approval path.",
            )
        elif missing:
            status, reason, action = (
                TaskCapabilityStatus.BLOCKED,
                "Required capabilities are not real and available.", "Restore required capabilities.",
            )
        elif task.reasoning_required:
            status, reason, action = (
                TaskCapabilityStatus.NEEDS_REASONING,
                "Architecture alternatives remain unresolved without a deterministic selection rule.",
                "Build bounded reasoning request and await a proposal.",
            )
        elif task.code_synthesis_required:
            status, reason, action = (
                TaskCapabilityStatus.NEEDS_CODEX,
                "Required code synthesis exceeds the native executor's supported operations.",
                "Use existing coder integration with its capability and approval gates.",
            )
        else:
            status, reason, action = (
                TaskCapabilityStatus.SELF_EXECUTABLE,
                "Required native capabilities are real and available.",
                "Use existing native tool execution with its policy checks.",
            )
        return TaskCapabilityAssessment(task.task_id, status, reason, action, required,
                                        available, missing, scope.project_id)

    @staticmethod
    def _route(status: TaskCapabilityStatus) -> ExecutionRoute:
        return {
            TaskCapabilityStatus.SELF_EXECUTABLE: ExecutionRoute.NATIVE,
            TaskCapabilityStatus.NEEDS_REASONING: ExecutionRoute.REASONING_HANDOFF,
            TaskCapabilityStatus.NEEDS_CODEX: ExecutionRoute.CODEX,
            TaskCapabilityStatus.NEEDS_APPROVAL: ExecutionRoute.APPROVAL,
            TaskCapabilityStatus.BLOCKED: ExecutionRoute.STOP,
        }[status]

    async def route(
        self, task: TaskRequirements, *, context: ContextRequest | None = None,
        problem_summary: str = "", constraints: tuple[str, ...] = (),
        unknowns: tuple[str, ...] = (), attempted_actions: tuple[str, ...] = (),
    ) -> RoutingResult:
        assessment = await self.assess(task)
        request = None
        if assessment.status is TaskCapabilityStatus.NEEDS_REASONING:
            request = await self.build_request(
                task, context=context, problem_summary=problem_summary,
                constraints=constraints, unknowns=unknowns, attempted_actions=attempted_actions,
            )
        return RoutingResult(assessment, self._route(assessment.status), request)

    async def build_request(
        self, task: TaskRequirements, *, context: ContextRequest | None = None,
        problem_summary: str = "", constraints: tuple[str, ...] = (),
        unknowns: tuple[str, ...] = (), attempted_actions: tuple[str, ...] = (),
    ) -> ReasoningRequest:
        budget = self._broker.policy.budget
        scope = await self._broker.scope_validator.resolve(task.project_id)
        assessment = await self.assess(task)
        if assessment.status is not TaskCapabilityStatus.NEEDS_REASONING:
            raise BridgeError(BridgeErrorCode.EXECUTION_BLOCKED)
        if len(self._pending) >= budget.max_selection_items:
            raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
        context = context or ContextRequest(task.task_id, scope.project_id)
        if context.task_id != task.task_id or context.project_id not in (None, scope.project_id):
            raise BridgeError(BridgeErrorCode.PROJECT_SCOPE_DENIED)
        if context.disclosure_level > DisclosureLevel.SELECTED_EVIDENCE:
            raise BridgeError(BridgeErrorCode.DISCLOSURE_DENIED)
        redactions = 0

        def clean(value):
            nonlocal redactions
            safe, count = self._broker.gate.sanitize(value, max_input_chars=budget.max_input_chars)
            redactions += count
            return safe

        def clean_sequence(values):
            if not isinstance(values, tuple) or len(values) > budget.max_selection_items:
                raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
            return tuple(clean(value) for value in values)

        request_id, task_alias = "req_" + uuid4().hex, "task_" + uuid4().hex
        objective, summary = clean(task.objective), clean(problem_summary)
        cleaned_constraints = clean_sequence(constraints)
        cleaned_unknowns = clean_sequence(unknowns)
        cleaned_attempts = clean_sequence(attempted_actions)
        available = clean_sequence(assessment.available_capabilities)
        context = replace(context, project_id=scope.project_id)
        envelope = await self._broker.build(context, request_id=request_id, external_task_id=task_alias)
        envelope = replace(envelope, manifest=replace(
            envelope.manifest, redaction_count=envelope.manifest.redaction_count + redactions,
        ))
        request = ReasoningRequest(
            request_id, task_alias, scope.external_id, objective, summary, envelope,
            cleaned_constraints, cleaned_unknowns, cleaned_attempts, available,
        )
        # Include headers, escaping and manifest in the final wire-size budget.
        while True:
            envelope = request.context
            while True:
                size = len(serialized(envelope.to_dict()))
                if size == envelope.manifest.budget_usage_chars:
                    break
                envelope = replace(envelope, manifest=replace(envelope.manifest, budget_usage_chars=size))
            request = replace(request, context=envelope)
            if len(serialized(request.to_dict())) <= budget.max_total_context_chars:
                break
            items = envelope.selected_context[:-1]
            if not envelope.selected_context:
                raise BridgeError(BridgeErrorCode.CONTEXT_BUDGET_EXCEEDED)
            manifest = replace(
                envelope.manifest, number_of_items=len(items),
                source_categories=tuple(sorted({i.category for i in items})),
                sources=tuple(DisclosedSource(i.category, i.source_id, i.start_line, i.end_line)
                              for i in items),
                memory_match_count=sum(i.category == "memory" for i in items),
                omission_count=envelope.manifest.omission_count + 1,
            )
            request = replace(request, context=DisclosureEnvelope(items, manifest))
        await self._broker.scope_validator.revalidate(scope)
        self._pending[request_id] = (replace(task, project_id=scope.project_id), scope)
        logger.info("reasoning_request request_id=%s items=%d omissions=%d",
                    request_id, request.context.manifest.number_of_items,
                    request.context.manifest.omission_count)
        return request

    _DESTRUCTIVE_ACTIONS = re.compile(
        r"(?i)\b(?:rm\s+-rf|git\s+reset\s+--hard|git\s+push\s+--force|git\s+clean|"
        r"drop\s+table|truncate\s+table|delete\s+from|delete_path|remove\s+file)\b"
    )

    async def reassess(self, payload: Mapping) -> ReassessedDecision:
        decision = ReasoningDecision.parse(payload, max_chars=self._broker.policy.budget.max_input_chars)
        pending = self._pending.get(decision.request_id)
        if pending is None:
            raise BridgeError(BridgeErrorCode.EXTERNAL_REASONING_INVALID)
        task, scope = pending
        await self._broker.scope_validator.revalidate(scope)
        # Inspect proposal data, but do not persist it or use it as trusted facts.
        for text in (decision.diagnosis, *decision.proposed_plan, *decision.constraints,
                     *decision.acceptance_criteria, *decision.verification_plan):
            self._broker.gate.sanitize(text, max_input_chars=self._broker.policy.budget.max_input_chars)
        fresh = await self.assess(task)
        if fresh.status is not TaskCapabilityStatus.BLOCKED:
            is_destructive = any(self._DESTRUCTIVE_ACTIONS.search(step) for step in decision.proposed_plan)
            # External recommendation is ADVISORY ONLY. It may request escalation (NEEDS_APPROVAL),
            # but final execution authority derives strictly from server-owned facts.
            if task.approval_required or is_destructive or decision.recommended_execution_class is TaskCapabilityStatus.NEEDS_APPROVAL:
                fresh = replace(
                    fresh, status=TaskCapabilityStatus.NEEDS_APPROVAL,
                    objective_reason="High-risk or approval-gated proposal requires explicit governed review.",
                    required_next_action="Review proposal through existing approval and execution policy.",
                )
            elif task.code_synthesis_required:
                fresh = replace(
                    fresh, status=TaskCapabilityStatus.NEEDS_CODEX,
                    objective_reason="Task requires code synthesis through existing coder integration.",
                    required_next_action="Use existing coder integration with its capability and approval gates.",
                )
            elif not fresh.missing_capabilities and not task.execution_denied:
                fresh = replace(
                    fresh, status=TaskCapabilityStatus.SELF_EXECUTABLE,
                    objective_reason="Proposal evaluated as safe, deterministic, and natively executable based on server facts.",
                    required_next_action="Use existing native tool execution with its policy checks.",
                )
            else:
                fresh = replace(
                    fresh, status=TaskCapabilityStatus.NEEDS_APPROVAL,
                    objective_reason="External reasoning is unverified and requires governed review.",
                    required_next_action="Review proposal through existing approval and execution policy.",
                )
        del self._pending[decision.request_id]
        return ReassessedDecision(decision.request_id, fresh, self._route(fresh.status))

    def discard_request(self, request_id: str) -> None:
        """Explicitly release a pending request without executing or persisting it."""
        self._pending.pop(request_id, None)
