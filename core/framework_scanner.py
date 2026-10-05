from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .language_scanner import ScanResult


class FrameworkScanner(Protocol):
    name: str

    def supports(self, root: Path, path: Path, language: str | None) -> bool: ...

    def scan_file(
        self,
        project_id: str,
        root: Path,
        path: Path,
        source: str,
        language: str | None,
    ) -> ScanResult: ...


class FrameworkScannerRegistry:
    def __init__(self) -> None:
        self._scanners: list[FrameworkScanner] = []

    def register(self, scanner: FrameworkScanner) -> None:
        if any(item.name == scanner.name for item in self._scanners):
            raise ValueError(f"framework scanner already registered: {scanner.name}")
        self._scanners.append(scanner)

    def scanners_for(self, root: Path, path: Path, language: str | None) -> tuple[FrameworkScanner, ...]:
        return tuple(s for s in self._scanners if s.supports(root, path, language))

    @property
    def scanners(self) -> tuple[FrameworkScanner, ...]:
        return tuple(self._scanners)
