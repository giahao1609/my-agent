from __future__ import annotations

import asyncio
import time
from pathlib import Path
import pytest

from core.performance_profiler import (
    BenchmarkRunnerAdapter,
    FunctionProfiler,
    PerformanceGateCoordinator,
)
from my_agent_mcp import server


def test_function_profiler_sync() -> None:
    def sample_work(n: int) -> int:
        return sum(i * i for i in range(n))

    metric = FunctionProfiler.profile_sync(
        sample_work,
        1000,
        iterations=50,
        slo_threshold_ms=10.0,
        name="sample_work",
    )

    assert metric.name == "sample_work"
    assert metric.iterations == 50
    assert metric.total_duration_ms > 0
    assert metric.avg_duration_ms >= 0
    assert metric.ops_per_sec > 0
    assert metric.passed_slo is True


@pytest.mark.asyncio
async def test_function_profiler_async() -> None:
    async def async_work() -> str:
        await asyncio.sleep(0.001)
        return "done"

    metric = await FunctionProfiler.profile_async(
        async_work,
        iterations=5,
        slo_threshold_ms=20.0,
        name="async_work",
    )

    assert metric.name == "async_work"
    assert metric.iterations == 5
    assert metric.avg_duration_ms >= 1.0  # at least ~1ms per call
    assert metric.passed_slo is True


def test_function_profiler_slo_breach() -> None:
    def slow_func() -> None:
        time.sleep(0.005)  # 5ms

    # SLO threshold is 1ms -> should fail SLO
    metric = FunctionProfiler.profile_sync(
        slow_func,
        iterations=3,
        slo_threshold_ms=1.0,
        name="slow_func",
    )
    assert metric.passed_slo is False


def test_function_profiler_code_snippet() -> None:
    snippet = "total = sum(x for x in range(500))"
    metric = FunctionProfiler.profile_code(snippet, iterations=20, slo_threshold_ms=5.0)
    assert metric.iterations == 20
    assert metric.passed_slo is True


def test_performance_gate_coordinator() -> None:
    coordinator = PerformanceGateCoordinator(default_slo_ms=5.0)

    fast = FunctionProfiler.profile_code("x = 1 + 1", iterations=10)
    slow = FunctionProfiler.profile_code("import time; time.sleep(0.01)", iterations=2)

    # Pass case
    res_pass = coordinator.evaluate_metrics("step-1", [fast], max_latency_ms=10.0)
    assert res_pass.passed_gate is True
    assert len(res_pass.slo_violations) == 0

    # Fail case
    res_fail = coordinator.evaluate_metrics("step-2", [slow], max_latency_ms=5.0)
    assert res_fail.passed_gate is False
    assert len(res_fail.slo_violations) == 1


@pytest.mark.asyncio
async def test_benchmark_runner_adapter(tmp_path: Path) -> None:
    adapter = BenchmarkRunnerAdapter()
    assert adapter.detect(tmp_path) is False

    # Create a benchmark file
    bench_file = tmp_path / "test_bench_math.py"
    bench_file.write_text("res = [i**2 for i in range(100)]\n", encoding="utf-8")

    assert adapter.detect(tmp_path) is True

    result = await adapter.run_benchmarks(tmp_path, step_id="bench-step-1", max_latency_ms=50.0)
    assert result.passed_gate is True
    assert len(result.metrics) == 1
    assert "test_bench_math.py" in result.metrics[0].name


@pytest.mark.asyncio
async def test_mcp_performance_tools(tmp_path: Path) -> None:
    # 1. profile_code tool
    prof_res = await server.profile_code("a = [i for i in range(100)]", iterations=10)
    assert "avg_duration_ms" in prof_res
    assert prof_res["passed_slo"] is True

    # 2. run_benchmark tool
    (tmp_path / "perf_calc.py").write_text("v = sum(range(50))\n", encoding="utf-8")
    bench_res = await server.run_benchmark(str(tmp_path), step_id="mcp-bench-1", max_latency_ms=50.0)
    assert bench_res["passed_gate"] is True
    assert len(bench_res["metrics"]) == 1
