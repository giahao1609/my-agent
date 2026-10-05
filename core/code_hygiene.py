from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from .handoff_contracts import CodeHygieneFinding, CodeHygieneReviewResult


class CircularDependencyDetector:
    """Detects cyclic import dependencies between modules using 3-color DFS cycle detection."""

    IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}

    @classmethod
    def _extract_imports(cls, file_path: Path, root: Path) -> list[str]:
        imported_modules: list[str] = []
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return imported_modules

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.append(node.module)

        return imported_modules

    @classmethod
    def detect_cycles(cls, workspace_path: Path | str) -> list[tuple[str, ...]]:
        root = Path(workspace_path)
        if not root.exists():
            return []

        # Map file paths to module names and vice versa
        py_files: list[Path] = []
        for p in root.rglob("*.py"):
            if any(part in p.parts for part in cls.IGNORE_DIRS):
                continue
            py_files.append(p)

        # Build module graph: module_name -> set of imported module_names
        graph: dict[str, set[str]] = defaultdict(set)
        mod_to_rel: dict[str, str] = {}

        for pf in py_files:
            try:
                rel = str(pf.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(pf).replace("\\", "/")

            # Derive python module identifier (e.g. core.app or app)
            mod_name = rel.removesuffix(".py").replace("/", ".")
            if mod_name.endswith(".__init__"):
                mod_name = mod_name.removesuffix(".__init__")
            mod_to_rel[mod_name] = rel

            imports = cls._extract_imports(pf, root)
            for imp in imports:
                graph[mod_name].add(imp)

        # Filter graph to only internal workspace edges
        internal_modules = set(mod_to_rel.keys())
        clean_graph: dict[str, set[str]] = defaultdict(set)

        for src, targets in graph.items():
            for tgt in targets:
                # Direct match or package prefix match
                matched_target = None
                if tgt in internal_modules:
                    matched_target = tgt
                else:
                    # e.g., if tgt is "core.app.helper", match "core.app"
                    parts = tgt.split(".")
                    for i in range(len(parts) - 1, 0, -1):
                        prefix = ".".join(parts[:i])
                        if prefix in internal_modules:
                            matched_target = prefix
                            break

                if matched_target and matched_target != src:
                    clean_graph[src].add(matched_target)

        # DFS 3-color cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        colors: dict[str, int] = {m: WHITE for m in internal_modules}
        detected_cycles: list[tuple[str, ...]] = []
        visited_cycles: set[frozenset[str]] = set()

        def dfs(node: str, path: list[str]) -> None:
            colors[node] = GRAY
            path.append(node)

            for neighbor in clean_graph.get(node, ()):
                if colors.get(neighbor) == GRAY:
                    # Cycle found
                    cycle_start_idx = path.index(neighbor)
                    cycle = path[cycle_start_idx:]
                    cycle_frozen = frozenset(cycle)
                    if len(cycle) >= 2 and cycle_frozen not in visited_cycles:
                        visited_cycles.add(cycle_frozen)
                        # Format as clean path strings
                        formatted_cycle = tuple(mod_to_rel.get(m, m) for m in cycle) + (mod_to_rel.get(neighbor, neighbor),)
                        detected_cycles.append(formatted_cycle)
                elif colors.get(neighbor, WHITE) == WHITE:
                    dfs(neighbor, path)

            path.pop()
            colors[node] = BLACK

        for m in sorted(internal_modules):
            if colors.get(m) == WHITE:
                dfs(m, [])

        return detected_cycles


class DeadCodeDetector:
    """Scans workspace to identify unused (dead) functions, methods, and classes."""

    IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
    ENTRYPOINT_NAMES = {
        "main",
        "run",
        "start",
        "setup",
        "cli",
        "app",
        "handler",
        "create_app",
        "execute",
    }

    @classmethod
    def detect_dead_code(cls, workspace_path: Path | str) -> list[tuple[str, str, int]]:
        """Returns list of (file_rel_path, symbol_name, line_number) for unreferenced symbols."""
        root = Path(workspace_path)
        if not root.exists():
            return []

        py_files: list[Path] = []
        for p in root.rglob("*.py"):
            if any(part in p.parts for part in cls.IGNORE_DIRS):
                continue
            py_files.append(p)

        definitions: list[tuple[Path, str, int]] = []
        all_file_contents: list[str] = []

        for pf in py_files:
            try:
                content = pf.read_text(encoding="utf-8", errors="replace")
                all_file_contents.append(content)

                # Skip test files from being marked as dead code definitions
                if "test" in pf.name.lower() or "tests" in pf.parts:
                    continue

                tree = ast.parse(content)
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = node.name
                        if (
                            not name.startswith("_")
                            and not name.startswith("test_")
                            and name not in cls.ENTRYPOINT_NAMES
                        ):
                            definitions.append((pf, name, node.lineno))
                    elif isinstance(node, ast.ClassDef):
                        name = node.name
                        if not name.startswith("_") and name not in cls.ENTRYPOINT_NAMES:
                            definitions.append((pf, name, node.lineno))
            except Exception:
                continue

        # Combine all files into a searchable body
        combined_code = "\n".join(all_file_contents)

        dead_symbols: list[tuple[str, str, int]] = []
        for pf, sym_name, lineno in definitions:
            try:
                rel = str(pf.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(pf).replace("\\", "/")

            # Count occurrences across entire workspace
            count = combined_code.count(sym_name)
            # If it only appears 1 time (its own definition line), it's never called anywhere!
            if count <= 1:
                dead_symbols.append((rel, sym_name, lineno))

        return dead_symbols


class CodeHygieneCoordinator:
    """Evaluates workspace code architecture hygiene (circular dependencies & dead code)."""

    @classmethod
    def evaluate(cls, workspace_path: Path | str, step_id: str = "manual") -> CodeHygieneReviewResult:
        root = Path(workspace_path)
        findings: list[CodeHygieneFinding] = []
        cycle_strings: list[str] = []

        # 1. Circular Dependencies
        cycles = CircularDependencyDetector.detect_cycles(root)
        for c in cycles:
            cycle_str = " -> ".join(c)
            cycle_strings.append(cycle_str)
            findings.append(
                CodeHygieneFinding(
                    category="circular_dependency",
                    severity="HIGH",
                    file_path=c[0],
                    symbol_name=Path(c[0]).stem,
                    details=f"Circular import cycle detected: {cycle_str}",
                    suggested_action="Refactor shared types or functions into an independent module or use lazy imports.",
                )
            )

        # 2. Dead Code
        dead_symbols = DeadCodeDetector.detect_dead_code(root)
        for file_path, sym, lineno in dead_symbols:
            findings.append(
                CodeHygieneFinding(
                    category="dead_code",
                    severity="LOW",
                    file_path=file_path,
                    symbol_name=sym,
                    details=f"Symbol '{sym}' at line {lineno} has 0 references across the repository.",
                    suggested_action="Remove unused symbol or export it if intended for public library consumers.",
                )
            )

        # Pass gate if no HIGH severity circular dependencies exist
        passed_gate = len(cycles) == 0
        summary_parts = []
        if cycles:
            summary_parts.append(f"{len(cycles)} circular import cycle(s) detected")
        if dead_symbols:
            summary_parts.append(f"{len(dead_symbols)} unused symbol(s) detected")

        if passed_gate:
            summary = (
                f"Code architecture hygiene PASSED: 0 circular dependencies ({len(dead_symbols)} unused symbols noted)."
                if dead_symbols
                else "Code architecture hygiene PASSED: 0 circular dependencies and 0 dead code issues."
            )
        else:
            summary = f"Code architecture hygiene FAILED: {'; '.join(summary_parts)}."

        return CodeHygieneReviewResult(
            step_id=step_id,
            passed_gate=passed_gate,
            summary=summary,
            cycles=tuple(cycle_strings),
            dead_code_count=len(dead_symbols),
            findings=tuple(findings),
        )
