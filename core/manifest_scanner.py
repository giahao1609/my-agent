from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import yaml

from .code_graph import CodeEdge, CodeEdgeKind, CodeNode, CodeNodeKind
from .language_scanner import ScanResult

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".idea",
    ".gradle",
    ".next",
}


def _stable_node_id(project_id: str, kind: CodeNodeKind, key: str) -> str:
    raw = f"{project_id}:{kind.value}:{key}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _is_compose(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in {"compose.yml", "compose.yaml"}
        or name.startswith(("compose.", "docker-compose."))
    ) and path.suffix.lower() in {".yml", ".yaml"}


def _is_workflow(path: Path) -> bool:
    parts = path.as_posix().split("/")
    return (
        len(parts) >= 3
        and parts[-3:-1] == [".github", "workflows"]
        and path.suffix.lower() in {".yml", ".yaml"}
    )


class ManifestScanner:
    name = "manifest"

    def supports(self, path: Path) -> bool:
        path = Path(path)
        return (
            path.name.startswith("Dockerfile")
            or path.name == "package.json"
            or _is_compose(path)
            or _is_workflow(path)
        )

    def scan_project(
        self,
        project_id: str,
        root: Path,
        *,
        include_paths: set[str] | None = None,
        seed_symbols: dict[str, str] | None = None,
        overlay_files: dict[str, str] | None = None,
    ) -> ScanResult:
        del seed_symbols

        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("project root must be an existing directory")

        wanted = set(include_paths or ())
        overlays = dict(overlay_files or {})
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []

        project_id_node = _stable_node_id(
            project_id,
            CodeNodeKind.PROJECT,
            project_id,
        )
        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=project_id_node,
                kind=CodeNodeKind.PROJECT,
                name=project_id,
                path=".",
            )
        )

        for current, dirs, files in os.walk(root, topdown=True):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            base = Path(current)

            for name in files:
                path = base / name
                rel = path.relative_to(root).as_posix()

                if not self.supports(Path(rel)):
                    continue
                if wanted and rel not in wanted:
                    continue

                source = overlays.get(rel)
                if source is None:
                    try:
                        source = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue

                file_id = _stable_node_id(project_id, CodeNodeKind.FILE, rel)
                nodes.append(
                    CodeNode(
                        project_id=project_id,
                        node_id=file_id,
                        kind=CodeNodeKind.FILE,
                        name=path.name,
                        path=rel,
                    )
                )
                edges.append(
                    CodeEdge(
                        project_id=project_id,
                        source_id=project_id_node,
                        target_id=file_id,
                        kind=CodeEdgeKind.CONTAINS,
                    )
                )

                if path.name.startswith("Dockerfile"):
                    self._scan_dockerfile(
                        project_id,
                        rel,
                        file_id,
                        source,
                        nodes,
                        edges,
                    )
                elif path.name == "package.json":
                    self._scan_package_json(
                        project_id,
                        rel,
                        file_id,
                        source,
                        nodes,
                        edges,
                    )
                elif _is_compose(Path(rel)):
                    self._scan_compose(
                        project_id,
                        rel,
                        file_id,
                        source,
                        nodes,
                        edges,
                    )
                elif _is_workflow(Path(rel)):
                    self._scan_workflow(
                        project_id,
                        rel,
                        file_id,
                        source,
                        nodes,
                        edges,
                    )

        unique_nodes = {node.node_id: node for node in nodes}
        unique_edges = {
            (edge.source_id, edge.target_id, edge.kind): edge
            for edge in edges
        }
        return ScanResult(
            nodes=tuple(unique_nodes.values()),
            edges=tuple(unique_edges.values()),
        )

    def _scan_dockerfile(
        self,
        project_id: str,
        rel: str,
        file_id: str,
        source: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
    ) -> None:
        for line_no, line in enumerate(source.splitlines(), start=1):
            match = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?([^\s]+)", line, re.IGNORECASE)
            if match is None:
                continue

            image = match.group(1)
            image_id = _stable_node_id(
                project_id,
                CodeNodeKind.IMAGE,
                f"docker:{image}",
            )
            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=image_id,
                    kind=CodeNodeKind.IMAGE,
                    name=image,
                    path=rel,
                    line_start=line_no,
                    line_end=line_no,
                    metadata={
                        "manifest": "dockerfile",
                        "role": "base_image",
                    },
                )
            )
            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=file_id,
                    target_id=image_id,
                    kind=CodeEdgeKind.USES,
                )
            )

    def _scan_package_json(
        self,
        project_id: str,
        rel: str,
        file_id: str,
        source: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
    ) -> None:
        try:
            data = json.loads(source)
        except json.JSONDecodeError:
            return

        package_name = str(data.get("name") or Path(rel).parent.name or rel)
        package_id = _stable_node_id(
            project_id,
            CodeNodeKind.PACKAGE,
            f"npm:{rel}:{package_name}",
        )
        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=package_id,
                kind=CodeNodeKind.PACKAGE,
                name=package_name,
                path=rel,
                qualified_name=package_name,
                metadata={
                    "manifest": "package_json",
                    "package_manager": "npm",
                },
            )
        )
        edges.append(
            CodeEdge(
                project_id=project_id,
                source_id=file_id,
                target_id=package_id,
                kind=CodeEdgeKind.DEFINES,
            )
        )

        for section in ("dependencies", "devDependencies", "peerDependencies"):
            dependencies = data.get(section) or {}
            if not isinstance(dependencies, dict):
                continue

            for name, version in dependencies.items():
                dependency_id = _stable_node_id(
                    project_id,
                    CodeNodeKind.EXTERNAL,
                    f"npm:{name}",
                )
                nodes.append(
                    CodeNode(
                        project_id=project_id,
                        node_id=dependency_id,
                        kind=CodeNodeKind.EXTERNAL,
                        name=str(name),
                        metadata={
                            "manifest": "package_json",
                            "package_manager": "npm",
                            "version": str(version),
                            "dependency_scope": section,
                        },
                    )
                )
                edges.append(
                    CodeEdge(
                        project_id=project_id,
                        source_id=package_id,
                        target_id=dependency_id,
                        kind=CodeEdgeKind.DEPENDS_ON,
                        metadata={"scope": section},
                    )
                )

    def _scan_compose(
        self,
        project_id: str,
        rel: str,
        file_id: str,
        source: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
    ) -> None:
        try:
            data = yaml.safe_load(source) or {}
        except yaml.YAMLError:
            return

        services = data.get("services") or {}
        if not isinstance(services, dict):
            return

        service_ids: dict[str, str] = {}

        for name, config in services.items():
            if not isinstance(config, dict):
                config = {}

            service_id = _stable_node_id(
                project_id,
                CodeNodeKind.SERVICE,
                f"compose:{rel}:{name}",
            )
            service_ids[str(name)] = service_id

            nodes.append(
                CodeNode(
                    project_id=project_id,
                    node_id=service_id,
                    kind=CodeNodeKind.SERVICE,
                    name=str(name),
                    path=rel,
                    metadata={
                        "manifest": "compose",
                        "build": config.get("build"),
                        "image": config.get("image"),
                        "ports": config.get("ports") or [],
                    },
                )
            )
            edges.append(
                CodeEdge(
                    project_id=project_id,
                    source_id=file_id,
                    target_id=service_id,
                    kind=CodeEdgeKind.DEFINES,
                )
            )

            image = config.get("image")
            if image:
                image_id = _stable_node_id(
                    project_id,
                    CodeNodeKind.IMAGE,
                    f"docker:{image}",
                )
                nodes.append(
                    CodeNode(
                        project_id=project_id,
                        node_id=image_id,
                        kind=CodeNodeKind.IMAGE,
                        name=str(image),
                        metadata={
                            "manifest": "compose",
                            "role": "service_image",
                        },
                    )
                )
                edges.append(
                    CodeEdge(
                        project_id=project_id,
                        source_id=service_id,
                        target_id=image_id,
                        kind=CodeEdgeKind.RUNS,
                    )
                )

        for name, config in services.items():
            if not isinstance(config, dict):
                continue
            source_id = service_ids[str(name)]
            depends_on = config.get("depends_on") or []
            names = (
                depends_on.keys()
                if isinstance(depends_on, dict)
                else depends_on
            )
            for dependency in names:
                target_id = service_ids.get(str(dependency))
                if target_id:
                    edges.append(
                        CodeEdge(
                            project_id=project_id,
                            source_id=source_id,
                            target_id=target_id,
                            kind=CodeEdgeKind.DEPENDS_ON,
                        )
                    )

    def _scan_workflow(
        self,
        project_id: str,
        rel: str,
        file_id: str,
        source: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
    ) -> None:
        try:
            data = yaml.safe_load(source) or {}
        except yaml.YAMLError:
            return

        workflow_name = str(data.get("name") or Path(rel).stem)
        workflow_id = _stable_node_id(
            project_id,
            CodeNodeKind.CONFIG,
            f"github-actions:{rel}",
        )
        jobs = data.get("jobs") or {}

        nodes.append(
            CodeNode(
                project_id=project_id,
                node_id=workflow_id,
                kind=CodeNodeKind.CONFIG,
                name=workflow_name,
                path=rel,
                qualified_name=rel,
                metadata={
                    "manifest": "github_actions",
                    "job_count": len(jobs) if isinstance(jobs, dict) else 0,
                },
            )
        )
        edges.append(
            CodeEdge(
                project_id=project_id,
                source_id=file_id,
                target_id=workflow_id,
                kind=CodeEdgeKind.DEFINES,
            )
        )
