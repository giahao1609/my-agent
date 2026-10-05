from __future__ import annotations

import asyncio
import inspect
import time
import tracemalloc
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .handoff_contracts import BenchmarkMetric, PerformanceReviewResult


class FunctionProfiler:
    """Measures precise function execution latency, memory delta, throughput, and SLO compliance."""

    @staticmethod
    def profile_sync(
        func: Callable[..., Any],
        *args: Any,
        iterations: int = 100,
        slo_threshold_ms: float | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> BenchmarkMetric:
        metric_name = name or getattr(func, "__name__", "anonymous_func")
        durations_ms: list[float] = []

        tracemalloc.start()
        start_mem = tracemalloc.get_traced_memory()[0]

        start_total = time.perf_counter()
        for _ in range(iterations):
            t0 = time.perf_counter()
            func(*args, **kwargs)
            t1 = time.perf_counter()
            durations_ms.append((t1 - t0) * 1000.0)
        end_total = time.perf_counter()

        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        total_ms = (end_total - start_total) * 1000.0
        avg_ms = sum(durations_ms) / len(durations_ms) if durations_ms else 0.0
        min_ms = min(durations_ms) if durations_ms else 0.0
        max_ms = max(durations_ms) if durations_ms else 0.0
        ops_sec = (iterations / (total_ms / 1000.0)) if total_ms > 0 else 0.0
        memory_used = max(0, peak_mem - start_mem)

        passed_slo = True
        if slo_threshold_ms is not None:
            passed_slo = avg_ms <= slo_threshold_ms

        return BenchmarkMetric(
            name=metric_name,
            iterations=iterations,
            total_duration_ms=round(total_ms, 3),
            avg_duration_ms=round(avg_ms, 4),
            min_duration_ms=round(min_ms, 4),
            max_duration_ms=round(max_ms, 4),
            ops_per_sec=round(ops_sec, 1),
            memory_bytes=memory_used,
            passed_slo=passed_slo,
            slo_threshold_ms=slo_threshold_ms,
        )

    @staticmethod
    async def profile_async(
        func: Callable[..., Any],
        *args: Any,
        iterations: int = 100,
        slo_threshold_ms: float | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> BenchmarkMetric:
        metric_name = name or getattr(func, "__name__", "anonymous_async_func")
        durations_ms: list[float] = []

        tracemalloc.start()
        start_mem = tracemalloc.get_traced_memory()[0]

        start_total = time.perf_counter()
        for _ in range(iterations):
            t0 = time.perf_counter()
            res = func(*args, **kwargs)
            if inspect.isawaitable(res):
                await res
            t1 = time.perf_counter()
            durations_ms.append((t1 - t0) * 1000.0)
        end_total = time.perf_counter()

        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        total_ms = (end_total - start_total) * 1000.0
        avg_ms = sum(durations_ms) / len(durations_ms) if durations_ms else 0.0
        min_ms = min(durations_ms) if durations_ms else 0.0
        max_ms = max(durations_ms) if durations_ms else 0.0
        ops_sec = (iterations / (total_ms / 1000.0)) if total_ms > 0 else 0.0
        memory_used = max(0, peak_mem - start_mem)

        passed_slo = True
        if slo_threshold_ms is not None:
            passed_slo = avg_ms <= slo_threshold_ms

        return BenchmarkMetric(
            name=metric_name,
            iterations=iterations,
            total_duration_ms=round(total_ms, 3),
            avg_duration_ms=round(avg_ms, 4),
            min_duration_ms=round(min_ms, 4),
            max_duration_ms=round(max_ms, 4),
            ops_per_sec=round(ops_sec, 1),
            memory_bytes=memory_used,
            passed_slo=passed_slo,
            slo_threshold_ms=slo_threshold_ms,
        )

    @classmethod
    def profile_code(
        cls,
        code_str: str,
        iterations: int = 100,
        slo_threshold_ms: float | None = None,
        name: str = "code_snippet",
    ) -> BenchmarkMetric:
        compiled = compile(code_str, f"<{name}>", "exec")
        scope: dict[str, Any] = {}

        def runner() -> None:
            exec(compiled, scope)

        return cls.profile_sync(
            runner,
            iterations=iterations,
            slo_threshold_ms=slo_threshold_ms,
            name=name,
        )


class BenchmarkRunnerAdapter:
    """Discovers and executes benchmark tests or micro-benchmarks across workspace files."""

    def detect(self, workspace_path: Path) -> bool:
        root = Path(workspace_path)
        if not root.exists():
            return False

        # Look for benchmark directories or test files with 'bench'
        for p in root.rglob("*"):
            if any(part in p.parts for part in (".venv", "node_modules", ".git", "__pycache__")):
                continue
            if p.is_file() and ("bench" in p.name.lower() or p.name.startswith("perf_")):
                return True
        return False

    async def run_benchmarks(
        self,
        workspace_path: Path | str,
        step_id: str = "manual",
        max_latency_ms: float = 100.0,
    ) -> PerformanceReviewResult:
        root = Path(workspace_path)
        metrics: list[BenchmarkMetric] = []
        violations: list[str] = []

        bench_files: list[Path] = []
        for p in root.rglob("*"):
            if any(part in p.parts for part in (".venv", "node_modules", ".git", "__pycache__")):
                continue
            if p.is_file() and p.suffix == ".py" and ("bench" in p.name.lower() or p.name.startswith("perf_")):
                bench_files.append(p)

        # Profile discovered benchmark files
        for bf in bench_files:
            try:
                rel = str(bf.relative_to(root))
            except ValueError:
                rel = str(bf)

            try:
                content = bf.read_text(encoding="utf-8", errors="replace")
                metric = FunctionProfiler.profile_code(
                    content,
                    iterations=5,
                    slo_threshold_ms=max_latency_ms,
                    name=rel,
                )
                metrics.append(metric)
                if not metric.passed_slo:
                    violations.append(
                        f"SLO breached in '{rel}': avg duration {metric.avg_duration_ms}ms > threshold {max_latency_ms}ms"
                    )
            except Exception as e:
                violations.append(f"Failed to benchmark '{rel}': {e}")

        passed_gate = len(violations) == 0
        summary = (
            f"Performance benchmark completed: {len(metrics)} benchmarks passed SLO (<{max_latency_ms}ms)."
            if passed_gate
            else f"Performance gate FAILED: {len(violations)} SLO violations or benchmark failures detected."
        )

        return PerformanceReviewResult(
            step_id=step_id,
            passed_gate=passed_gate,
            summary=summary,
            metrics=tuple(metrics),
            slo_violations=tuple(violations),
        )


class PerformanceGateCoordinator:
    """Evaluates performance metrics against SLA/SLO thresholds and gates step completion."""

    def __init__(self, default_slo_ms: float = 100.0) -> None:
        self.default_slo_ms = default_slo_ms
        self.profiler = FunctionProfiler()
        self.runner = BenchmarkRunnerAdapter()

    def evaluate_metrics(
        self,
        step_id: str,
        metrics: Sequence[BenchmarkMetric],
        max_latency_ms: float | None = None,
    ) -> PerformanceReviewResult:
        threshold = max_latency_ms if max_latency_ms is not None else self.default_slo_ms
        violations: list[str] = []
        final_metrics: list[BenchmarkMetric] = []

        for m in metrics:
            passed = m.avg_duration_ms <= threshold
            if not passed:
                violations.append(
                    f"Function '{m.name}' exceeded latency SLO: {m.avg_duration_ms}ms > {threshold}ms"
                )
            final_metrics.append(
                BenchmarkMetric(
                    name=m.name,
                    iterations=m.iterations,
                    total_duration_ms=m.total_duration_ms,
                    avg_duration_ms=m.avg_duration_ms,
                    min_duration_ms=m.min_duration_ms,
                    max_duration_ms=m.max_duration_ms,
                    ops_per_sec=m.ops_per_sec,
                    memory_bytes=m.memory_bytes,
                    passed_slo=passed,
                    slo_threshold_ms=threshold,
                )
            )

        passed_gate = len(violations) == 0
        summary = (
            f"Performance gate PASSED: {len(final_metrics)} functions within {threshold}ms SLO."
            if passed_gate
            else f"Performance gate FAILED: {len(violations)} functions breached {threshold}ms SLO."
        )

        return PerformanceReviewResult(
            step_id=step_id,
            passed_gate=passed_gate,
            summary=summary,
            metrics=tuple(final_metrics),
            slo_violations=tuple(violations),
        )
