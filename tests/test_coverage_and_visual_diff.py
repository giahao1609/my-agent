from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path
import pytest

from core.coverage_gate import CoverageGateCoordinator
from core.handoff_contracts import TestResult
from core.test_runner import PytestRunnerAdapter, NpmTestRunnerAdapter, GoTestRunnerAdapter
from core.visual_diff import VisualDiffValidator
from my_agent_mcp import server


def _make_test_png(w: int, h: int, r: int, g: int, b: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = binascii.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    raw_scanlines = b"".join(b"\x00" + bytes([r, g, b]) * w for _ in range(h))
    idat = chunk(b"IDAT", zlib.compress(raw_scanlines))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def test_coverage_gate_coordinator() -> None:
    coordinator = CoverageGateCoordinator(default_threshold=80.0)

    # 1. High coverage -> pass
    res_high = TestResult(
        step_id="step-1",
        total_tests=10,
        passed_tests=10,
        failed_tests=0,
        coverage_percentage=85.5,
    )
    eval_pass = coordinator.evaluate_coverage(res_high)
    assert eval_pass.passed_gate is True
    assert eval_pass.coverage_percentage == 85.5
    assert "PASSED" in eval_pass.summary

    # 2. Low coverage -> fail
    res_low = TestResult(
        step_id="step-2",
        total_tests=10,
        passed_tests=10,
        failed_tests=0,
        coverage_percentage=65.0,
    )
    eval_fail = coordinator.evaluate_coverage(res_low)
    assert eval_fail.passed_gate is False
    assert "FAILED" in eval_fail.summary

    # 3. No coverage data -> fail
    res_none = TestResult(
        step_id="step-3",
        total_tests=5,
        passed_tests=5,
        failed_tests=0,
        coverage_percentage=None,
    )
    eval_none = coordinator.evaluate_coverage(res_none)
    assert eval_none.passed_gate is False


def test_test_runner_coverage_parsing() -> None:
    # Pytest coverage output
    pytest_out = (
        "Name                      Stmts   Miss  Cover\n"
        "---------------------------------------------\n"
        "core/app.py                  50      5    90%\n"
        "TOTAL                        50      5    90%\n"
        "================ 10 passed in 1.20s ================\n"
    )
    res_pytest = PytestRunnerAdapter._parse_pytest_output("s1", pytest_out, 0)
    assert res_pytest.coverage_percentage == 90.0

    # Npm coverage output
    npm_out = (
        "PASS tests/app.test.js\n"
        "All files | 87.5 | 80 | 100 | 87.5\n"
        "Tests: 4 passed, 4 total\n"
    )
    res_npm = NpmTestRunnerAdapter._parse_npm_output("s2", npm_out, 0)
    assert res_npm.coverage_percentage == 87.5

    # Go coverage output
    go_out = (
        "=== RUN   TestHello\n"
        "--- PASS: TestHello (0.00s)\n"
        "PASS\n"
        "coverage: 92.4% of statements\n"
        "ok      example.com/mod 0.012s\n"
    )
    res_go = GoTestRunnerAdapter._parse_go_output("s3", go_out, 0)
    assert res_go.coverage_percentage == 92.4


def test_visual_diff_identical_images(tmp_path: Path) -> None:
    img1 = tmp_path / "base.png"
    img2 = tmp_path / "curr.png"

    raw = _make_test_png(10, 10, 100, 150, 200)
    img1.write_bytes(raw)
    img2.write_bytes(raw)

    res = VisualDiffValidator.compare_images(img1, img2, tolerance_percentage=0.5)
    assert res.passed is True
    assert res.diff_percentage == 0.0
    assert "PASSED" in res.summary


def test_visual_diff_differing_images(tmp_path: Path) -> None:
    img1 = tmp_path / "red.png"
    img2 = tmp_path / "blue.png"

    img1.write_bytes(_make_test_png(10, 10, 255, 0, 0))
    img2.write_bytes(_make_test_png(10, 10, 0, 0, 255))

    # All pixels differ (100% difference)
    res = VisualDiffValidator.compare_images(img1, img2, tolerance_percentage=1.0)
    assert res.passed is False
    assert res.diff_percentage >= 99.0
    assert "FAILED" in res.summary


def test_visual_diff_missing_files() -> None:
    res = VisualDiffValidator.compare_images("non_existent_1.png", "non_existent_2.png")
    assert res.passed is False
    assert "failed" in res.summary.lower()


@pytest.mark.asyncio
async def test_mcp_coverage_and_visual_tools(tmp_path: Path) -> None:
    # 1. compare_visual_diff tool
    img1 = tmp_path / "v1.png"
    img2 = tmp_path / "v2.png"
    raw = _make_test_png(4, 4, 50, 50, 50)
    img1.write_bytes(raw)
    img2.write_bytes(raw)

    vis_res = await server.compare_visual_diff(str(img1), str(img2), tolerance_percent=0.5)
    assert vis_res["passed"] is True
    assert vis_res["diff_percentage"] == 0.0

    # 2. verify_coverage tool
    # Create empty workspace with a simple test
    (tmp_path / "test_app.py").write_text("def test_ok(): assert 1 == 1\n", encoding="utf-8")
    cov_res = await server.verify_coverage(str(tmp_path), step_id="cov-1", min_coverage=80.0)
    assert "passed_gate" in cov_res
    assert "summary" in cov_res
