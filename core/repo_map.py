from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RepoMapSummary:
    repo_map: str
    token_count_estimate: int
    files_indexed: int
    symbols_indexed: int

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_map": self.repo_map,
            "token_count_estimate": self.token_count_estimate,
            "files_indexed": self.files_indexed,
            "symbols_indexed": self.symbols_indexed,
        }


class RepoMapCompactor:
    """Extracts code architecture outlines and compresses them into a token-budgeted repository map."""

    IGNORE_DIRS = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
        ".gemini",
        "scratch",
        ".agents",
    }

    IGNORE_EXTENSIONS = {
        ".pyc",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".log",
        ".lock",
        ".bak",
    }

    @classmethod
    def _extract_python_symbols(cls, file_content: str) -> list[str]:
        symbols: list[str] = []
        try:
            tree = ast.parse(file_content)
        except Exception:
            return symbols

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append(f"  class {node.name}")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in sub.args.args if a.arg != "self"]
                        args_str = ", ".join(args[:4])
                        symbols.append(f"    def {sub.name}({args_str})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                args_str = ", ".join(args[:5])
                symbols.append(f"  def {node.name}({args_str})")

        return symbols

    @classmethod
    def _extract_python_symbols_and_deps(
        cls, file_content: str
    ) -> tuple[list[str], set[str], set[str], set[str]]:
        """Extracts class/function signatures, defined names, referenced names, and imported modules."""
        symbols: list[str] = []
        defined_names: set[str] = set()
        referenced_names: set[str] = set()
        imported_modules: set[str] = set()

        try:
            tree = ast.parse(file_content)
        except Exception:
            return symbols, defined_names, referenced_names, imported_modules

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)
                for alias in node.names:
                    referenced_names.add(alias.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                referenced_names.add(node.id)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                defined_names.add(node.name)
                symbols.append(f"  class {node.name}")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined_names.add(sub.name)
                        args = [a.arg for a in sub.args.args if a.arg != "self"]
                        args_str = ", ".join(args[:4])
                        symbols.append(f"    def {sub.name}({args_str})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined_names.add(node.name)
                args = [a.arg for a in node.args.args]
                args_str = ", ".join(args[:5])
                symbols.append(f"  def {node.name}({args_str})")

        return symbols, defined_names, referenced_names, imported_modules

    @classmethod
    def _extract_generic_symbols(cls, file_content: str) -> list[str]:
        symbols: list[str] = []
        for line in file_content.splitlines():
            line_s = line.strip()
            # Match function/class declarations in JS/TS/Go
            if re.match(r"^(export\s+)?(class|interface|type|enum)\s+([A-Za-z0-9_]+)", line_s):
                symbols.append(f"  {line_s[:60]}")
            elif re.match(r"^(export\s+)?(async\s+)?function\s+([A-Za-z0-9_]+)", line_s):
                symbols.append(f"  {line_s[:60]}")
            elif re.match(r"^func\s+([A-Za-z0-9_]+)", line_s):
                symbols.append(f"  {line_s[:60]}")
        return symbols[:20]

    @classmethod
    def compute_pagerank(
        cls,
        graph: dict[str, set[str]],
        damping: float = 0.85,
        max_iter: int = 30,
        tol: float = 1e-4,
    ) -> dict[str, float]:
        """Computes PageRank centrality over a directed graph (pure Python power iteration).
        
        Args:
            graph: Mapping of node -> set of out-neighbors (nodes this node depends on).
            damping: Damping factor (default 0.85).
            max_iter: Maximum iterations.
            tol: Convergence tolerance.
        """
        nodes = list(graph.keys())
        n = len(nodes)
        if n == 0:
            return {}
        if n == 1:
            return {nodes[0]: 1.0}

        # Initial uniform distribution
        ranks = {node: 1.0 / n for node in nodes}

        # Build in-neighbors map for fast iteration
        in_neighbors: dict[str, set[str]] = {node: set() for node in nodes}
        out_degrees: dict[str, int] = {}
        for u, neighbors in graph.items():
            out_degrees[u] = len(neighbors)
            for v in neighbors:
                if v in in_neighbors:
                    in_neighbors[v].add(u)

        base_score = (1.0 - damping) / n

        for _ in range(max_iter):
            dangling_mass = sum(ranks[u] for u in nodes if out_degrees.get(u, 0) == 0)
            dangling_contribution = damping * (dangling_mass / n)

            new_ranks: dict[str, float] = {}
            total_diff = 0.0

            for v in nodes:
                incoming_sum = sum(
                    ranks[u] / out_degrees[u]
                    for u in in_neighbors[v]
                    if out_degrees[u] > 0
                )
                new_rank = base_score + dangling_contribution + damping * incoming_sum
                total_diff += abs(new_rank - ranks[v])
                new_ranks[v] = new_rank

            ranks = new_ranks
            if total_diff < tol:
                break

        return ranks

    @classmethod
    def generate_repo_map(
        cls,
        workspace_path: Path | str,
        max_tokens: int = 1500,
    ) -> RepoMapSummary:
        root = Path(workspace_path)
        if not root.exists():
            return RepoMapSummary(repo_map="", token_count_estimate=0, files_indexed=0, symbols_indexed=0)

        # Discover relevant source files
        candidate_files: list[Path] = []
        for p in root.rglob("*"):
            if any(part in p.parts for part in cls.IGNORE_DIRS):
                continue
            if p.is_file() and p.suffix not in cls.IGNORE_EXTENSIONS and p.stat().st_size < 200_000:
                candidate_files.append(p)

        file_outlines: dict[str, list[str]] = {}
        file_definitions: dict[str, set[str]] = {}
        file_references: dict[str, set[str]] = {}
        file_imports: dict[str, set[str]] = {}
        total_symbols = 0

        # Step 1: Parse AST and symbols for candidate files
        for cf in candidate_files:
            try:
                rel_path = str(cf.relative_to(root))
            except ValueError:
                rel_path = str(cf)

            try:
                content = cf.read_text(encoding="utf-8", errors="replace")
                if cf.suffix == ".py":
                    syms, defs, refs, imps = cls._extract_python_symbols_and_deps(content)
                    file_outlines[rel_path] = syms
                    file_definitions[rel_path] = defs
                    file_references[rel_path] = refs
                    file_imports[rel_path] = imps
                    total_symbols += len(syms)
                elif cf.suffix in (".js", ".ts", ".jsx", ".tsx", ".go"):
                    syms = cls._extract_generic_symbols(content)
                    if syms:
                        file_outlines[rel_path] = syms
                        total_symbols += len(syms)
            except Exception:
                continue

        # Step 2: Build dependency graph for PageRank
        graph: dict[str, set[str]] = {p: set() for p in file_outlines}
        for u_path, u_refs in file_references.items():
            u_imps = file_imports.get(u_path, set())
            for v_path, v_defs in file_definitions.items():
                if u_path == v_path:
                    continue
                # Check symbol intersection
                if u_refs & v_defs:
                    graph[u_path].add(v_path)
                    continue
                # Check module import match
                v_mod = v_path.replace("/", ".").replace(".py", "")
                if any(v_mod.endswith(imp) or imp.endswith(v_mod.split(".")[-1]) for imp in u_imps):
                    graph[u_path].add(v_path)

        # Step 3: Compute PageRank scores
        pagerank_scores = cls.compute_pagerank(graph)

        # Step 4: Sort files by PageRank score descending, with directory heuristic as tiebreaker
        def ranking_key(item: tuple[str, list[str]]) -> tuple[float, int, int]:
            path_str = item[0]
            pr_score = pagerank_scores.get(path_str, 0.0)
            heuristic_bonus = 0
            if "core" in path_str:
                heuristic_bonus += 2
            if "agent" in path_str:
                heuristic_bonus += 1
            if "test" in path_str:
                heuristic_bonus -= 3
            return (pr_score, heuristic_bonus, -len(Path(path_str).parts))

        sorted_files = sorted(file_outlines.items(), key=ranking_key, reverse=True)

        # Step 5: Build compressed repo map within max_tokens budget
        max_chars = max(100, max_tokens * 4)
        trunc_msg = "\n... [Remaining files truncated to fit token budget]"
        lines: list[str] = ["# Repository Architecture Map (Compressed)"]
        current_chars = len(lines[0])
        files_count = 0
        truncated = False

        for path_str, symbols in sorted_files:
            if not symbols:
                continue
            pr = pagerank_scores.get(path_str, 0.0)
            header = f"\n### {path_str} (centrality: {pr:.3f})"
            if current_chars + len(header) + len(trunc_msg) > max_chars:
                lines.append(trunc_msg)
                truncated = True
                break

            lines.append(header)
            current_chars += len(header)
            files_count += 1

            for sym in symbols:
                if current_chars + len(sym) + 1 + len(trunc_msg) > max_chars:
                    lines.append("  ...")
                    truncated = True
                    break
                lines.append(sym)
                current_chars += len(sym) + 1

            if truncated:
                break

        repo_map_text = "\n".join(lines)
        token_estimate = len(repo_map_text) // 4

        return RepoMapSummary(
            repo_map=repo_map_text,
            token_count_estimate=token_estimate,
            files_indexed=files_count,
            symbols_indexed=total_symbols,
        )

