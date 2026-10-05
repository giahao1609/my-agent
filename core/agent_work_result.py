from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .handoff_contracts import DecisionRequiredResult


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    change_type: str  # 'modified', 'created', 'deleted'
    diff_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "diff_summary": self.diff_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileChange:
        return cls(
            path=data["path"],
            change_type=data.get("change_type", "modified"),
            diff_summary=data.get("diff_summary", ""),
        )


@dataclass(frozen=True, slots=True)
class CommandExecution:
    command: str
    exit_code: int = 0
    output_summary: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "output_summary": self.output_summary,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandExecution:
        return cls(
            command=data["command"],
            exit_code=data.get("exit_code", 0),
            output_summary=data.get("output_summary", ""),
            duration_ms=data.get("duration_ms", 0),
        )


@dataclass(frozen=True, slots=True)
class TestExecutionResult:
    __test__ = False
    command: str = ""
    framework: str = ""
    status: str = "passed"  # 'passed', 'failed', 'skipped'
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_ms: int = 0
    summary: str = ""
    failure_details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_success(self) -> bool:
        return self.failed == 0 and self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "framework": self.framework,
            "status": self.status,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "failure_details": list(self.failure_details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestExecutionResult:
        return cls(
            command=data.get("command", ""),
            framework=data.get("framework", ""),
            status=data.get("status", "passed"),
            passed=data.get("passed", 0),
            failed=data.get("failed", 0),
            skipped=data.get("skipped", 0),
            duration_ms=data.get("duration_ms", 0),
            summary=data.get("summary", ""),
            failure_details=tuple(data.get("failure_details", ())),
        )


@dataclass(frozen=True, slots=True)
class AgentFinding:
    finding_id: str
    kind: str  # 'security', 'bug', 'architecture', 'ui', 'review', 'performance'
    severity: str  # 'critical', 'high', 'medium', 'low', 'info'
    title: str
    summary: str
    evidence: str = ""
    file_path: str = ""
    line: int | None = None
    remediation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "evidence": self.evidence,
            "file_path": self.file_path,
            "line": self.line,
            "remediation": self.remediation,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentFinding:
        return cls(
            finding_id=data["finding_id"],
            kind=data.get("kind", "general"),
            severity=data.get("severity", "medium"),
            title=data["title"],
            summary=data.get("summary", ""),
            evidence=data.get("evidence", ""),
            file_path=data.get("file_path", ""),
            line=data.get("line"),
            remediation=data.get("remediation", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class AgentArtifact:
    artifact_id: str
    kind: str  # 'diff', 'screenshot', 'report', 'trace', 'coverage', 'sbom'
    uri: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "uri": self.uri,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentArtifact:
        return cls(
            artifact_id=data["artifact_id"],
            kind=data.get("kind", "file"),
            uri=data["uri"],
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class AgentDecisionReference:
    decision_id: str
    prompt: str
    selected_option_id: str | None = None
    rationale: str | None = None
    severity: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "prompt": self.prompt,
            "selected_option_id": self.selected_option_id,
            "rationale": self.rationale,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDecisionReference:
        return cls(
            decision_id=data["decision_id"],
            prompt=data["prompt"],
            selected_option_id=data.get("selected_option_id"),
            rationale=data.get("rationale"),
            severity=data.get("severity", "medium"),
        )


_SECRET_REDACT_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
)


def redact_text(text: str) -> str:
    res = text
    for pat in _SECRET_REDACT_PATTERNS:
        res = pat.sub("[REDACTED_SECRET]", res)
    return res


@dataclass(frozen=True, slots=True)
class AgentWorkResult:
    """Structured, machine-readable output produced at the completion of an AgentRun."""

    run_id: str
    status: str  # 'completed', 'failed', 'cancelled'
    summary: str
    changed_files: tuple[FileChange, ...] = field(default_factory=tuple)
    created_files: tuple[str, ...] = field(default_factory=tuple)
    deleted_files: tuple[str, ...] = field(default_factory=tuple)
    commands_run: tuple[CommandExecution, ...] = field(default_factory=tuple)
    tests: tuple[TestExecutionResult, ...] = field(default_factory=tuple)
    findings: tuple[AgentFinding, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[AgentArtifact, ...] = field(default_factory=tuple)
    decisions_made: tuple[AgentDecisionReference, ...] = field(default_factory=tuple)
    decision_required: DecisionRequiredResult | None = None
    remaining_work: tuple[str, ...] = field(default_factory=tuple)
    handoff_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "summary": redact_text(self.summary),
            "changed_files": [f.to_dict() for f in self.changed_files],
            "created_files": list(self.created_files),
            "deleted_files": list(self.deleted_files),
            "commands_run": [c.to_dict() for c in self.commands_run],
            "tests": [t.to_dict() for t in self.tests],
            "findings": [f.to_dict() for f in self.findings],
            "warnings": [redact_text(w) for w in self.warnings],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "decisions_made": [d.to_dict() for d in self.decisions_made],
            "decision_required": self.decision_required.to_dict() if self.decision_required else None,
            "remaining_work": [redact_text(r) for r in self.remaining_work],
            "handoff_notes": redact_text(self.handoff_notes),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentWorkResult:
        dec_req = None
        if data.get("decision_required"):
            dec_req = DecisionRequiredResult.from_dict(data["decision_required"])

        return cls(
            run_id=data["run_id"],
            status=data.get("status", "completed"),
            summary=data.get("summary", ""),
            changed_files=tuple(FileChange.from_dict(f) for f in data.get("changed_files", ())),
            created_files=tuple(data.get("created_files", ())),
            deleted_files=tuple(data.get("deleted_files", ())),
            commands_run=tuple(CommandExecution.from_dict(c) for c in data.get("commands_run", ())),
            tests=tuple(TestExecutionResult.from_dict(t) for t in data.get("tests", ())),
            findings=tuple(AgentFinding.from_dict(f) for f in data.get("findings", ())),
            warnings=tuple(data.get("warnings", ())),
            artifacts=tuple(AgentArtifact.from_dict(a) for a in data.get("artifacts", ())),
            decisions_made=tuple(AgentDecisionReference.from_dict(d) for d in data.get("decisions_made", ())),
            decision_required=dec_req,
            remaining_work=tuple(data.get("remaining_work", ())),
            handoff_notes=data.get("handoff_notes", ""),
            metadata=data.get("metadata", {}),
        )
