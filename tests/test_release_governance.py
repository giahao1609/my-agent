from __future__ import annotations

import json
from pathlib import Path
import pytest

from core.release_governance import (
    ReleaseApprovalCoordinator,
    ReleaseAttestation,
    SBOMGenerator,
)


def test_sbom_generator_python_and_node(tmp_path: Path):
    # Setup requirements.txt
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn==0.23.0\n# comment\n")
    # Setup package.json
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.2.0", "lucide-react": "^0.300.0"}}))

    generator = SBOMGenerator()
    sbom = generator.generate(tmp_path, project_name="my-test-app")

    assert sbom.project_name == "my-test-app"
    assert sbom.format == "cyclonedx-json"
    comp_names = {c.name for c in sbom.components}
    assert "fastapi" in comp_names
    assert "uvicorn" in comp_names
    assert "react" in comp_names
    assert "lucide-react" in comp_names


def test_sbom_generator_go_and_rust(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module mymod\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.1\n")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "rust-app"\n\n[dependencies]\ntokio = "1.35.0"\nserde = "1.0"\n')

    generator = SBOMGenerator()
    sbom = generator.generate(tmp_path, project_name="polyglot-app")

    comp_names = {c.name for c in sbom.components}
    assert "github.com/gin-gonic/gin" in comp_names
    assert "tokio" in comp_names
    assert "serde" in comp_names


@pytest.mark.asyncio
async def test_release_approval_coordinator_and_attestation(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.py").write_text("def run():\n    return 42\n")
    (tmp_path / "requirements.txt").write_text("pydantic>=2.0\n")

    coordinator = ReleaseApprovalCoordinator()
    readiness = await coordinator.check_release_readiness(tmp_path, task_id="task-release-1")

    assert "is_ready_for_release" in readiness
    assert "sbom_summary" in readiness
    assert readiness["total_dependencies"] >= 1

    attestation = coordinator.generate_attestation(
        tmp_path,
        task_id="task-release-1",
        version="v1.2.0",
        approved_by="test-approver",
    )
    assert attestation.task_id == "task-release-1"
    assert attestation.version == "v1.2.0"
    assert len(attestation.workspace_hash) == 64
    assert len(attestation.sbom_hash) == 64
    assert len(attestation.attestation_signature) == 64


def test_sbom_generator_pyproject_toml(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("""
[project]
name = "demo"
dependencies = [
    "httpx>=0.27.0",
    "pydantic==2.5.0",
]
""")
    generator = SBOMGenerator()
    sbom = generator.generate(tmp_path, project_name="demo-pyproject")
    names = {c.name for c in sbom.components}
    assert "httpx" in names
    assert "pydantic" in names


def test_sbom_generator_package_lock_v1_fallback(tmp_path: Path):
    # Old npm v1 package-lock without "packages" dictionary
    pkg_lock = tmp_path / "package-lock.json"
    pkg_lock.write_text(json.dumps({
        "name": "legacy-app",
        "lockfileVersion": 1,
        "dependencies": {
            "chalk": {"version": "4.1.2"},
            "debug": {"version": "4.3.4"}
        }
    }))
    generator = SBOMGenerator()
    sbom = generator.generate(tmp_path, project_name="legacy-app")
    names = {c.name for c in sbom.components}
    assert "chalk" in names
    assert "debug" in names


def test_sbom_generator_corrupted_files_handled_gracefully(tmp_path: Path):
    # Corrupted JSON files should not crash SBOM generator
    (tmp_path / "package-lock.json").write_text("{corrupted: invalid json")
    (tmp_path / "package.json").write_text("{bad json")

    generator = SBOMGenerator()
    sbom = generator.generate(tmp_path, project_name="corrupted-app")
    assert sbom.project_name == "corrupted-app"
    assert len(sbom.components) == 0


@pytest.mark.asyncio
async def test_release_readiness_rejection_on_test_failure(tmp_path: Path):
    (tmp_path / "src").mkdir()
    # Create failing test file
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_fail.py").write_text("def test_broken():\n    assert False\n")

    coordinator = ReleaseApprovalCoordinator()
    readiness = await coordinator.check_release_readiness(tmp_path, task_id="task-fail-test")
    assert readiness["is_ready_for_release"] is False
    assert readiness["tests_passed"] is False
    assert any("Test suite failed" in r for r in readiness["reasons"])


@pytest.mark.asyncio
async def test_release_readiness_rejection_on_security_vulnerability(tmp_path: Path):
    (tmp_path / "src").mkdir()
    # Add a file containing hardcoded secret that triggers CRITICAL/HIGH security finding
    (tmp_path / "src" / "secrets.py").write_text(
        'AWS_SECRET_KEY = "AKIA1234567890123456"\nOPENAI_API_KEY = "sk-abcdefghijklmnopqrstuvwxyz1234567890"\n'
    )

    coordinator = ReleaseApprovalCoordinator()
    readiness = await coordinator.check_release_readiness(tmp_path, task_id="task-sec-fail")
    assert readiness["is_ready_for_release"] is False
    assert readiness["security_passed"] is False
    assert any("Security scan found critical/high findings" in r for r in readiness["reasons"])


def test_attestation_to_dict_and_serialization(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    coordinator = ReleaseApprovalCoordinator()
    attestation = coordinator.generate_attestation(
        tmp_path,
        task_id="task-dict",
        version="v2.0.0",
        approved_by="tech-lead",
    )
    att_dict = attestation.to_dict()
    assert att_dict["task_id"] == "task-dict"
    assert att_dict["version"] == "v2.0.0"
    assert att_dict["approved_by"] == "tech-lead"
    assert "workspace_hash" in att_dict
    assert "attestation_signature" in att_dict

