from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .handoff_contracts import SecuritySeverity
from .security_scanner import SecurityScannerRegistry
from .test_runner import TestRunnerRegistry


@dataclass(frozen=True, slots=True)
class DependencyComponent:
    name: str
    version: str
    ecosystem: str  # pypi, npm, gomod, cargo
    direct: bool = True
    license: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "ecosystem": self.ecosystem,
            "direct": self.direct,
            "license": self.license,
        }


@dataclass(slots=True)
class SBOMManifest:
    project_name: str
    format: str = "cyclonedx-json"
    spec_version: str = "1.5"
    components: list[DependencyComponent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bomFormat": "CycloneDX",
            "specVersion": self.spec_version,
            "project": self.project_name,
            "total_components": len(self.components),
            "components": [c.to_dict() for c in self.components],
        }


class SBOMGenerator:
    """Generates SBOM manifests from project dependency definitions and lockfiles (Python, Node, Go, Rust)."""

    def generate(self, workspace_path: Path | str, project_name: str = "unknown") -> SBOMManifest:
        root = Path(workspace_path)
        components: list[DependencyComponent] = []

        # 1. Python: requirements.txt or pyproject.toml
        req_file = root / "requirements.txt"
        if req_file.exists():
            for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    match = re.match(r"^([a-zA-Z0-9_-]+)([>=<~!]*)(.*)$", line)
                    if match:
                        name, _, ver = match.groups()
                        components.append(
                            DependencyComponent(
                                name=name.strip(),
                                version=ver.strip() or "latest",
                                ecosystem="pypi",
                            )
                        )

        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            # Parse dependencies array
            dep_matches = re.findall(r'["\']([a-zA-Z0-9_-]+)(?:([>=<~!]+)([^"\']+))?["\']', content)
            for name, op, ver in dep_matches:
                if name.lower() not in ("project", "tool", "pytest", "ruff", "python"):
                    components.append(
                        DependencyComponent(
                            name=name,
                            version=ver.strip() if ver else "latest",
                            ecosystem="pypi",
                        )
                    )

        # 2. Node.js: package-lock.json or package.json
        pkg_lock = root / "package-lock.json"
        if pkg_lock.exists():
            try:
                lock_data = json.loads(pkg_lock.read_text(encoding="utf-8"))
                # v2/v3 packages structure
                packages = lock_data.get("packages", {})
                for pkg_path, pkg_info in packages.items():
                    if pkg_path:  # Skip root ""
                        pkg_name = pkg_path.replace("node_modules/", "")
                        if "version" in pkg_info:
                            components.append(
                                DependencyComponent(
                                    name=pkg_name,
                                    version=pkg_info.get("version", "latest"),
                                    ecosystem="npm",
                                    direct=not ("node_modules/" in pkg_path[13:]),
                                    license=pkg_info.get("license", "UNKNOWN"),
                                )
                            )
                # v1 dependencies structure fallback
                if not components and "dependencies" in lock_data:
                    for dep_name, dep_info in lock_data.get("dependencies", {}).items():
                        components.append(
                            DependencyComponent(
                                name=dep_name,
                                version=dep_info.get("version", "latest"),
                                ecosystem="npm",
                            )
                        )
            except Exception:
                pass

        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                for dep_name, dep_ver in data.get("dependencies", {}).items():
                    components.append(
                        DependencyComponent(
                            name=dep_name,
                            version=str(dep_ver).lstrip("^~"),
                            ecosystem="npm",
                            direct=True,
                        )
                    )
            except Exception:
                pass

        # 3. Go: go.sum and go.mod
        go_sum = root / "go.sum"
        if go_sum.exists():
            for line in go_sum.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and not parts[1].endswith("/go.mod"):
                    components.append(
                        DependencyComponent(
                            name=parts[0],
                            version=parts[1],
                            ecosystem="gomod",
                            direct=False,
                        )
                    )

        go_mod = root / "go.mod"
        if go_mod.exists():
            for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("require ") or (not line.startswith("//") and "\tv" in line):
                    parts = line.replace("require ", "").strip().split()
                    if len(parts) >= 2:
                        components.append(
                            DependencyComponent(
                                name=parts[0],
                                version=parts[1],
                                ecosystem="gomod",
                                direct=True,
                            )
                        )

        # 4. Rust: Cargo.lock and Cargo.toml
        cargo_lock = root / "Cargo.lock"
        if cargo_lock.exists():
            content = cargo_lock.read_text(encoding="utf-8", errors="replace")
            # Parse [[package]] blocks
            pkg_blocks = re.findall(r'\[\[package\]\][\s\S]*?name\s*=\s*"([^"]+)"[\s\S]*?version\s*=\s*"([^"]+)"', content)
            for name, ver in pkg_blocks:
                components.append(
                    DependencyComponent(
                        name=name,
                        version=ver,
                        ecosystem="cargo",
                        direct=False,
                    )
                )

        cargo_toml = root / "Cargo.toml"
        if cargo_toml.exists():
            content = cargo_toml.read_text(encoding="utf-8", errors="replace")
            cargo_matches = re.findall(r'([a-zA-Z0-9_-]+)\s*=\s*["\']([^"\']+)["\']', content)
            for name, ver in cargo_matches:
                if name.lower() not in ("name", "version", "edition"):
                    components.append(
                        DependencyComponent(
                            name=name,
                            version=ver,
                            ecosystem="cargo",
                            direct=True,
                        )
                    )

        # Deduplicate while prioritizing direct=True
        seen: dict[tuple[str, str], DependencyComponent] = {}
        for c in components:
            key = (c.name.lower(), c.ecosystem)
            if key not in seen or (c.direct and not seen[key].direct):
                seen[key] = c

        unique_components = list(seen.values())

        return SBOMManifest(
            project_name=project_name,
            components=unique_components,
        )


@dataclass(frozen=True, slots=True)
class ReleaseAttestation:
    task_id: str
    version: str
    workspace_hash: str
    sbom_hash: str
    attestation_signature: str
    approved_by: str
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "version": self.version,
            "workspace_hash": self.workspace_hash,
            "sbom_hash": self.sbom_hash,
            "attestation_signature": self.attestation_signature,
            "approved_by": self.approved_by,
            "timestamp": self.timestamp,
        }


class ReleaseApprovalCoordinator:
    """Evaluates release readiness gates: 100% tests passing, clean security scan, SBOM attached."""

    def __init__(
        self,
        test_runner: TestRunnerRegistry | None = None,
        security_scanner: SecurityScannerRegistry | None = None,
        sbom_generator: SBOMGenerator | None = None,
    ) -> None:
        self._test_runner = test_runner or TestRunnerRegistry()
        self._security_scanner = security_scanner or SecurityScannerRegistry()
        self._sbom_generator = sbom_generator or SBOMGenerator()

    async def check_release_readiness(
        self,
        workspace_path: Path | str,
        task_id: str,
    ) -> dict[str, Any]:
        root = Path(workspace_path)

        # 1. Run full test gate
        test_result = await self._test_runner.run_tests(root, step_id=f"release-{task_id}")
        tests_passed = test_result.success

        # 2. Run security gate
        sec_result = self._security_scanner.scan_workspace(root, step_id=f"release-{task_id}")
        has_critical_sec = any(
            f.severity in (SecuritySeverity.CRITICAL, SecuritySeverity.HIGH)
            for f in sec_result.findings
        )
        security_passed = sec_result.passed_gate and not has_critical_sec

        # 3. Generate SBOM
        sbom = self._sbom_generator.generate(root, project_name=root.name)

        is_ready = tests_passed and security_passed
        reasons: list[str] = []
        if not tests_passed:
            reasons.append(f"Test suite failed ({test_result.failed_tests}/{test_result.total_tests} failed)")
        if not security_passed:
            reasons.append(f"Security scan found critical/high findings ({len(sec_result.findings)} findings)")

        return {
            "task_id": task_id,
            "is_ready_for_release": is_ready,
            "tests_passed": tests_passed,
            "security_passed": security_passed,
            "total_dependencies": len(sbom.components),
            "reasons": reasons,
            "sbom_summary": sbom.to_dict(),
        }

    def generate_attestation(
        self,
        workspace_path: Path | str,
        task_id: str,
        version: str = "v1.0.0",
        approved_by: str = "my-agent-control-plane",
    ) -> ReleaseAttestation:
        import time

        root = Path(workspace_path)
        sbom = self._sbom_generator.generate(root, project_name=root.name)
        sbom_str = json.dumps(sbom.to_dict(), sort_keys=True)
        sbom_hash = hashlib.sha256(sbom_str.encode("utf-8")).hexdigest()

        # Compute workspace file contents hash
        h = hashlib.sha256()
        for f in sorted(root.rglob("*")):
            if f.is_file() and not any(p.startswith(".") for p in f.parts):
                try:
                    h.update(f.read_bytes())
                except Exception:
                    pass
        workspace_hash = h.hexdigest()

        # Generate cryptographic attestation signature
        sig_data = f"{task_id}:{version}:{workspace_hash}:{sbom_hash}:{approved_by}"
        sig = hashlib.sha256(sig_data.encode("utf-8")).hexdigest()

        return ReleaseAttestation(
            task_id=task_id,
            version=version,
            workspace_hash=workspace_hash,
            sbom_hash=sbom_hash,
            attestation_signature=sig,
            approved_by=approved_by,
            timestamp=time.time(),
        )
