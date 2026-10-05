from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .code_graph import CodeEdge, CodeNode


@dataclass(frozen=True, slots=True)
class ScanResult:
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]


class LanguageScanner(Protocol):
    name: str

    def supports(self, path: Path) -> bool: ...

    def scan_project(
        self,
        project_id: str,
        root: Path,
        *,
        include_paths: set[str] | None = None,
        seed_symbols: dict[str, str] | None = None,
        overlay_files: dict[str, str] | None = None,
    ) -> ScanResult: ...


class ScannerRegistry:
    def __init__(self) -> None:
        self._scanners: list[LanguageScanner] = []

    def register(self, scanner: LanguageScanner) -> None:
        if any(item.name == scanner.name for item in self._scanners):
            raise ValueError(f"scanner already registered: {scanner.name}")
        self._scanners.append(scanner)

    def scanner_for(self, path: Path) -> LanguageScanner | None:
        path = Path(path)
        for scanner in self._scanners:
            if scanner.supports(path):
                return scanner
        return None

    def supports(self, path: Path) -> bool:
        return self.scanner_for(path) is not None

    @property
    def scanners(self) -> tuple[LanguageScanner, ...]:
        return tuple(self._scanners)
