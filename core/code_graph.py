from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from core.status import CapabilityStatus


class CodeNodeKind(StrEnum):
    PROJECT = "project"
    DIRECTORY = "directory"
    FILE = "file"
    MODULE = "module"
    PACKAGE = "package"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    STRUCT = "struct"
    TRAIT = "trait"
    NAMESPACE = "namespace"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    ROUTE = "route"
    COMPONENT = "component"
    TEST = "test"
    DATASTORE = "datastore"
    SERVICE = "service"
    IMAGE = "image"
    CONTAINER = "container"
    CONFIG = "config"
    RESOURCE = "resource"
    STYLE = "style"
    EXTERNAL = "external"


class CodeEdgeKind(StrEnum):
    CONTAINS = "contains"
    DEFINES = "defines"
    IMPORTS = "imports"
    CALLS = "calls"
    REFERENCES = "references"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    READS = "reads"
    WRITES = "writes"
    TESTS = "tests"
    ROUTES_TO = "routes_to"
    BUILDS = "builds"
    RUNS = "runs"
    LOADS = "loads"
    MOUNTS = "mounts"
    EXPOSES = "exposes"
    USES = "uses"
    DEPENDS_ON = "depends_on"


@dataclass(frozen=True, slots=True)
class CodeNode:
    project_id: str
    node_id: str
    kind: CodeNodeKind
    name: str
    path: str | None = None
    qualified_name: str | None = None
    language: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.node_id.strip():
            raise ValueError("node_id must not be empty")
        if not self.name.strip():
            raise ValueError("node name must not be empty")
        if self.line_start is not None and self.line_start < 1:
            raise ValueError("line_start must be >= 1")
        if (
            self.line_end is not None
            and self.line_start is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be >= line_start")


@dataclass(frozen=True, slots=True)
class CodeEdge:
    project_id: str
    source_id: str
    target_id: str
    kind: CodeEdgeKind
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.source_id.strip() or not self.target_id.strip():
            raise ValueError("edge endpoints must not be empty")


@dataclass(frozen=True, slots=True)
class ImpactResult:
    project_id: str
    root_node_id: str
    direct_dependents: tuple[str, ...] = ()
    transitive_dependents: tuple[str, ...] = ()
    related_tests: tuple[str, ...] = ()
    entrypoints: tuple[str, ...] = ()


class CodeGraphBackend(Protocol):
    async def replace_project_graph(
        self,
        project_id: str,
        nodes: Sequence[CodeNode],
        edges: Sequence[CodeEdge],
    ) -> None: ...

    async def get_node(
        self,
        project_id: str,
        node_id: str,
    ) -> CodeNode | None: ...

    async def find_nodes(
        self,
        project_id: str,
        query: str,
        *,
        kinds: Sequence[CodeNodeKind] | None = None,
        limit: int = 50,
    ) -> tuple[CodeNode, ...]: ...

    async def dependencies(
        self,
        project_id: str,
        node_id: str,
        *,
        depth: int = 1,
    ) -> tuple[CodeNode, ...]: ...

    async def dependents(
        self,
        project_id: str,
        node_id: str,
        *,
        depth: int = 1,
    ) -> tuple[CodeNode, ...]: ...

    async def semantic_dependencies(
        self,
        project_id: str,
        node_id: str,
        *,
        depth: int = 1,
    ) -> tuple[CodeNode, ...]: ...

    async def semantic_dependents(
        self,
        project_id: str,
        node_id: str,
        *,
        depth: int = 1,
    ) -> tuple[CodeNode, ...]: ...

    async def impact(
        self,
        project_id: str,
        node_id: str,
        *,
        max_depth: int = 8,
    ) -> ImpactResult: ...

    async def capabilities(self) -> tuple[CapabilityStatus, ...]: ...
