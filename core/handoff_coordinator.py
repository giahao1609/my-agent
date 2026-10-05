from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from .agent_role import AgentRole
from .ai_security import PromptInjectionDefense
from .security_scanner import SecretRedactor


class HandoffDecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REDIRECTED = "redirected"


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    source_role: AgentRole
    target_role: AgentRole
    task_id: str
    step_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    handoff_id: str = field(default_factory=lambda: f"handoff-{uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "source_role": self.source_role.value,
            "target_role": self.target_role.value,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "payload": self.payload,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class HandoffDecision:
    handoff_id: str
    status: HandoffDecisionStatus
    source_role: AgentRole
    target_role: AgentRole
    task_id: str
    sanitized_payload: dict[str, Any]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    guardrail_results: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "status": self.status.value,
            "source_role": self.source_role.value,
            "target_role": self.target_role.value,
            "task_id": self.task_id,
            "sanitized_payload": self.sanitized_payload,
            "reasons": list(self.reasons),
            "guardrail_results": self.guardrail_results,
            "timestamp": self.timestamp,
        }


class HandoffTransitionMatrix:
    """Defines valid and authorized handoff pathways between specialist agent roles.

    Prevents chaotic peer-to-peer delegation and policy bypassing (e.g. Coder -> Release).
    """

    ALLOWED_TRANSITIONS: dict[AgentRole, set[AgentRole]] = {
        AgentRole.USER_INTERFACE: {
            AgentRole.PLANNER,
            AgentRole.ARCHITECT,
            AgentRole.RESEARCHER,
        },
        AgentRole.PLANNER: {
            AgentRole.ARCHITECT,
            AgentRole.RESEARCHER,
            AgentRole.BACKEND_CODER,
            AgentRole.UI_CODER,
            AgentRole.DOCUMENTATION,
        },
        AgentRole.ARCHITECT: {
            AgentRole.PLANNER,
            AgentRole.BACKEND_CODER,
            AgentRole.UI_CODER,
            AgentRole.RESEARCHER,
        },
        AgentRole.RESEARCHER: {
            AgentRole.PLANNER,
            AgentRole.ARCHITECT,
            AgentRole.BACKEND_CODER,
            AgentRole.UI_CODER,
        },
        AgentRole.BACKEND_CODER: {
            AgentRole.TESTER,
            AgentRole.UI_CODER,
            AgentRole.DB_MIGRATION,
            AgentRole.PERFORMANCE,
            AgentRole.REVIEWER,
        },
        AgentRole.UI_CODER: {
            AgentRole.TESTER,
            AgentRole.BACKEND_CODER,
            AgentRole.REVIEWER,
        },
        AgentRole.DB_MIGRATION: {
            AgentRole.BACKEND_CODER,
            AgentRole.TESTER,
        },
        AgentRole.PERFORMANCE: {
            AgentRole.BACKEND_CODER,
            AgentRole.TESTER,
        },
        AgentRole.TESTER: {
            AgentRole.SECURITY_REVIEWER,
            AgentRole.REVIEWER,
            AgentRole.BACKEND_CODER,
            AgentRole.UI_CODER,
        },
        AgentRole.SECURITY_REVIEWER: {
            AgentRole.REVIEWER,
            AgentRole.BACKEND_CODER,
        },
        AgentRole.REVIEWER: {
            AgentRole.RELEASE,
            AgentRole.BACKEND_CODER,
            AgentRole.UI_CODER,
            AgentRole.PLANNER,
        },
        AgentRole.RELEASE: {
            AgentRole.DOCUMENTATION,
            AgentRole.PLANNER,
        },
        AgentRole.DOCUMENTATION: {
            AgentRole.PLANNER,
            AgentRole.RELEASE,
        },
    }

    @classmethod
    def is_transition_allowed(cls, source: AgentRole, target: AgentRole) -> bool:
        allowed = cls.ALLOWED_TRANSITIONS.get(source, set())
        return target in allowed

    @classmethod
    def get_allowed_targets(cls, source: AgentRole) -> list[AgentRole]:
        return sorted(list(cls.ALLOWED_TRANSITIONS.get(source, set())), key=lambda r: r.value)


class HandoffGuardrailEngine:
    """Evaluates payload security, schema integrity, and prompt safety before handoff."""

    # Allowed payload top-level keys per target role.
    # Keys not in this set are stripped and flagged.
    PAYLOAD_SCHEMA: dict[AgentRole, frozenset[str]] = {
        AgentRole.PLANNER: frozenset({"task_id", "objective", "context", "constraints", "history"}),
        AgentRole.ARCHITECT: frozenset({"task_id", "objective", "context", "constraints", "existing_architecture"}),
        AgentRole.RESEARCHER: frozenset({"task_id", "query", "context", "sources"}),
        AgentRole.BACKEND_CODER: frozenset({"task_id", "step_id", "plan", "context", "files", "repair_hint", "objective"}),
        AgentRole.UI_CODER: frozenset({"task_id", "step_id", "plan", "context", "files", "repair_hint", "design_tokens", "objective"}),
        AgentRole.TESTER: frozenset({"task_id", "step_id", "objective", "changed_files", "context"}),
        AgentRole.SECURITY_REVIEWER: frozenset({"task_id", "step_id", "objective", "changed_files", "context"}),
        AgentRole.REVIEWER: frozenset({"task_id", "step_id", "objective", "changed_files", "context", "review_criteria"}),
        AgentRole.RELEASE: frozenset({"task_id", "plan_id", "changelog", "version", "artifacts"}),
        AgentRole.DOCUMENTATION: frozenset({"task_id", "objective", "changed_files", "context"}),
        AgentRole.DB_MIGRATION: frozenset({"task_id", "step_id", "migration_plan", "context"}),
        AgentRole.PERFORMANCE: frozenset({"task_id", "step_id", "profiling_data", "context"}),
    }

    def __init__(
        self,
        prompt_defense: PromptInjectionDefense | None = None,
        strict_schema: bool = False,
    ) -> None:
        self._prompt_defense = prompt_defense or PromptInjectionDefense()
        self.strict_schema = strict_schema

    def sanitize_payload_recursive(self, data: Any) -> Any:
        if isinstance(data, str):
            # Redact secrets
            redacted = SecretRedactor.redact(data)
            # Redact prompt injection
            inspection = self._prompt_defense.inspect(redacted)
            return inspection.sanitized_text
        elif isinstance(data, dict):
            return {k: self.sanitize_payload_recursive(v) for k, v in data.items()}
        elif isinstance(data, (list, tuple)):
            return [self.sanitize_payload_recursive(item) for item in data]
        return data

    def prune_payload(self, payload: Any) -> tuple[Any, bool]:
        """Prunes historical conversation contexts if they exceed threshold."""
        was_pruned = False
        if not isinstance(payload, dict):
            return payload, False

        pruned = dict(payload)

        # 1. Prune conversation history lists to keep only recent messages + summary
        if "history" in pruned and isinstance(pruned["history"], list):
            history = pruned["history"]
            if len(history) > 15:
                # Keep first message context + last 15 messages
                pruned["history"] = [
                    {"role": "system", "content": f"[{len(history) - 15} earlier messages truncated for token efficiency]"}
                ] + history[-15:]
                was_pruned = True

        # 2. Truncate arbitrarily huge raw string fields (> 5000 chars)
        for k, v in list(pruned.items()):
            if isinstance(v, str) and len(v) > 5000:
                pruned[k] = v[:4000] + "\n...[truncated]"
                was_pruned = True

        return pruned, was_pruned

    def evaluate_guardrails(
        self,
        request: HandoffRequest,
    ) -> tuple[bool, list[str], dict[str, str], Any]:
        reasons: list[str] = []
        guardrail_results: dict[str, str] = {}

        # 1. Check transition authorization
        if not HandoffTransitionMatrix.is_transition_allowed(request.source_role, request.target_role):
            reasons.append(
                f"Unauthorized role transition: {request.source_role.value} cannot handoff directly to {request.target_role.value}"
            )
            guardrail_results["transition_matrix"] = "FAIL"
        else:
            guardrail_results["transition_matrix"] = "PASS"

        # 2. Check prompt injection in reason & string values
        injection_found = False
        if request.reason:
            inspection = self._prompt_defense.inspect(request.reason)
            if not injection_found and not inspection.is_safe:
                reasons.append(f"Prompt injection detected in handoff reason: {inspection.detected_patterns}")
                injection_found = True

        guardrail_results["prompt_safety"] = "FAIL" if injection_found else "PASS"

        # 3. Context Pruning
        pruned_payload, was_pruned = self.prune_payload(request.payload)
        guardrail_results["payload_pruned"] = "PRUNED" if was_pruned else "UNCHANGED"

        # 4. Payload schema validation
        allowed_keys = self.PAYLOAD_SCHEMA.get(request.target_role)
        if allowed_keys is not None and isinstance(pruned_payload, dict):
            unknown_keys = set(pruned_payload.keys()) - allowed_keys
            if unknown_keys:
                guardrail_results["schema_validation"] = f"FLAGGED_KEYS:{','.join(sorted(unknown_keys))}"
                if self.strict_schema:
                    pruned_payload = {k: v for k, v in pruned_payload.items() if k in allowed_keys}
            else:
                guardrail_results["schema_validation"] = "PASS"
        else:
            guardrail_results["schema_validation"] = "NO_SCHEMA_DEFINED"

        # 5. Sanitize payload
        sanitized = self.sanitize_payload_recursive(pruned_payload)
        guardrail_results["secret_sanitization"] = "APPLIED"

        is_approved = len(reasons) == 0
        return is_approved, reasons, guardrail_results, sanitized


class _HandoffHistoryStore(Protocol):
    """Minimal async protocol for durable handoff history persistence."""
    async def save(self, decision: dict[str, Any]) -> None: ...
    async def list_by_task(self, task_id: str) -> list[dict[str, Any]]: ...
    async def list_stale(self, accepted_before_timestamp: float) -> list[dict[str, Any]]: ...


class HandoffCoordinator:
    """Coordinator-controlled explicit handoff engine between specialist agent roles."""

    def __init__(
        self,
        guardrail_engine: HandoffGuardrailEngine | None = None,
        history_store: _HandoffHistoryStore | None = None,
    ) -> None:
        self._guardrail_engine = guardrail_engine or HandoffGuardrailEngine()
        self._history: list[HandoffDecision] = []  # In-memory cache
        self._history_store = history_store  # Optional durable store

    def request_handoff(
        self,
        source_role: AgentRole | str,
        target_role: AgentRole | str,
        task_id: str,
        step_id: str | None = None,
        payload: dict[str, Any] | None = None,
        reason: str = "",
    ) -> HandoffDecision:
        src = AgentRole(source_role) if isinstance(source_role, str) else source_role
        tgt = AgentRole(target_role) if isinstance(target_role, str) else target_role

        req = HandoffRequest(
            source_role=src,
            target_role=tgt,
            task_id=task_id,
            step_id=step_id,
            payload=payload or {},
            reason=reason,
        )

        is_approved, reasons, guardrails, sanitized_payload = self._guardrail_engine.evaluate_guardrails(req)

        decision = HandoffDecision(
            handoff_id=req.handoff_id,
            status=HandoffDecisionStatus.ACCEPTED if is_approved else HandoffDecisionStatus.REJECTED,
            source_role=src,
            target_role=tgt,
            task_id=task_id,
            sanitized_payload=sanitized_payload,
            reasons=tuple(reasons),
            guardrail_results=guardrails,
        )

        self._history.append(decision)
        # Persist durably if a store is wired in (fire-and-forget in sync context)
        if self._history_store is not None:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._history_store.save(decision.to_dict()))
                else:
                    loop.run_until_complete(self._history_store.save(decision.to_dict()))
            except Exception:
                pass  # Never let persistence failure break handoff logic
        return decision

    def get_allowed_targets(self, role: AgentRole | str) -> list[str]:
        r = AgentRole(role) if isinstance(role, str) else role
        return [target.value for target in HandoffTransitionMatrix.get_allowed_targets(r)]

    def get_handoff_history(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id is not None:
            return [d.to_dict() for d in self._history if d.task_id == task_id]
        return [d.to_dict() for d in self._history]

    def get_stale_handoffs(
        self,
        timeout_seconds: float = 300.0,
    ) -> list[HandoffDecision]:
        """Returns ACCEPTED handoffs older than ``timeout_seconds`` that have
        not yet been followed by a corresponding completion event.  These may
        indicate a stale or timed-out target agent that needs intervention.
        """
        cutoff = time.time() - timeout_seconds
        return [
            d for d in self._history
            if d.status == HandoffDecisionStatus.ACCEPTED and d.timestamp < cutoff
        ]
