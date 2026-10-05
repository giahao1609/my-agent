from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.task import TaskRecord, TaskState


class SWEBenchEvalStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    EMPTY_PATCH = "empty_patch"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SWEBenchInstance:
    """Represents a benchmark task instance from SWE-bench / SWE-bench Lite."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    golden_patch: str = ""
    fail_to_pass: tuple[str, ...] = field(default_factory=tuple)
    pass_to_pass: tuple[str, ...] = field(default_factory=tuple)
    version: str = ""
    environment_setup_commit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "problem_statement": self.problem_statement,
            "hints_text": self.hints_text,
            "test_patch": self.test_patch,
            "golden_patch": self.golden_patch,
            "FAIL_TO_PASS": list(self.fail_to_pass),
            "PASS_TO_PASS": list(self.pass_to_pass),
            "version": self.version,
            "environment_setup_commit": self.environment_setup_commit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SWEBenchInstance:
        fail_to_pass = data.get("FAIL_TO_PASS") or data.get("fail_to_pass") or ()
        if isinstance(fail_to_pass, str):
            fail_to_pass = tuple(json.loads(fail_to_pass))
        else:
            fail_to_pass = tuple(fail_to_pass)

        pass_to_pass = data.get("PASS_TO_PASS") or data.get("pass_to_pass") or ()
        if isinstance(pass_to_pass, str):
            pass_to_pass = tuple(json.loads(pass_to_pass))
        else:
            pass_to_pass = tuple(pass_to_pass)

        return cls(
            instance_id=data["instance_id"],
            repo=data["repo"],
            base_commit=data["base_commit"],
            problem_statement=data["problem_statement"],
            hints_text=data.get("hints_text", ""),
            test_patch=data.get("test_patch", ""),
            golden_patch=data.get("patch", "") or data.get("golden_patch", ""),
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            version=data.get("version", ""),
            environment_setup_commit=data.get("environment_setup_commit", ""),
        )

    @classmethod
    def from_json(cls, json_str: str) -> SWEBenchInstance:
        return cls.from_dict(json.loads(json_str))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass(frozen=True, slots=True)
class SWEBenchPrediction:
    """Represents an agent prediction/solution in standard SWE-bench format."""

    instance_id: str
    model_name_or_path: str
    model_patch: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": self.model_name_or_path,
            "model_patch": self.model_patch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SWEBenchPrediction:
        return cls(
            instance_id=data["instance_id"],
            model_name_or_path=data.get("model_name_or_path", "my-agent"),
            model_patch=data.get("model_patch", ""),
        )


@dataclass(frozen=True, slots=True)
class SWEBenchEvalResult:
    """Evaluation result for a single SWE-bench instance."""

    instance_id: str
    status: SWEBenchEvalStatus
    resolved: bool
    fail_to_pass_passed: tuple[str, ...] = field(default_factory=tuple)
    fail_to_pass_failed: tuple[str, ...] = field(default_factory=tuple)
    pass_to_pass_passed: tuple[str, ...] = field(default_factory=tuple)
    pass_to_pass_failed: tuple[str, ...] = field(default_factory=tuple)
    duration_seconds: float = 0.0
    cost_usd: float = 0.0
    log: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "status": self.status.value,
            "resolved": self.resolved,
            "fail_to_pass_passed": list(self.fail_to_pass_passed),
            "fail_to_pass_failed": list(self.fail_to_pass_failed),
            "pass_to_pass_passed": list(self.pass_to_pass_passed),
            "pass_to_pass_failed": list(self.pass_to_pass_failed),
            "duration_seconds": self.duration_seconds,
            "cost_usd": self.cost_usd,
            "log": self.log,
        }


@dataclass(frozen=True, slots=True)
class SWEBenchBatchReport:
    """Aggregated evaluation report across multiple SWE-bench instances."""

    total_instances: int
    resolved_instances: int
    pass_rate: float
    empty_patch_count: int
    error_count: int
    duration_seconds: float
    total_cost_usd: float
    results: tuple[SWEBenchEvalResult, ...] = field(default_factory=tuple)
    predictions: tuple[SWEBenchPrediction, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_instances": self.total_instances,
            "resolved_instances": self.resolved_instances,
            "pass_rate": round(self.pass_rate, 4),
            "empty_patch_count": self.empty_patch_count,
            "error_count": self.error_count,
            "duration_seconds": round(self.duration_seconds, 2),
            "total_cost_usd": round(self.total_cost_usd, 4),
            "results": [r.to_dict() for r in self.results],
        }

    def export_predictions_json(self, file_path: Path | str) -> None:
        """Exports predictions in official SWE-bench submission format (dict keyed by instance_id)."""
        export_data = {
            pred.instance_id: {
                "model_name_or_path": pred.model_name_or_path,
                "model_patch": pred.model_patch,
            }
            for pred in self.predictions
        }
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(export_data, indent=2), encoding="utf-8")


class SWEBenchTaskAdapter:
    """Adapter bridging SWE-bench instances into MyAgent tasks and workspaces."""

    @staticmethod
    def create_task(instance: SWEBenchInstance, project_id: str = "swebench-project") -> TaskRecord:
        """Converts a SWE-bench instance into a durable MyAgent TaskRecord."""
        return TaskRecord(
            task_id=f"swe-{instance.instance_id}",
            project_id=project_id,
            objective=instance.problem_statement,
            state=TaskState.CREATED,
        )

    @staticmethod
    def extract_model_patch(workspace_path: Path | str) -> str:
        """Extracts the git unified diff produced by the agent in the workspace."""
        path = Path(workspace_path)
        try:
            res = subprocess.run(
                ["git", "diff"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=False,
            )
            patch = res.stdout
            if patch and not patch.endswith("\n"):
                patch += "\n"
            return patch
        except Exception:
            return ""

    @staticmethod
    def apply_patch(workspace_path: Path | str, patch_text: str) -> bool:
        """Applies a unified diff patch to the workspace."""
        if not patch_text.strip():
            return True
        if not patch_text.endswith("\n"):
            patch_text += "\n"
        path = Path(workspace_path)
        try:
            res = subprocess.run(
                ["git", "apply", "--whitespace=nowarn"],
                input=patch_text,
                cwd=str(path),
                capture_output=True,
                text=True,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False


class SWEBenchEvaluator:
    """Evaluates agent predictions against SWE-bench pass criteria."""

    def evaluate(
        self,
        instance: SWEBenchInstance,
        prediction: SWEBenchPrediction,
        test_results: Mapping[str, bool] | None = None,
        duration_seconds: float = 0.0,
        cost_usd: float = 0.0,
    ) -> SWEBenchEvalResult:
        """Evaluates whether prediction resolves the instance.

        If test_results is provided, it maps test_name -> passed (bool).
        Criteria for RESOLVED:
        1. Model patch must not be empty.
        2. Every test in FAIL_TO_PASS must pass (True).
        3. Every test in PASS_TO_PASS must continue to pass (True).
        """
        patch = prediction.model_patch.strip()
        if not patch:
            return SWEBenchEvalResult(
                instance_id=instance.instance_id,
                status=SWEBenchEvalStatus.EMPTY_PATCH,
                resolved=False,
                duration_seconds=duration_seconds,
                cost_usd=cost_usd,
                log="No changes produced (empty patch)",
            )

        if test_results is None:
            # When no test runner output is supplied, fail by default (Fail-Closed)
            return SWEBenchEvalResult(
                instance_id=instance.instance_id,
                status=SWEBenchEvalStatus.UNRESOLVED,
                resolved=False,
                duration_seconds=duration_seconds,
                cost_usd=cost_usd,
                log="No test execution results provided for verification",
            )

        f2p_passed: list[str] = []
        f2p_failed: list[str] = []
        for t in instance.fail_to_pass:
            if test_results.get(t, False):
                f2p_passed.append(t)
            else:
                f2p_failed.append(t)

        p2p_passed: list[str] = []
        p2p_failed: list[str] = []
        for t in instance.pass_to_pass:
            if test_results.get(t, False):
                p2p_passed.append(t)
            else:
                p2p_failed.append(t)

        all_f2p_passed = len(f2p_passed) == len(instance.fail_to_pass) if instance.fail_to_pass else True
        no_p2p_failed = len(p2p_failed) == 0

        is_resolved = all_f2p_passed and no_p2p_failed

        return SWEBenchEvalResult(
            instance_id=instance.instance_id,
            status=SWEBenchEvalStatus.RESOLVED if is_resolved else SWEBenchEvalStatus.UNRESOLVED,
            resolved=is_resolved,
            fail_to_pass_passed=tuple(f2p_passed),
            fail_to_pass_failed=tuple(f2p_failed),
            pass_to_pass_passed=tuple(p2p_passed),
            pass_to_pass_failed=tuple(p2p_failed),
            duration_seconds=duration_seconds,
            cost_usd=cost_usd,
            log=f"Resolved={is_resolved}. F2P: {len(f2p_passed)}/{len(instance.fail_to_pass)}, P2P failures: {len(p2p_failed)}",
        )


class SWEBenchHarness:
    """Coordinates batch benchmark runs and prediction generation for SWE-bench."""

    def __init__(self, evaluator: SWEBenchEvaluator | None = None) -> None:
        self._evaluator = evaluator or SWEBenchEvaluator()

    def evaluate_batch(
        self,
        instances: Sequence[SWEBenchInstance],
        predictions: Sequence[SWEBenchPrediction],
        test_results_map: Mapping[str, Mapping[str, bool]] | None = None,
        duration_seconds: float = 0.0,
        total_cost_usd: float = 0.0,
    ) -> SWEBenchBatchReport:
        pred_by_id = {p.instance_id: p for p in predictions}
        results: list[SWEBenchEvalResult] = []
        empty_patches = 0
        errors = 0
        resolved_count = 0

        for inst in instances:
            pred = pred_by_id.get(
                inst.instance_id,
                SWEBenchPrediction(
                    instance_id=inst.instance_id,
                    model_name_or_path="my-agent",
                    model_patch="",
                ),
            )
            t_res = (test_results_map or {}).get(inst.instance_id)
            eval_res = self._evaluator.evaluate(inst, pred, test_results=t_res)
            results.append(eval_res)

            if eval_res.resolved:
                resolved_count += 1
            if eval_res.status == SWEBenchEvalStatus.EMPTY_PATCH:
                empty_patches += 1
            elif eval_res.status == SWEBenchEvalStatus.ERROR:
                errors += 1

        total = len(instances)
        pass_rate = (resolved_count / total) if total > 0 else 0.0

        return SWEBenchBatchReport(
            total_instances=total,
            resolved_instances=resolved_count,
            pass_rate=pass_rate,
            empty_patch_count=empty_patches,
            error_count=errors,
            duration_seconds=duration_seconds,
            total_cost_usd=total_cost_usd,
            results=tuple(results),
            predictions=tuple(predictions),
        )
