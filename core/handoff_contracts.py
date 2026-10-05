from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReviewStatus(StrEnum):
    APPROVED = 'approved'
    REJECTED = 'rejected'
    REQUEST_REWORK = 'request_rework'


class SecuritySeverity(StrEnum):
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'
    INFO = 'info'


@dataclass(frozen=True, slots=True)
class ArchitectureProposal:
    task_id: str
    summary: str
    affected_components: tuple[str, ...] = field(default_factory=tuple)
    interfaces_to_add: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            'task_id': self.task_id,
            'summary': self.summary,
            'affected_components': list(self.affected_components),
            'interfaces_to_add': list(self.interfaces_to_add),
            'constraints': list(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitectureProposal:
        return cls(
            task_id=data['task_id'],
            summary=data['summary'],
            affected_components=tuple(data.get('affected_components', ())),
            interfaces_to_add=tuple(data.get('interfaces_to_add', ())),
            constraints=tuple(data.get('constraints', ())),
        )


@dataclass(frozen=True, slots=True)
class ResearchResult:
    topic: str
    summary: str
    findings: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    recommendations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            'topic': self.topic,
            'summary': self.summary,
            'findings': list(self.findings),
            'sources': list(self.sources),
            'recommendations': list(self.recommendations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchResult:
        return cls(
            topic=data['topic'],
            summary=data['summary'],
            findings=tuple(data.get('findings', ())),
            sources=tuple(data.get('sources', ())),
            recommendations=tuple(data.get('recommendations', ())),
        )


@dataclass(frozen=True, slots=True)
class ImplementationResult:
    step_id: str
    summary: str
    modified_files: tuple[str, ...] = field(default_factory=tuple)
    created_files: tuple[str, ...] = field(default_factory=tuple)
    deleted_files: tuple[str, ...] = field(default_factory=tuple)
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'summary': self.summary,
            'modified_files': list(self.modified_files),
            'created_files': list(self.created_files),
            'deleted_files': list(self.deleted_files),
            'success': self.success,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImplementationResult:
        return cls(
            step_id=data['step_id'],
            summary=data['summary'],
            modified_files=tuple(data.get('modified_files', ())),
            created_files=tuple(data.get('created_files', ())),
            deleted_files=tuple(data.get('deleted_files', ())),
            success=data.get('success', True),
        )


@dataclass(frozen=True, slots=True)
class UiImplementationResult:
    step_id: str
    summary: str
    modified_files: tuple[str, ...] = field(default_factory=tuple)
    components_reused: tuple[str, ...] = field(default_factory=tuple)
    components_created: tuple[str, ...] = field(default_factory=tuple)
    a11y_status: str = 'passed'
    success: bool = True
    # Design intelligence fields (Phase 2 — UI Creative Engine)
    visual_language: str = ''                          # e.g. "Neo-Brutalism"
    design_originality_score: float = 0.0              # 0.0-1.0
    anti_patterns_avoided: tuple[str, ...] = field(default_factory=tuple)
    anti_patterns_detected: tuple[str, ...] = field(default_factory=tuple)
    design_brief_used: str = ''                        # short summary of design brief applied

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'summary': self.summary,
            'modified_files': list(self.modified_files),
            'components_reused': list(self.components_reused),
            'components_created': list(self.components_created),
            'a11y_status': self.a11y_status,
            'success': self.success,
            'visual_language': self.visual_language,
            'design_originality_score': self.design_originality_score,
            'anti_patterns_avoided': list(self.anti_patterns_avoided),
            'anti_patterns_detected': list(self.anti_patterns_detected),
            'design_brief_used': self.design_brief_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UiImplementationResult:
        return cls(
            step_id=data['step_id'],
            summary=data['summary'],
            modified_files=tuple(data.get('modified_files', ())),
            components_reused=tuple(data.get('components_reused', ())),
            components_created=tuple(data.get('components_created', ())),
            a11y_status=data.get('a11y_status', 'passed'),
            success=data.get('success', True),
            visual_language=data.get('visual_language', ''),
            design_originality_score=data.get('design_originality_score', 0.0),
            anti_patterns_avoided=tuple(data.get('anti_patterns_avoided', ())),
            anti_patterns_detected=tuple(data.get('anti_patterns_detected', ())),
            design_brief_used=data.get('design_brief_used', ''),
        )


@dataclass(frozen=True, slots=True)
class TestResult:
    __test__ = False
    step_id: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_ms: int = 0
    failure_details: tuple[str, ...] = field(default_factory=tuple)
    coverage_percentage: float | None = None

    @property
    def success(self) -> bool:
        return self.failed_tests == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'total_tests': self.total_tests,
            'passed_tests': self.passed_tests,
            'failed_tests': self.failed_tests,
            'duration_ms': self.duration_ms,
            'failure_details': list(self.failure_details),
            'coverage_percentage': self.coverage_percentage,
            'success': self.success,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        return cls(
            step_id=data['step_id'],
            total_tests=data['total_tests'],
            passed_tests=data['passed_tests'],
            failed_tests=data['failed_tests'],
            duration_ms=data.get('duration_ms', 0),
            failure_details=tuple(data.get('failure_details', ())),
            coverage_percentage=data.get('coverage_percentage'),
        )


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    rule_id: str
    severity: SecuritySeverity
    file_path: str
    line_number: int
    description: str
    remediation: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'rule_id': self.rule_id,
            'severity': self.severity.value,
            'file_path': self.file_path,
            'line_number': self.line_number,
            'description': self.description,
            'remediation': self.remediation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityFinding:
        return cls(
            rule_id=data['rule_id'],
            severity=SecuritySeverity(data['severity']),
            file_path=data['file_path'],
            line_number=data['line_number'],
            description=data['description'],
            remediation=data.get('remediation', ''),
        )


@dataclass(frozen=True, slots=True)
class SecurityReviewResult:
    step_id: str
    passed_gate: bool
    summary: str
    findings: tuple[SecurityFinding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'passed_gate': self.passed_gate,
            'summary': self.summary,
            'findings': [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityReviewResult:
        return cls(
            step_id=data['step_id'],
            passed_gate=data['passed_gate'],
            summary=data['summary'],
            findings=tuple(SecurityFinding.from_dict(f) for f in data.get('findings', ())),
        )


@dataclass(frozen=True, slots=True)
class ReviewResult:
    step_id: str
    status: ReviewStatus
    summary: str
    comments: tuple[str, ...] = field(default_factory=tuple)
    required_repairs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'status': self.status.value,
            'summary': self.summary,
            'comments': list(self.comments),
            'required_repairs': list(self.required_repairs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewResult:
        return cls(
            step_id=data['step_id'],
            status=ReviewStatus(data['status']),
            summary=data['summary'],
            comments=tuple(data.get('comments', ())),
            required_repairs=tuple(data.get('required_repairs', ())),
        )


@dataclass(frozen=True, slots=True)
class DecisionOptionDraft:
    option_id: str
    title: str
    description: str
    trade_offs: str = ''
    impact: str = ''
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            'option_id': self.option_id,
            'title': self.title,
            'description': self.description,
            'trade_offs': self.trade_offs,
            'impact': self.impact,
            'recommended': self.recommended,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionOptionDraft:
        return cls(
            option_id=data['option_id'],
            title=data['title'],
            description=data['description'],
            trade_offs=data.get('trade_offs', ''),
            impact=data.get('impact', ''),
            recommended=data.get('recommended', False),
        )


@dataclass(frozen=True, slots=True)
class DecisionRequiredResult:
    """Emitted by a specialist agent when a choice between multiple valid paths is required."""
    step_id: str
    prompt: str
    severity: str
    options: tuple[DecisionOptionDraft, ...] = field(default_factory=tuple)
    rationale: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'prompt': self.prompt,
            'severity': self.severity,
            'options': [opt.to_dict() for opt in self.options],
            'rationale': self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRequiredResult:
        return cls(
            step_id=data['step_id'],
            prompt=data['prompt'],
            severity=data['severity'],
            options=tuple(DecisionOptionDraft.from_dict(opt) for opt in data.get('options', ())),
            rationale=data.get('rationale', ''),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkMetric:
    name: str
    iterations: int
    total_duration_ms: float
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float
    ops_per_sec: float
    memory_bytes: int = 0
    passed_slo: bool = True
    slo_threshold_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "total_duration_ms": self.total_duration_ms,
            "avg_duration_ms": self.avg_duration_ms,
            "min_duration_ms": self.min_duration_ms,
            "max_duration_ms": self.max_duration_ms,
            "ops_per_sec": self.ops_per_sec,
            "memory_bytes": self.memory_bytes,
            "passed_slo": self.passed_slo,
            "slo_threshold_ms": self.slo_threshold_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkMetric:
        return cls(
            name=data["name"],
            iterations=data["iterations"],
            total_duration_ms=data["total_duration_ms"],
            avg_duration_ms=data["avg_duration_ms"],
            min_duration_ms=data["min_duration_ms"],
            max_duration_ms=data["max_duration_ms"],
            ops_per_sec=data["ops_per_sec"],
            memory_bytes=data.get("memory_bytes", 0),
            passed_slo=data.get("passed_slo", True),
            slo_threshold_ms=data.get("slo_threshold_ms"),
        )


@dataclass(frozen=True, slots=True)
class PerformanceReviewResult:
    step_id: str
    passed_gate: bool
    summary: str
    metrics: tuple[BenchmarkMetric, ...] = field(default_factory=tuple)
    slo_violations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "passed_gate": self.passed_gate,
            "summary": self.summary,
            "metrics": [m.to_dict() for m in self.metrics],
            "slo_violations": list(self.slo_violations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceReviewResult:
        return cls(
            step_id=data["step_id"],
            passed_gate=data["passed_gate"],
            summary=data["summary"],
            metrics=tuple(BenchmarkMetric.from_dict(m) for m in data.get("metrics", ())),
            slo_violations=tuple(data.get("slo_violations", ())),
        )


@dataclass(frozen=True, slots=True)
class CoverageReviewResult:
    step_id: str
    coverage_percentage: float
    threshold_percentage: float
    passed_gate: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "coverage_percentage": self.coverage_percentage,
            "threshold_percentage": self.threshold_percentage,
            "passed_gate": self.passed_gate,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageReviewResult:
        return cls(
            step_id=data["step_id"],
            coverage_percentage=data["coverage_percentage"],
            threshold_percentage=data["threshold_percentage"],
            passed_gate=data["passed_gate"],
            summary=data["summary"],
        )


@dataclass(frozen=True, slots=True)
class VisualDiffResult:
    baseline_path: str
    current_path: str
    total_pixels: int
    differing_pixels: int
    diff_percentage: float
    tolerance_percentage: float
    passed: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_path": self.baseline_path,
            "current_path": self.current_path,
            "total_pixels": self.total_pixels,
            "differing_pixels": self.differing_pixels,
            "diff_percentage": self.diff_percentage,
            "tolerance_percentage": self.tolerance_percentage,
            "passed": self.passed,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualDiffResult:
        return cls(
            baseline_path=data["baseline_path"],
            current_path=data["current_path"],
            total_pixels=data["total_pixels"],
            differing_pixels=data["differing_pixels"],
            diff_percentage=data["diff_percentage"],
            tolerance_percentage=data["tolerance_percentage"],
            passed=data["passed"],
            summary=data["summary"],
        )


@dataclass(frozen=True, slots=True)
class GoalDriftResult:
    step_id: str
    passed: bool
    drift_detected: bool
    out_of_scope_files: tuple[str, ...] = field(default_factory=tuple)
    violations: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "passed": self.passed,
            "drift_detected": self.drift_detected,
            "out_of_scope_files": list(self.out_of_scope_files),
            "violations": list(self.violations),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalDriftResult:
        return cls(
            step_id=data["step_id"],
            passed=data["passed"],
            drift_detected=data["drift_detected"],
            out_of_scope_files=tuple(data.get("out_of_scope_files", ())),
            violations=tuple(data.get("violations", ())),
            summary=data.get("summary", ""),
        )


@dataclass(frozen=True, slots=True)
class CodeHygieneFinding:
    category: str
    severity: str
    file_path: str
    symbol_name: str
    details: str
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "file_path": self.file_path,
            "symbol_name": self.symbol_name,
            "details": self.details,
            "suggested_action": self.suggested_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeHygieneFinding:
        return cls(
            category=data["category"],
            severity=data["severity"],
            file_path=data["file_path"],
            symbol_name=data["symbol_name"],
            details=data["details"],
            suggested_action=data.get("suggested_action", ""),
        )


@dataclass(frozen=True, slots=True)
class CodeHygieneReviewResult:
    step_id: str
    passed_gate: bool
    summary: str
    cycles: tuple[str, ...] = field(default_factory=tuple)
    dead_code_count: int = 0
    findings: tuple[CodeHygieneFinding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "passed_gate": self.passed_gate,
            "summary": self.summary,
            "cycles": list(self.cycles),
            "dead_code_count": self.dead_code_count,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeHygieneReviewResult:
        return cls(
            step_id=data["step_id"],
            passed_gate=data["passed_gate"],
            summary=data["summary"],
            cycles=tuple(data.get("cycles", ())),
            dead_code_count=data.get("dead_code_count", 0),
            findings=tuple(CodeHygieneFinding.from_dict(f) for f in data.get("findings", ())),
        )


@dataclass(frozen=True, slots=True)
class PiiEntity:
    entity_type: str
    matched_text: str
    start_index: int
    end_index: int
    replacement: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "matched_text": self.matched_text,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "replacement": self.replacement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiiEntity:
        return cls(
            entity_type=data["entity_type"],
            matched_text=data["matched_text"],
            start_index=data["start_index"],
            end_index=data["end_index"],
            replacement=data["replacement"],
        )


@dataclass(frozen=True, slots=True)
class PiiSanitizationResult:
    original_length: int
    sanitized_text: str
    entities_found: tuple[PiiEntity, ...] = field(default_factory=tuple)
    pii_detected: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_length": self.original_length,
            "sanitized_text": self.sanitized_text,
            "entities_found": [e.to_dict() for e in self.entities_found],
            "pii_detected": self.pii_detected,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiiSanitizationResult:
        return cls(
            original_length=data["original_length"],
            sanitized_text=data["sanitized_text"],
            entities_found=tuple(PiiEntity.from_dict(e) for e in data.get("entities_found", ())),
            pii_detected=data.get("pii_detected", False),
            summary=data.get("summary", ""),
        )


