from __future__ import annotations

from pathlib import Path
import pytest

from core.repo_map import RepoMapCompactor


def test_compute_pagerank_empty_and_single() -> None:
    # Empty graph
    assert RepoMapCompactor.compute_pagerank({}) == {}

    # Single node
    res = RepoMapCompactor.compute_pagerank({"a.py": set()})
    assert res == {"a.py": 1.0}


def test_compute_pagerank_centrality() -> None:
    # Star topology: b, c, d all point to / depend on a.py
    # Therefore, a.py should have the highest PageRank score
    graph = {
        "b.py": {"a.py"},
        "c.py": {"a.py"},
        "d.py": {"a.py"},
        "a.py": set(),
    }
    ranks = RepoMapCompactor.compute_pagerank(graph, damping=0.85)
    assert len(ranks) == 4
    assert ranks["a.py"] > ranks["b.py"]
    assert ranks["a.py"] > ranks["c.py"]
    assert ranks["a.py"] > ranks["d.py"]
    assert pytest.approx(ranks["b.py"]) == ranks["c.py"]
    assert pytest.approx(sum(ranks.values()), abs=1e-3) == 1.0


def test_generate_repo_map_with_pagerank(tmp_path: Path) -> None:
    # Create files in workspace
    # core_service.py defines CoreService
    core_file = tmp_path / "core_service.py"
    core_file.write_text(
        "class CoreService:\n"
        "    def run(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    # client1.py uses CoreService
    client1 = tmp_path / "client1.py"
    client1.write_text(
        "from core_service import CoreService\n"
        "def use_service():\n"
        "    s = CoreService()\n"
        "    s.run()\n",
        encoding="utf-8",
    )

    # client2.py also uses CoreService
    client2 = tmp_path / "client2.py"
    client2.write_text(
        "from core_service import CoreService\n"
        "def another_client():\n"
        "    return CoreService()\n",
        encoding="utf-8",
    )

    summary = RepoMapCompactor.generate_repo_map(tmp_path, max_tokens=1000)
    assert summary.files_indexed == 3
    assert "### core_service.py" in summary.repo_map
    assert "class CoreService" in summary.repo_map

    # core_service.py should have highest centrality or appear first
    lines = [l for l in summary.repo_map.splitlines() if l.startswith("### ")]
    assert "core_service.py" in lines[0]
