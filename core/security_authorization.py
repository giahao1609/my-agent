"""Core Security Authorization and Workspace Isolation Engine.

Enforces strict tenant and project boundaries:
1. Validates caller workspace (CWD / session) against the ProjectStore.
2. Forbids Cross-Project Insecure Direct Object References (IDOR).
3. Eliminates reliance on the global `active_project_id` singleton.
4. Prevents filesystem path traversal and symlink escapes outside the authorized workspace.
5. Blocks project-specific intellectual property from bleeding into global L3 memory.

Governing Security Invariant:
"An agent may forget context rather than retrieve unauthorized context.
Data isolation strictly takes precedence over continuity, convenience, speed, and token efficiency."
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from persistence.sqlite_project_store import ProjectRecord, SQLiteProjectStore

logger = logging.getLogger(__name__)


class SecurityAuthorizationError(PermissionError):
    """Raised when an unauthorized project access, IDOR attempt, or path traversal is detected."""

    def __init__(
        self,
        message: str,
        *,
        requested_project_id: str | None = None,
        authorized_project_id: str | None = None,
        caller_path: str | None = None,
        violation_type: str = "UNAUTHORIZED_ACCESS",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.requested_project_id = requested_project_id
        self.authorized_project_id = authorized_project_id
        self.caller_path = caller_path
        self.violation_type = violation_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "SECURITY_AUTHORIZATION_DENIED",
            "violation_type": self.violation_type,
            "message": self.message,
            "requested_project_id": self.requested_project_id,
            "authorized_project_id": self.authorized_project_id,
            "caller_path": self.caller_path,
        }


@dataclass(frozen=True, slots=True)
class ProjectAuthorizationContext:
    project_id: str
    workspace_path: Path
    tenant_id: str = "default"
    is_verified: bool = True


class SecurityAuthorizationManager:
    """Central server-side authority for multi-tenant project isolation and workspace sandboxing."""

    _SUSPICIOUS_GLOBAL_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"/Users/[a-zA-Z0-9_-]+/Project/(?!my-agent)", re.IGNORECASE),
        re.compile(r"(?i)\b(teachora|platform|ai-protein|chatbot|medicare)\b"),
        re.compile(r"(?i)\b(api_key|secret_key|private_key|token\s*=\s*['\"][a-zA-Z0-9_\-]{16,})"),
    )

    _DISALLOWED_SYSTEM_ROOTS: tuple[Path, ...] = (
        Path("/"),
        Path("/private"),
        Path("/tmp"),
        Path("/var"),
        Path("/etc"),
        Path("/usr"),
        Path("/bin"),
        Path("/System"),
        Path.home(),
    )

    def __init__(self, project_store: SQLiteProjectStore | None = None, server_workspace_root: Path | str | None = None) -> None:
        self._project_store = project_store
        initial_ws = Path(server_workspace_root or Path.cwd()).expanduser().resolve()
        self._server_workspace_root: Path = initial_ws

    def set_project_store(self, store: SQLiteProjectStore) -> None:
        self._project_store = store

    def set_server_workspace(self, path: Path | str) -> None:
        resolved = Path(path).expanduser().resolve()
        if resolved in self._DISALLOWED_SYSTEM_ROOTS:
            self._log_security_audit(
                event="SET_SERVER_WORKSPACE",
                decision="DENIED",
                reason="DISALLOWED_SYSTEM_ROOT",
                target_path=str(resolved),
            )
            raise SecurityAuthorizationError(
                f"DISALLOWED_WORKSPACE_ROOT: Setting server workspace to system root '{resolved}' is forbidden.",
                caller_path=str(resolved),
                violation_type="UNAUTHORIZED_WORKSPACE",
            )
        self._server_workspace_root = resolved

    def get_server_workspace(self) -> Path:
        return self._server_workspace_root

    def _log_security_audit(
        self,
        *,
        event: str,
        decision: str,
        reason: str,
        project_id: str | None = None,
        authorized_project_id: str | None = None,
        target_path: str | None = None,
    ) -> None:
        """Emits structured runtime security audit logs with timestamps and boundary decisions."""
        timestamp = datetime.now(timezone.utc).isoformat()
        log_line = (
            f"[SECURITY_AUDIT] timestamp={timestamp} event={event} decision={decision} "
            f"server_workspace={self._server_workspace_root} bound_project={authorized_project_id} "
            f"requested_project={project_id} target_path={target_path} reason=\"{reason}\""
        )
        if decision == "DENIED":
            logger.warning(log_line)
        else:
            logger.info(log_line)

    async def authorize_project(
        self,
        project_id: str | None = None,
        *,
        required_scope: str = "read",
    ) -> ProjectRecord:
        """Authorizes access to a project, enforcing server workspace binding and IDOR defense.

        Rules:
        1. Binds strictly to server's verified OS process workspace (self._server_workspace_root).
        2. Identifies which registered project physically owns the server workspace.
           Resolves nested workspaces deterministically by picking the most specific match.
        3. If caller specifies project_id, verifies that project_id matches the physical owner.
           Discrepancy triggers an IDOR violation.
        4. If project_id is omitted, returns the physical owner project.
        5. If no registered project owns the server workspace, fails closed immediately.
        """
        if self._project_store is None:
            raise RuntimeError("SecurityAuthorizationManager: project_store is not configured")

        # 1. Bind strictly to server process workspace root
        effective_path = self._server_workspace_root.expanduser().resolve()

        if not hasattr(self._project_store, "list"):
            raise SecurityAuthorizationError(
                "FAIL_CLOSED: Project store does not support list(). Cannot verify workspace ownership.",
                caller_path=str(effective_path),
                violation_type="UNAUTHORIZED_WORKSPACE",
            )

        # 2. Query registered projects to find physical workspace owner (handling nested workspaces)
        all_projects = await self._project_store.list()
        matching_candidates: list[ProjectRecord] = []

        for p in all_projects:
            proj_root = Path(p.workspace_path).expanduser().resolve()
            if effective_path == proj_root or effective_path.is_relative_to(proj_root):
                matching_candidates.append(p)

        matching_project: ProjectRecord | None = None
        if matching_candidates:
            # Sort by path specificity: exact match first, then longest path (most specific nested directory)
            matching_candidates.sort(
                key=lambda p: (
                    Path(p.workspace_path).expanduser().resolve() == effective_path,
                    len(Path(p.workspace_path).expanduser().resolve().parts),
                ),
                reverse=True,
            )
            matching_project = matching_candidates[0]

        # 3. Parameter tampering (IDOR) check
        if project_id is not None and project_id.strip():
            target_id = project_id.strip()
            if matching_project is not None:
                if matching_project.project_id != target_id:
                    self._log_security_audit(
                        event="AUTHORIZE_PROJECT",
                        decision="DENIED",
                        reason="CROSS_PROJECT_IDOR_DETECTED",
                        project_id=target_id,
                        authorized_project_id=matching_project.project_id,
                    )
                    raise SecurityAuthorizationError(
                        f"CROSS_PROJECT_IDOR_VIOLATION: Server workspace at '{effective_path}' belongs to project "
                        f"'{matching_project.project_id}'. Access to requested project '{target_id}' is FORBIDDEN.",
                        requested_project_id=target_id,
                        authorized_project_id=matching_project.project_id,
                        caller_path=str(effective_path),
                        violation_type="CROSS_PROJECT_IDOR",
                    )
                self._log_security_audit(
                    event="AUTHORIZE_PROJECT",
                    decision="ALLOWED",
                    reason="IDOR_VERIFIED_MATCH",
                    project_id=target_id,
                    authorized_project_id=matching_project.project_id,
                )
                return matching_project

            # Fail-Closed: Server workspace does not match any registered project
            self._log_security_audit(
                event="AUTHORIZE_PROJECT",
                decision="DENIED",
                reason="UNREGISTERED_SERVER_WORKSPACE",
                project_id=target_id,
            )
            raise SecurityAuthorizationError(
                f"UNAUTHORIZED_WORKSPACE: Server working directory '{effective_path}' is outside "
                f"all registered project workspaces. Access to project '{target_id}' is strictly DENIED.",
                requested_project_id=target_id,
                caller_path=str(effective_path),
                violation_type="UNAUTHORIZED_WORKSPACE",
            )

        # 4. project_id is omitted: caller relies on ambient workspace
        if matching_project is not None:
            self._log_security_audit(
                event="AUTHORIZE_PROJECT",
                decision="ALLOWED",
                reason="BOUND_PROJECT_RESOLVED",
                authorized_project_id=matching_project.project_id,
            )
            return matching_project

        # 5. Fail-Closed: Never fall back to global app_state.active_project_id!
        self._log_security_audit(
            event="AUTHORIZE_PROJECT",
            decision="DENIED",
            reason="NO_AUTHORIZED_PROJECT_MATCHING_WORKSPACE",
        )
        raise SecurityAuthorizationError(
            f"NO_AUTHORIZED_PROJECT: Server working directory '{effective_path}' does not match any "
            "registered project workspace. Global singleton fallback is strictly disabled.",
            caller_path=str(effective_path),
            violation_type="NO_AUTHORIZED_PROJECT",
        )

    def validate_path_containment(
        self,
        target_path: Path | str,
        project: ProjectRecord,
    ) -> Path:
        """Confines a filesystem path to the authorized project workspace root.

        Prevents Path Traversal ('../') and symlink breakout attacks.
        """
        resolved_target = Path(target_path).expanduser().resolve()
        resolved_root = Path(project.workspace_path).expanduser().resolve()

        if not (resolved_target == resolved_root or resolved_target.is_relative_to(resolved_root)):
            self._log_security_audit(
                event="VALIDATE_PATH_CONTAINMENT",
                decision="DENIED",
                reason="PATH_TRAVERSAL_DETECTED",
                authorized_project_id=project.project_id,
                target_path=str(resolved_target),
            )
            raise SecurityAuthorizationError(
                f"PATH_TRAVERSAL_VIOLATION: Target path '{target_path}' resolves to '{resolved_target}', "
                f"which is outside authorized workspace root '{resolved_root}'.",
                authorized_project_id=project.project_id,
                caller_path=str(resolved_target),
                violation_type="PATH_TRAVERSAL",
            )

        self._log_security_audit(
            event="VALIDATE_PATH_CONTAINMENT",
            decision="ALLOWED",
            reason="PATH_WITHIN_WORKSPACE",
            authorized_project_id=project.project_id,
            target_path=str(resolved_target),
        )
        return resolved_target

    def validate_global_memory_payload(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Inspects memory payloads destined for global L3 scope to prevent proprietary data leakage."""
        for pattern in self._SUSPICIOUS_GLOBAL_PATTERNS:
            if pattern.search(content):
                self._log_security_audit(
                    event="VALIDATE_GLOBAL_MEMORY",
                    decision="DENIED",
                    reason="GLOBAL_MEMORY_POLLUTION_DETECTED",
                )
                raise SecurityAuthorizationError(
                    "GLOBAL_MEMORY_POLLUTION_VIOLATION: Cannot persist memory with global scope because "
                    "it contains project-specific paths, project identifiers, or private credentials.",
                    violation_type="GLOBAL_MEMORY_POLLUTION",
                )
        self._log_security_audit(
            event="VALIDATE_GLOBAL_MEMORY",
            decision="ALLOWED",
            reason="MEMORY_PAYLOAD_CLEAN",
        )
