---
name: my-release
description: Software release governance (Release Readiness Gate, CycloneDX 1.5 SBOM generation, and cryptographic SHA-256 release attestation).
---

# Release Governance & Supply Chain Workflow

Activate the **Release Agent (`AgentRole.RELEASE`)** to prepare safe, verifiable software release packages.

## Execution Guidelines

1. **Release Readiness Gate Inspection**:
   - Use the MCP tool `check_release_readiness(workspace_path, task_id)`:
     - Enforces 100% unit and integration test pass rate.
     - Enforces 0 CRITICAL and 0 HIGH security vulnerabilities.
     - Analyzes lockfile integrity and dependency licensing.

2. **Software Bill of Materials (SBOM) Generation**:
   - Use the MCP tool `generate_sbom(workspace_path, project_name)` to generate CycloneDX 1.5 JSON from detected package manifests (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`).

3. **Cryptographic Release Attestation**:
   - Use the MCP tool `create_release_attestation(workspace_path, task_id, version)` to sign and record a SHA-256 release integrity hash, preventing unauthorized tampering.

