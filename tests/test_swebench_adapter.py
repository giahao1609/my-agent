from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
import pytest

from core.task import TaskState
from eval.swebench_adapter import (
    SWEBenchBatchReport,
    SWEBenchEvalResult,
    SWEBenchEvalStatus,
    SWEBenchEvaluator,
    SWEBenchHarness,
    SWEBenchInstance,
    SWEBenchPrediction,
    SWEBenchTaskAdapter,
)


def test_swebench_instance_serialization():
    data = {
        "instance_id": "pytest-dev__pytest-5227",
        "repo": "pytest-dev/pytest",
        "base_commit": "a1b2c3d4",
        "problem_statement": "Fix logging issue in CLI",
        "hints_text": "Check config.py",
        "test_patch": "diff --git a/tests/test_log.py ...",
        "patch": "diff --git a/src/log.py ...",
        "FAIL_TO_PASS": ["testing/test_logging.py::test_cli_log"],
        "PASS_TO_PASS": ["testing/test_logging.py::test_basic"],
        "version": "4.5",
        "environment_setup_commit": "setup123",
    }
    instance = SWEBenchInstance.from_dict(data)
    assert instance.instance_id == "pytest-dev__pytest-5227"
    assert instance.fail_to_pass == ("testing/test_logging.py::test_cli_log",)
    assert instance.pass_to_pass == ("testing/test_logging.py::test_basic",)
    assert instance.golden_patch == "diff --git a/src/log.py ..."

    # JSON roundtrip
    json_str = instance.to_json()
    reloaded = SWEBenchInstance.from_json(json_str)
    assert reloaded == instance


def test_swebench_instance_from_dict_with_json_strings():
    # In some datasets FAIL_TO_PASS is encoded as a JSON array string
    data = {
        "instance_id": "django__django-11099",
        "repo": "django/django",
        "base_commit": "commit123",
        "problem_statement": "Validator regex problem",
        "FAIL_TO_PASS": json.dumps(["tests.validators.TestRegex"]),
        "PASS_TO_PASS": json.dumps(["tests.validators.TestEmail"]),
    }
    instance = SWEBenchInstance.from_dict(data)
    assert instance.fail_to_pass == ("tests.validators.TestRegex",)
    assert instance.pass_to_pass == ("tests.validators.TestEmail",)


def test_swebench_prediction_serialization():
    pred = SWEBenchPrediction(
        instance_id="sympy__sympy-20590",
        model_name_or_path="my-agent-v1",
        model_patch="diff --git a/sympy/core.py ...\n+ new code",
    )
    d = pred.to_dict()
    assert d["instance_id"] == "sympy__sympy-20590"
    assert d["model_name_or_path"] == "my-agent-v1"

    restored = SWEBenchPrediction.from_dict(d)
    assert restored == pred


def test_task_adapter_create_task():
    inst = SWEBenchInstance(
        instance_id="astropy__astropy-1234",
        repo="astropy/astropy",
        base_commit="b1",
        problem_statement="Coordinate transformation precision bug",
    )
    task = SWEBenchTaskAdapter.create_task(inst, project_id="astropy-bench")
    assert task.task_id == "swe-astropy__astropy-1234"
    assert task.project_id == "astropy-bench"
    assert task.objective == "Coordinate transformation precision bug"
    assert task.state == TaskState.CREATED


def test_task_adapter_extract_and_apply_patch():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir)
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)

        target_file = repo_dir / "calc.py"
        target_file.write_text("def add(a, b):\n    return a - b\n")
        subprocess.run(["git", "add", "calc.py"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Initial bug"], cwd=str(repo_dir), check=True)

        # Agent fixes the bug
        target_file.write_text("def add(a, b):\n    return a + b\n")

        # Extract patch
        patch = SWEBenchTaskAdapter.extract_model_patch(repo_dir)
        assert "+    return a + b" in patch
        assert "-    return a - b" in patch

        # Reset git state
        subprocess.run(["git", "checkout", "--", "calc.py"], cwd=str(repo_dir), check=True)
        assert target_file.read_text() == "def add(a, b):\n    return a - b\n"

        # Apply patch back
        success = SWEBenchTaskAdapter.apply_patch(repo_dir, patch)
        assert success is True
        assert target_file.read_text() == "def add(a, b):\n    return a + b\n"


def test_swebench_evaluator_resolved():
    inst = SWEBenchInstance(
        instance_id="inst-1",
        repo="repo/demo",
        base_commit="c1",
        problem_statement="Bug",
        fail_to_pass=("test_f1", "test_f2"),
        pass_to_pass=("test_p1",),
    )
    pred = SWEBenchPrediction(
        instance_id="inst-1",
        model_name_or_path="my-agent",
        model_patch="diff --git a/code.py ...",
    )
    evaluator = SWEBenchEvaluator()
    res = evaluator.evaluate(
        inst,
        pred,
        test_results={
            "test_f1": True,
            "test_f2": True,
            "test_p1": True,
        },
        duration_seconds=12.5,
        cost_usd=0.03,
    )
    assert res.status == SWEBenchEvalStatus.RESOLVED
    assert res.resolved is True
    assert res.fail_to_pass_passed == ("test_f1", "test_f2")
    assert len(res.fail_to_pass_failed) == 0
    assert res.pass_to_pass_passed == ("test_p1",)
    assert res.duration_seconds == 12.5
    assert res.cost_usd == 0.03


def test_swebench_evaluator_unresolved_and_regression():
    inst = SWEBenchInstance(
        instance_id="inst-2",
        repo="repo/demo",
        base_commit="c1",
        problem_statement="Bug",
        fail_to_pass=("test_fix",),
        pass_to_pass=("test_existing",),
    )
    pred = SWEBenchPrediction(
        instance_id="inst-2",
        model_name_or_path="my-agent",
        model_patch="diff --git a/code.py ...",
    )
    evaluator = SWEBenchEvaluator()

    # Case A: fail_to_pass still fails
    res_a = evaluator.evaluate(inst, pred, test_results={"test_fix": False, "test_existing": True})
    assert res_a.status == SWEBenchEvalStatus.UNRESOLVED
    assert res_a.resolved is False
    assert "test_fix" in res_a.fail_to_pass_failed

    # Case B: regression in pass_to_pass
    res_b = evaluator.evaluate(inst, pred, test_results={"test_fix": True, "test_existing": False})
    assert res_b.status == SWEBenchEvalStatus.UNRESOLVED
    assert res_b.resolved is False
    assert "test_existing" in res_b.pass_to_pass_failed


def test_swebench_evaluator_empty_patch_and_no_test_results():
    inst = SWEBenchInstance(
        instance_id="inst-3",
        repo="repo/demo",
        base_commit="c1",
        problem_statement="Bug",
    )
    evaluator = SWEBenchEvaluator()

    # Empty patch
    pred_empty = SWEBenchPrediction(instance_id="inst-3", model_name_or_path="my-agent", model_patch="   ")
    res_empty = evaluator.evaluate(inst, pred_empty)
    assert res_empty.status == SWEBenchEvalStatus.EMPTY_PATCH
    assert res_empty.resolved is False

    # Non-empty patch but no test results provided (Fail-Closed)
    pred_valid = SWEBenchPrediction(instance_id="inst-3", model_name_or_path="my-agent", model_patch="diff ...")
    res_no_tests = evaluator.evaluate(inst, pred_valid, test_results=None)
    assert res_no_tests.status == SWEBenchEvalStatus.UNRESOLVED
    assert res_no_tests.resolved is False


def test_swebench_harness_batch_evaluation_and_export():
    inst1 = SWEBenchInstance(
        instance_id="task-1",
        repo="r/1",
        base_commit="c1",
        problem_statement="Problem 1",
        fail_to_pass=("test_1",),
    )
    inst2 = SWEBenchInstance(
        instance_id="task-2",
        repo="r/2",
        base_commit="c2",
        problem_statement="Problem 2",
        fail_to_pass=("test_2",),
    )

    pred1 = SWEBenchPrediction(
        instance_id="task-1",
        model_name_or_path="my-agent-pro",
        model_patch="diff --git a/a.py ...",
    )
    pred2 = SWEBenchPrediction(
        instance_id="task-2",
        model_name_or_path="my-agent-pro",
        model_patch="",  # empty patch
    )

    harness = SWEBenchHarness()
    report = harness.evaluate_batch(
        instances=[inst1, inst2],
        predictions=[pred1, pred2],
        test_results_map={"task-1": {"test_1": True}},
        duration_seconds=25.0,
        total_cost_usd=0.08,
    )

    assert report.total_instances == 2
    assert report.resolved_instances == 1
    assert report.pass_rate == 0.5
    assert report.empty_patch_count == 1
    assert len(report.results) == 2

    # Verify to_dict on result and report
    res_dict = report.results[0].to_dict()
    assert res_dict["instance_id"] == "task-1"
    assert res_dict["resolved"] is True

    rep_dict = report.to_dict()
    assert rep_dict["total_instances"] == 2
    assert rep_dict["resolved_instances"] == 1
    assert rep_dict["pass_rate"] == 0.5

    # Verify export format for SWE-bench submission
    with tempfile.TemporaryDirectory() as tmpdir:
        export_file = Path(tmpdir) / "predictions.json"
        report.export_predictions_json(export_file)

        exported = json.loads(export_file.read_text(encoding="utf-8"))
        assert "task-1" in exported
        assert exported["task-1"]["model_name_or_path"] == "my-agent-pro"
        assert exported["task-1"]["model_patch"] == "diff --git a/a.py ..."
        assert "task-2" in exported


def test_swebench_harness_handles_error_status():
    inst = SWEBenchInstance(
        instance_id="err-task",
        repo="r/err",
        base_commit="c0",
        problem_statement="Problem",
    )

    class ErrorEvaluator(SWEBenchEvaluator):
        def evaluate(self, instance, prediction, test_results=None, duration_seconds=0.0, cost_usd=0.0):
            return SWEBenchEvalResult(
                instance_id=instance.instance_id,
                status=SWEBenchEvalStatus.ERROR,
                resolved=False,
                log="Docker container execution error",
            )

    harness = SWEBenchHarness(evaluator=ErrorEvaluator())
    report = harness.evaluate_batch(instances=[inst], predictions=[])
    assert report.error_count == 1
    assert report.resolved_instances == 0
    assert report.pass_rate == 0.0

