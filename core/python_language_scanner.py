from __future__ import annotations

from pathlib import Path

from .language_scanner import ScanResult
from .python_code_scanner import scan_python_project


class PythonLanguageScanner:
    name = "python"

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() == ".py"

    def scan_project(
        self,
        project_id: str,
        root: Path,
        *,
        include_paths: set[str] | None = None,
        seed_symbols: dict[str, str] | None = None,
        overlay_files: dict[str, str] | None = None,
    ) -> ScanResult:
        result = scan_python_project(
            project_id,
            root,
            include_paths=include_paths,
            seed_symbols=seed_symbols,
            overlay_files=overlay_files,
        )
        return ScanResult(nodes=result.nodes, edges=result.edges)
