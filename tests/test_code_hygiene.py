from __future__ import annotations

from pathlib import Path
import pytest

from core.code_hygiene import (
    CircularDependencyDetector,
    CodeHygieneCoordinator,
    DeadCodeDetector,
)
from my_agent_mcp import server


def test_circular_dependency_2_nodes(tmp_path: Path) -> None:
    # a.py imports b, b.py imports a
    (tmp_path / "mod_a.py").write_text("import mod_b\ndef func_a(): pass\n", encoding="utf-8")
    (tmp_path / "mod_b.py").write_text("import mod_a\ndef func_b(): pass\n", encoding="utf-8")

    cycles = CircularDependencyDetector.detect_cycles(tmp_path)
    assert len(cycles) >= 1
    cycle_str = " -> ".join(cycles[0])
    assert "mod_a.py" in cycle_str
    assert "mod_b.py" in cycle_str


def test_circular_dependency_3_nodes(tmp_path: Path) -> None:
    # a -> b -> c -> a
    (tmp_path / "svc_a.py").write_text("import svc_b\n", encoding="utf-8")
    (tmp_path / "svc_b.py").write_text("import svc_c\n", encoding="utf-8")
    (tmp_path / "svc_c.py").write_text("import svc_a\n", encoding="utf-8")

    cycles = CircularDependencyDetector.detect_cycles(tmp_path)
    assert len(cycles) >= 1
    cycle_str = " -> ".join(cycles[0])
    assert "svc_a.py" in cycle_str
    assert "svc_b.py" in cycle_str
    assert "svc_c.py" in cycle_str


def test_circular_dependency_clean(tmp_path: Path) -> None:
    # a -> b -> c (linear, no cycle)
    (tmp_path / "clean_a.py").write_text("import clean_b\n", encoding="utf-8")
    (tmp_path / "clean_b.py").write_text("import clean_c\n", encoding="utf-8")
    (tmp_path / "clean_c.py").write_text("x = 1\n", encoding="utf-8")

    cycles = CircularDependencyDetector.detect_cycles(tmp_path)
    assert len(cycles) == 0


def test_dead_code_detection(tmp_path: Path) -> None:
    # define used_func and unused_orphan_func
    code_helpers = (
        "def used_func():\n    return 42\n\n"
        "def unused_orphan_func():\n    return 0\n"
    )
    (tmp_path / "helpers.py").write_text(code_helpers, encoding="utf-8")

    # main.py calls used_func
    code_main = (
        "from helpers import used_func\n"
        "def main():\n"
        "    return used_func()\n"
    )
    (tmp_path / "main.py").write_text(code_main, encoding="utf-8")

    dead = DeadCodeDetector.detect_dead_code(tmp_path)
    dead_names = [name for _, name, _ in dead]

    assert "unused_orphan_func" in dead_names
    assert "used_func" not in dead_names
    assert "main" not in dead_names  # entrypoint ignored


def test_code_hygiene_coordinator(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("import two\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("import one\n", encoding="utf-8")

    review = CodeHygieneCoordinator.evaluate(tmp_path, step_id="hygiene-1")
    assert review.passed_gate is False
    assert len(review.cycles) >= 1
    assert "FAILED" in review.summary


@pytest.mark.asyncio
async def test_mcp_code_hygiene_tools(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("import y\ndef unused_foo(): pass\n", encoding="utf-8")
    (tmp_path / "y.py").write_text("import x\n", encoding="utf-8")

    # 1. detect_circular_dependencies tool
    circ_res = await server.detect_circular_dependencies(str(tmp_path))
    assert circ_res["passed"] is False
    assert circ_res["cycles_count"] >= 1

    # 2. detect_dead_code tool
    dead_res = await server.detect_dead_code(str(tmp_path))
    assert dead_res["dead_symbols_count"] >= 1
    assert any(s["symbol"] == "unused_foo" for s in dead_res["dead_symbols"])
