from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence
from uuid import uuid4

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server import MCPServer

from agents.coder_stack import CoderAgentStack, build_coder_agent_stack
from agents.model_agent_composition import (
    ModelAgentComposition,
    build_model_agent_composition,
)
from core.checkpoint import CheckpointRecord
from core.code_graph_sync import sync_code_graph as run_code_graph_sync
from core.config import CoderRuntimeMode, settings
from core.context import ExecutionContext
from core.conversation import ConversationRecord
from core.events import AgentEvent, EventType
from core.message import MessageRecord, MessageRole
from core.plan import PlanRecord, PlanStepRecord
from core.plan_service import PlanService
from core.plan_step_execution_coordinator import (
    PlanStepExecutionCoordinator,
)
from core.planner import PlannerContext
from core.planning_coordinator import PlanningCoordinator
from core.project import ProjectRecord
from core.session import SessionState
from core.task import TaskRecord
from core.task_service import TaskService
from core.security_authorization import (
    SecurityAuthorizationManager,
    SecurityAuthorizationError,
)
from core.tools import ToolPermission
from persistence.sqlite_checkpoint_store import SQLiteCheckpointStore
from persistence.sqlite_code_graph_store import SQLiteCodeGraphStore
from persistence.sqlite_conversation_store import SQLiteConversationStore
from persistence.sqlite_plan_store import SQLitePlanStore
from persistence.sqlite_project_store import SQLiteProjectStore
from persistence.sqlite_task_store import SQLiteTaskStore
from integrations.model_backend_factory import (
    build_model_backend_provider,
)
# Phase 3-6 production modules
from core.ai_security import PromptInjectionDefense
from core.red_team_engine import (
    ATTACK_LIBRARY,
    OWASPLLMCategory,
    RedTeamOrchestrator,
)
from core.ui_design_style_engine import (
    UiDesignStyleEngine,
    DesignAntiPatternDetector,
)
from core.ui_remediation_generator import UiRemediationGenerator
from core.design_md_generator import DesignMdGenerator
from core.prompt_rewriter import PromptRewriter
from core.durable_memory_service import DurableMemoryService
from core.memory_consolidation import MemoryConsolidator
from persistence.sqlite_memory_store import SQLiteMemoryStore
from core.memory import MemoryStatus
from core.user_model import (
    EffectiveUserProfile,
    PreferenceCategory,
    PreferenceItem,
    PreferenceSourceType,
    PreferenceSpec,
    ProvenanceStatus,
    ServerBoundaryToken,
    TrustedProvenanceAuthority,
    normalize_preference_category,
    normalize_preference_source_type,
)
from core.user_model_engine import (
    DEFAULT_USER_ID,
    InvalidPreferenceSourceError,
    PersistenceContractBlockedError,
    SecurityPolicyViolationError,
    TruncatedRetrievalError,
    UntrustedProvenanceError,
    UserModelEngine,
    UserModelEngineError,
)
from core.security_scanner import SecurityScannerRegistry
from core.test_runner import TestRunnerRegistry
from core.verification_gate_coordinator import VerificationGateCoordinator
from core.whole_plan_coordinator import WholePlanCoordinator
from core.step_execution import CoderRuntimeStepExecutor
from core.context_builder import UnifiedContextBuilder
from eval.evaluation_job import EvaluationHarness
from integrations.docs_knowledge_backend import DocsKnowledgeBackend
from integrations.playwright_browser import PlaywrightBrowserAdapter
from core.browser_backend import BrowserPolicy

# Phase 7 reliability & release governance modules
from core.budget_router import BudgetTracker, CostPolicyEngine, DEFAULT_MODEL_CAPABILITIES
from core.provider_health_router import FallbackRoutingEngine
from core.release_governance import SBOMGenerator, ReleaseApprovalCoordinator
from core.handoff_coordinator import HandoffCoordinator

# 3-Layer Architecture & Decision subsystem
from core.decision import DecisionOption, DecisionRecord, DecisionSeverity, DecisionState
from core.decision_service import DecisionService
from persistence.sqlite_decision_store import SQLiteDecisionStore
from core.front_agent import FrontAgent, FrontAgentRequest, PresentationLevel, ResponseKind
from core.control_plane import MyAgentControlPlane

# Cross-Agent Durable Execution Ledger & Handoff
from core.agent_role import AgentRole
from core.agent_run import AgentRunRecord, AgentRunState
from core.agent_run_service import AgentRunService
from core.agent_work_result import AgentWorkResult
from core.agent_handoff_context import AgentHandoffContextBuilder
from core.agent_handoff_message import AgentHandoffMessage
from persistence.sqlite_agent_run_store import SQLiteAgentRunStore
from persistence.sqlite_handoff_history_store import SqliteHandoffHistoryStore
from persistence.sqlite_pending_approval_store import SqlitePendingApprovalStore
from core.resume_assembler import ResumeAssembler
from core.git_review_coordinator import GitReviewCoordinator, parse_github_remote

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("MYAGENT_DB_PATH", ROOT / "data" / "my_agent.db"))

projects = SQLiteProjectStore(DB_PATH)
_global_projects = projects
conversations = SQLiteConversationStore(DB_PATH)
checkpoints = SQLiteCheckpointStore(DB_PATH)
code_graph = SQLiteCodeGraphStore(DB_PATH)
tasks = SQLiteTaskStore(DB_PATH)
plans = SQLitePlanStore(DB_PATH)
decisions = SQLiteDecisionStore(DB_PATH)
_decision_service = DecisionService(decisions)
agent_runs = SQLiteAgentRunStore(DB_PATH)
_agent_run_service = AgentRunService(agent_runs)
_handoff_context_builder = AgentHandoffContextBuilder(agent_runs, require_project_id=True)
pending_approvals = SqlitePendingApprovalStore(DB_PATH)
_handoff_history_store = SqliteHandoffHistoryStore(DB_PATH)
_resume_assembler = ResumeAssembler(
    conversation_store=conversations,
    checkpoint_store=checkpoints,
    task_store=tasks,
    handoff_builder=_handoff_context_builder,
)
memory_store = SQLiteMemoryStore(DB_PATH)
_memory_service = DurableMemoryService(memory_store)
_user_model_engine = UserModelEngine(_memory_service, store=memory_store)
_unified_context_builder = UnifiedContextBuilder(
    code_graph=code_graph,
    memory_service=_memory_service,
    user_model_engine=_user_model_engine,
    design_dir=ROOT / "data" / "designs",
)
_auth_manager = SecurityAuthorizationManager(projects)



# Phase 3-6 singletons
_prompt_defense = PromptInjectionDefense()
_red_team_orchestrator = RedTeamOrchestrator()
_ui_design_engine = UiDesignStyleEngine()
_ui_anti_pattern_detector = DesignAntiPatternDetector()
_ui_remediation_generator = UiRemediationGenerator(_ui_design_engine)
_design_md_generator = DesignMdGenerator()
_prompt_rewriter = PromptRewriter()
_memory_consolidator = MemoryConsolidator()
_security_scanner_registry = SecurityScannerRegistry()
_test_runner_registry = TestRunnerRegistry()
_verification_gate_coordinator = VerificationGateCoordinator()
_evaluation_harness = EvaluationHarness()
_playwright_browser = PlaywrightBrowserAdapter(
    artifacts_dir=ROOT / "data" / "browser_artifacts"
)

# Phase 7 singletons
_budget_tracker = BudgetTracker()
_cost_policy_engine = CostPolicyEngine()
_fallback_routing_engine = FallbackRoutingEngine()
_sbom_generator = SBOMGenerator()
_release_approval_coordinator = ReleaseApprovalCoordinator(
    test_runner=_test_runner_registry,
    security_scanner=_security_scanner_registry,
    sbom_generator=_sbom_generator,
)
_handoff_coordinator = HandoffCoordinator(
    guardrail_engine=None,  # Uses default HandoffGuardrailEngine with PromptInjectionDefense
    history_store=_handoff_history_store,
)
_git_review_coordinator = GitReviewCoordinator()

_model_provider = build_model_backend_provider(
    settings.model_settings
)
_model_composition: ModelAgentComposition | None = None

_coder_stack: CoderAgentStack | None = None
_coder_tasks: dict[str, asyncio.Task[None]] = {}
_coder_task_failures: dict[str, dict[str, str]] = {}
_coder_worker_tasks: dict[str, asyncio.Task[None]] = {}
_coder_worker_failures: dict[str, dict[str, str]] = {}
_coder_worker_lock = asyncio.Lock()

mcp = MCPServer("my-agent")


async def _initialize() -> None:
    await projects.initialize()
    await conversations.initialize()
    await checkpoints.initialize()
    await code_graph.initialize()
    await tasks.initialize()
    await plans.initialize()
    await decisions.initialize()
    await agent_runs.initialize()
    await memory_store.initialize()
    await pending_approvals.initialize()
    await _handoff_history_store.initialize()

    env_ws_str = os.environ.get("MYAGENT_WORKSPACE")
    if env_ws_str:
        env_ws = Path(env_ws_str).expanduser().resolve()
        cwd = Path.cwd().resolve()
        if cwd != env_ws and not cwd.is_relative_to(env_ws) and not env_ws.is_relative_to(cwd):
            logger.warning(
                "Process working directory '%s' does not match MYAGENT_WORKSPACE '%s'. Enforcing MYAGENT_WORKSPACE.",
                cwd,
                env_ws,
            )
        bound_workspace = env_ws
    else:
        bound_workspace = Path.cwd().resolve()

    disallowed_roots = {
        Path("/"),
        Path("/private"),
        Path("/tmp"),
        Path("/var"),
        Path("/etc"),
        Path("/usr"),
        Path("/bin"),
        Path("/System"),
        Path.home(),
    }

    if bound_workspace not in disallowed_roots:
        _auth_manager.set_server_workspace(bound_workspace)
    _auth_manager.set_project_store(projects)

    # Auto-register bound workspace if it is an existing directory, not registered yet, and not a system root
    try:
        records = await projects.list()
        bound_key = str(bound_workspace).casefold()
        matching = [
            p for p in records
            if str(Path(p.workspace_path).expanduser().resolve()).casefold() == bound_key
        ]
        if not matching and bound_workspace.exists() and bound_workspace.is_dir() and bound_workspace not in disallowed_roots:
            base_id = bound_workspace.name.strip() or "project"
            existing_ids = {p.project_id for p in records}
            proj_id = base_id
            if proj_id in existing_ids:
                import hashlib
                suffix = hashlib.sha1(bound_key.encode("utf-8")).hexdigest()[:8]
                proj_id = f"{base_id}-{suffix}"
            await projects.create(ProjectRecord(
                project_id=proj_id,
                name=bound_workspace.name or proj_id,
                workspace_path=str(bound_workspace),
            ))
    except Exception as exc:
        logger.debug("Failed auto-registering workspace during init: %s", exc)


def _project_payload(project: ProjectRecord) -> dict[str, object]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "workspace_path": project.workspace_path,
        "current_phase": project.current_phase,
        "active_task_id": project.active_task_id,
        "last_checkpoint_id": project.last_checkpoint_id,
        "updated_at": project.updated_at.isoformat(),
    }


def _task_payload(task: TaskRecord) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "project_id": task.project_id,
        "objective": task.objective,
        "state": task.state.value,
        "active_plan_id": task.active_plan_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _task_service() -> TaskService:
    return TaskService(
        project_store=projects,
        task_store=tasks,
    )


def _plan_payload(plan: PlanRecord) -> dict[str, object]:
    return {
        "plan_id": plan.plan_id,
        "task_id": plan.task_id,
        "revision": plan.revision,
        "state": plan.state.value,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }


def _plan_step_payload(step: PlanStepRecord) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "plan_id": step.plan_id,
        "step_index": step.step_index,
        "title": step.title,
        "instruction": step.instruction,
        "state": step.state.value,
        "created_at": step.created_at.isoformat(),
        "updated_at": step.updated_at.isoformat(),
    }


def _plan_service() -> PlanService:
    return PlanService(
        task_store=tasks,
        plan_store=plans,
    )


class _ServerPlanStepCoderLauncher:
    async def start(
        self,
        *,
        task: TaskRecord,
        step: PlanStepRecord,
    ) -> str:
        project = await projects.get(
            task.project_id
        )

        if project is None:
            raise KeyError(
                f"unknown project for task: {task.project_id}"
            )

        return await _start_coder_session_for_project(
            project=project,
            message=step.instruction,
            task_id=task.task_id,
        )


async def _resolve_project(
    project_id: str | None = None,
) -> ProjectRecord | None:
    _auth_manager.set_project_store(projects)
    if project_id is not None and project_id.strip():
        return await _auth_manager.authorize_project(project_id=project_id.strip())

    try:
        return await _auth_manager.authorize_project(project_id=None)
    except SecurityAuthorizationError as exc:
        logger.debug("No active authorized project for bound workspace: %s", exc)
        return None


async def _require_bound_project(
    project_id: str | None = None,
) -> ProjectRecord:
    project = await _resolve_project(project_id)
    if project is None:
        bound_ws = _auth_manager.get_server_workspace()
        raise SecurityAuthorizationError(
            f"NO_AUTHORIZED_PROJECT: Server workspace '{bound_ws}' has no authorized project.",
            violation_type="UNAUTHORIZED_WORKSPACE",
        )
    return project


async def _authorize_task(task_id: str, *, require_exists: bool = False) -> tuple[TaskRecord | None, ProjectRecord]:
    task = await _task_service().get_task(task_id)
    bound_project = await _require_bound_project()
    if task is None:
        if require_exists:
            raise KeyError(f"unknown task: {task_id}")
        return None, bound_project
    if task.project_id != bound_project.project_id:
        _auth_manager._log_security_audit(
            event="AUTHORIZE_TASK",
            decision="DENIED",
            reason="CROSS_PROJECT_IDOR_TASK_ACCESS_FORBIDDEN",
            project_id=task.project_id,
            authorized_project_id=bound_project.project_id,
        )
        raise SecurityAuthorizationError(
            f"CROSS_PROJECT_IDOR_VIOLATION: Task '{task_id}' belongs to project '{task.project_id}', "
            f"which does not match authorized server project '{bound_project.project_id}'.",
            requested_project_id=task.project_id,
            authorized_project_id=bound_project.project_id,
            violation_type="CROSS_PROJECT_IDOR",
        )
    return task, bound_project


async def _authorize_plan(plan_id: str, *, require_exists: bool = True) -> tuple[PlanRecord | None, TaskRecord | None, ProjectRecord]:
    plan = await plans.get_plan(plan_id)
    if plan is None:
        if require_exists:
            raise KeyError(f"unknown plan: {plan_id}")
        bound_project = await _require_bound_project()
        return None, None, bound_project
    task, bound_project = await _authorize_task(plan.task_id, require_exists=False)
    return plan, task, bound_project


async def _authorize_step(step_id: str, *, require_exists: bool = False) -> tuple[PlanStepRecord | None, PlanRecord | None, TaskRecord | None, ProjectRecord]:
    step = await plans.get_step(step_id)
    if step is None:
        if require_exists:
            raise KeyError(f"unknown step: {step_id}")
        bound_project = await _require_bound_project()
        return None, None, None, bound_project
    plan, task, bound_project = await _authorize_plan(step.plan_id, require_exists=False)
    return step, plan, task, bound_project


async def _authorize_agent_run(run_id: str) -> tuple[AgentRunRecord, ProjectRecord]:
    run = await _agent_run_service.get_run(run_id)
    if run is None:
        raise KeyError(f"unknown run: {run_id}")
    bound_project = await _require_bound_project()
    if run.project_id != bound_project.project_id:
        _auth_manager._log_security_audit(
            event="AUTHORIZE_AGENT_RUN",
            decision="DENIED",
            reason="CROSS_PROJECT_IDOR_AGENT_RUN_ACCESS_FORBIDDEN",
            project_id=run.project_id,
            authorized_project_id=bound_project.project_id,
        )
        raise SecurityAuthorizationError(
            f"CROSS_PROJECT_IDOR_VIOLATION: AgentRun '{run_id}' belongs to project '{run.project_id}', "
            f"which does not match authorized server project '{bound_project.project_id}'.",
            requested_project_id=run.project_id,
            authorized_project_id=bound_project.project_id,
            violation_type="CROSS_PROJECT_IDOR",
        )
    return run, bound_project


async def _authorize_decision(decision_id: str) -> tuple[DecisionRecord, TaskRecord | None, ProjectRecord]:
    rec = await _decision_service.get_decision(decision_id)
    if rec is None:
        raise KeyError(f"unknown decision: {decision_id}")
    task, bound_project = await _authorize_task(rec.task_id, require_exists=False)
    return rec, task, bound_project


@mcp.tool()
async def my_agent_status() -> dict[str, object]:
    """Return MyAgent persistence status and the active project."""
    await _initialize()
    active = await _resolve_project(None)
    active_coder_workers = sorted(
        session_id
        for session_id, task in _coder_worker_tasks.items()
        if not task.done()
    )

    coder_worker_failures = {
        session_id: dict(failure)
        for session_id, failure in _coder_worker_failures.items()
    }

    coder_task_failures = {
        session_id: dict(failure)
        for session_id, failure in _coder_task_failures.items()
    }

    from core.capabilities import default_capability_registry
    caps = default_capability_registry.list()
    capabilities_summary = {
        "total": len(caps),
        "available": sum(1 for c in caps if c.available),
        "mocked": sum(1 for c in caps if c.is_mocked),
        "degraded": sum(1 for c in caps if c.is_degraded),
        "unavailable": sum(1 for c in caps if c.is_unavailable),
        "unknown": sum(1 for c in caps if c.is_unknown),
    }

    return {
        "status": "ready",
        "database": str(DB_PATH),
        "active_project": _project_payload(active) if active is not None else None,
        "coder_runtime_mode": settings.coder_runtime_mode.value,
        "active_coder_workers": active_coder_workers,
        "coder_worker_failures": coder_worker_failures,
        "coder_task_failures": coder_task_failures,
        "capabilities_summary": capabilities_summary,
    }


@mcp.tool()
async def list_capabilities(filter_status: str | None = None) -> list[dict[str, object]]:
    """List registered capabilities with truthful status, implementation, and evidence."""
    from core.capabilities import default_capability_registry
    caps = default_capability_registry.list()
    if filter_status:
        target = filter_status.strip().lower()
        caps = [c for c in caps if c.state.value == target or (target == "available" and c.available)]
    return [c.to_dict() for c in caps]


@mcp.tool()
async def list_projects(admin_token: str | None = None) -> list[dict[str, object]]:
    """List projects authorized for the active server workspace, or all if verified admin."""
    await _initialize()
    bound_ws = _auth_manager.get_server_workspace()
    records = await projects.list()

    admin_secret = os.environ.get("MYAGENT_ADMIN_SECRET")
    if admin_secret and admin_token == admin_secret:
        _auth_manager._log_security_audit(
            event="LIST_PROJECTS",
            decision="ALLOWED",
            reason="ADMIN_TOKEN_VERIFIED",
        )
        return [_project_payload(project) for project in records]

    # Standard non-admin client: strictly filter to projects within or owning the bound server workspace
    scoped_projects: list[ProjectRecord] = []
    for p in records:
        proj_root = Path(p.workspace_path).expanduser().resolve()
        if proj_root == bound_ws or proj_root.is_relative_to(bound_ws) or bound_ws.is_relative_to(proj_root):
            scoped_projects.append(p)

    _auth_manager._log_security_audit(
        event="LIST_PROJECTS",
        decision="ALLOWED",
        reason="SCOPED_TO_BOUND_WORKSPACE",
        authorized_project_id=scoped_projects[0].project_id if scoped_projects else None,
    )
    return [_project_payload(p) for p in scoped_projects]


@mcp.tool()
async def register_project(
    project_id: str,
    name: str,
    workspace_path: str,
) -> dict[str, object]:
    """Register an existing workspace as a MyAgent project within the authorized server boundary."""
    await _initialize()
    bound_ws = _auth_manager.get_server_workspace()
    target_path = Path(workspace_path).expanduser().resolve()

    if not target_path.exists() or not target_path.is_dir():
        raise ValueError(f"Workspace path does not exist or is not a directory: {workspace_path}")

    # Enforce containment within server's verified bound workspace
    if not (target_path == bound_ws or target_path.is_relative_to(bound_ws)):
        _auth_manager._log_security_audit(
            event="REGISTER_PROJECT",
            decision="DENIED",
            reason="EXTERNAL_WORKSPACE_REGISTRATION_FORBIDDEN",
            project_id=project_id,
            target_path=str(target_path),
        )
        raise SecurityAuthorizationError(
            f"UNAUTHORIZED_REGISTRATION: Cannot register workspace '{target_path}' outside "
            f"the server authorized boundary '{bound_ws}'.",
            requested_project_id=project_id,
            caller_path=str(target_path),
            violation_type="UNAUTHORIZED_WORKSPACE",
        )

    # Prevent overwriting or hijacking existing projects
    existing = await projects.get(project_id)
    if existing is not None:
        _auth_manager._log_security_audit(
            event="REGISTER_PROJECT",
            decision="DENIED",
            reason="PROJECT_ALREADY_EXISTS_HIJACKING_PREVENTED",
            project_id=project_id,
            target_path=str(target_path),
        )
        raise SecurityAuthorizationError(
            f"PROJECT_ALREADY_EXISTS: Cannot overwrite or hijack existing project '{project_id}'.",
            requested_project_id=project_id,
            violation_type="PROJECT_HIJACK_ATTEMPT",
        )

    project = ProjectRecord(
        project_id=project_id,
        name=name,
        workspace_path=str(target_path),
    )
    await projects.create(project)
    _auth_manager._log_security_audit(
        event="REGISTER_PROJECT",
        decision="ALLOWED",
        reason="PROJECT_REGISTERED_WITHIN_BOUNDARY",
        project_id=project_id,
        authorized_project_id=project_id,
        target_path=str(target_path),
    )
    return _project_payload(project)



@mcp.tool()
async def switch_project(project_id: str) -> dict[str, object]:
    """Switch MyAgent to another persistent project within the authorized server boundary."""
    await _initialize()
    project = await _auth_manager.authorize_project(project_id=project_id)
    return {
        "status": "switched",
        "project": _project_payload(project),
    }


@mcp.tool()
async def create_task(
    objective: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, object]:
    """Create a durable task for the active or specified project."""
    await _initialize()

    project = await _require_bound_project(project_id)

    resolved_task_id = task_id or f"task-{uuid4().hex}"

    task = await _task_service().create_task(
        project_id=project.project_id,
        task_id=resolved_task_id,
        objective=objective,
    )

    return {
        "status": "created",
        "task": _task_payload(task),
    }


@mcp.tool()
async def start_task(
    task_id: str,
) -> dict[str, object]:
    """Activate a durable task for its project."""
    await _initialize()

    task, project = await _authorize_task(task_id, require_exists=True)

    task = await _task_service().start_task(task.task_id)

    return {
        "status": "started",
        "task": _task_payload(task),
    }


@mcp.tool()
async def get_active_task(
    project_id: str | None = None,
) -> dict[str, object]:
    """Return the active durable task for a project."""
    await _initialize()

    project = await _require_bound_project(project_id)

    task = await _task_service().get_active_task(
        project.project_id,
    )

    if task is None:
        return {
            "status": "not_found",
            "task": None,
        }

    return {
        "status": "ok",
        "task": _task_payload(task),
    }


@mcp.tool()
async def list_tasks(
    project_id: str | None = None,
) -> dict[str, object]:
    """List durable tasks for the active or specified project."""
    await _initialize()

    project = await _require_bound_project(project_id)

    records = await _task_service().list_tasks(
        project.project_id,
    )

    return {
        "status": "ok",
        "project_id": project.project_id,
        "tasks": [
            _task_payload(task)
            for task in records
        ],
    }


@mcp.tool()
async def cancel_task(
    task_id: str,
) -> dict[str, object]:
    """Cancel a durable task when it has no resumable coder session."""
    await _initialize()

    task, project = await _authorize_task(task_id, require_exists=True)

    stack = await _get_coder_stack()
    session = await stack.session_store.get_latest_resumable(
        task.project_id,
        task_id=task.task_id,
    )

    if session is not None:
        return {
            "status": "active_session",
            "task_id": task.task_id,
            "project_id": task.project_id,
            "session_id": session.session_id,
            "session_state": session.state.value,
        }

    service = _task_service()
    task = await service.cancel_task(task.task_id)

    return {
        "status": "cancelled",
        "task": _task_payload(task),
    }


@mcp.tool()
async def complete_task(
    task_id: str,
) -> dict[str, object]:
    """Complete a durable task when it has no resumable coder session."""
    await _initialize()

    task, project = await _authorize_task(task_id, require_exists=True)

    stack = await _get_coder_stack()
    session = await stack.session_store.get_latest_resumable(
        task.project_id,
        task_id=task.task_id,
    )

    if session is not None:
        return {
            "status": "active_session",
            "task_id": task.task_id,
            "project_id": task.project_id,
            "session_id": session.session_id,
            "session_state": session.state.value,
        }

    service = _task_service()
    task = await service.complete_task(task.task_id)

    return {
        "status": "completed",
        "task": _task_payload(task),
    }


@mcp.tool()
async def propose_task_plan(
    task_id: str,
    plan_id: str | None = None,
    project_context: str = "",
    code_context: str = "",
) -> dict[str, object]:
    """Generate and persist a model-proposed Draft Plan for a durable task."""
    await _initialize()

    task, project = await _authorize_task(task_id, require_exists=True)

    if task.terminal:
        raise ValueError(
            f"cannot plan terminal task: {task_id}"
        )

    composition = await _get_model_composition()

    coordinator = PlanningCoordinator(
        planner=composition.planner,
        task_store=tasks,
        plan_service=_plan_service(),
    )

    resolved_plan_id = (
        plan_id
        if plan_id is not None
        else f"plan-{uuid4().hex}"
    )

    plan = await coordinator.propose_plan_with_generated_step_ids(
        task_id=task.task_id,
        plan_id=resolved_plan_id,
        step_id_factory=lambda: f"step-{uuid4().hex}",
        context=PlannerContext(
            workspace_id=project.workspace_path,
            project_context=project_context,
            code_context=code_context,
        ),
    )

    steps = await plans.list_steps(plan.plan_id)

    return {
        "status": "proposed",
        "plan": _plan_payload(plan),
        "steps": [
            _plan_step_payload(step)
            for step in steps
        ],
    }


@mcp.tool()
async def start_plan_step_execution(
    step_id: str,
) -> dict[str, object]:
    """Start one active PlanStep in a dedicated Coder session."""
    await _initialize()

    step, plan, task, project = await _authorize_step(step_id)

    coordinator = PlanStepExecutionCoordinator(
        task_store=tasks,
        plan_store=plans,
        plan_service=_plan_service(),
        launcher=_ServerPlanStepCoderLauncher(),
        agent_run_service=_agent_run_service,
    )

    execution = await coordinator.start_step_execution(
        step.step_id
    )

    return {
        "status": "started",
        "step_id": execution.step_id,
        "session_id": execution.session_id,
        "run_id": execution.run_id,
    }


@mcp.tool()
async def create_plan(
    task_id: str,
    plan_id: str | None = None,
) -> dict[str, object]:
    """Create a durable plan revision for a task."""
    await _initialize()

    task, project = await _authorize_task(task_id, require_exists=True)

    resolved_plan_id = plan_id or f"plan-{uuid4().hex}"

    plan = await _plan_service().create_plan(
        task_id=task.task_id,
        plan_id=resolved_plan_id,
    )

    return {
        "status": "created",
        "plan": _plan_payload(plan),
    }


@mcp.tool()
async def list_plans(
    task_id: str,
) -> dict[str, object]:
    """List durable plan revisions for a task."""
    await _initialize()

    task, project = await _authorize_task(task_id, require_exists=True)

    records = await _plan_service().list_plans(task.task_id)

    return {
        "status": "ok",
        "task_id": task.task_id,
        "plans": [
            _plan_payload(plan)
            for plan in records
        ],
    }


@mcp.tool()
async def add_plan_step(
    plan_id: str,
    title: str,
    instruction: str,
    step_id: str | None = None,
) -> dict[str, object]:
    """Add a durable ordered step to a plan."""
    await _initialize()

    plan, task, project = await _authorize_plan(plan_id)

    resolved_step_id = step_id or f"step-{uuid4().hex}"

    step = await _plan_service().add_step(
        plan_id=plan.plan_id,
        step_id=resolved_step_id,
        title=title,
        instruction=instruction,
    )

    return {
        "status": "created",
        "step": _plan_step_payload(step),
    }


@mcp.tool()
async def activate_plan(
    plan_id: str,
) -> dict[str, object]:
    """Activate a durable plan for execution."""
    await _initialize()

    plan, task, project = await _authorize_plan(plan_id)

    activated_plan = await _plan_service().activate_plan(plan.plan_id)

    return {
        "status": "activated",
        "plan": _plan_payload(activated_plan),
    }


@mcp.tool()
async def start_plan_step(
    step_id: str,
) -> dict[str, object]:
    """Mark a durable plan step as running."""
    await _initialize()

    step, plan, task, project = await _authorize_step(step_id)

    running_step = await _plan_service().start_step(step.step_id)

    return {
        "status": "started",
        "step": _plan_step_payload(running_step),
    }


@mcp.tool()
async def complete_plan_step(
    step_id: str,
) -> dict[str, object]:
    """Mark a running durable plan step as complete."""
    await _initialize()

    step, plan, task, project = await _authorize_step(step_id)

    completed_step = await _plan_service().complete_step(step.step_id)

    return {
        "status": "completed",
        "step": _plan_step_payload(completed_step),
    }


@mcp.tool()
async def complete_plan(
    plan_id: str,
) -> dict[str, object]:
    """Complete an active plan after all steps are finished."""
    await _initialize()

    plan, task, project = await _authorize_plan(plan_id)

    completed = await _plan_service().complete_plan(plan.plan_id)

    return {
        "status": "completed",
        "plan": _plan_payload(completed),
    }


@mcp.tool()
async def resume_project(
    project_id: str | None = None,
    history_limit: int = 20,
) -> dict[str, object]:
    """Resume a project with its latest checkpoint, conversation, and history."""
    await _initialize()

    project = await _require_bound_project(project_id)

    if project is None:
        return {
            "status": "no_active_project",
            "project": None,
            "checkpoint": None,
            "conversation": None,
            "history": [],
        }

    latest_checkpoint = await checkpoints.latest(
        project.project_id,
        task_id=project.active_task_id,
    )
    if latest_checkpoint is None and project.active_task_id is not None:
        latest_checkpoint = await checkpoints.latest(
            project.project_id,
            task_id=None,
        )
    conversation_list = await conversations.list_for_project(project.project_id)
    latest_conversation = conversation_list[0] if conversation_list else None

    history_records = ()
    if latest_conversation is not None:
        history_records = await conversations.history(
            project.project_id,
            latest_conversation.conversation_id,
            limit=history_limit,
        )

    checkpoint_payload = None
    if latest_checkpoint is not None:
        checkpoint_payload = {
            "checkpoint_id": latest_checkpoint.checkpoint_id,
            "task_id": latest_checkpoint.task_id,
            "summary": latest_checkpoint.summary,
            "next_action": latest_checkpoint.next_action,
            "files_changed": list(latest_checkpoint.files_changed),
            "tests": list(latest_checkpoint.tests),
            "created_at": latest_checkpoint.created_at.isoformat(),
        }

    conversation_payload = None
    if latest_conversation is not None:
        conversation_payload = {
            "conversation_id": latest_conversation.conversation_id,
            "title": latest_conversation.title,
            "summary": latest_conversation.summary,
        }

    return {
        "status": "resumed",
        "project": _project_payload(project),
        "checkpoint": checkpoint_payload,
        "conversation": conversation_payload,
        "history": [
            {
                "message_id": message.message_id,
                "role": message.role.value,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            }
            for message in history_records
        ],
    }




@mcp.tool()
async def open_workspace(workspace_path: str) -> dict[str, object]:
    """Open an existing workspace in MyAgent within the authorized server boundary."""
    await _initialize()

    resolved = Path(workspace_path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {resolved}")

    # Enforce that workspace_path cannot escape the server's bound workspace root
    bound_root = _auth_manager.get_server_workspace()
    if not (resolved == bound_root or resolved.is_relative_to(bound_root)):
        _auth_manager._log_security_audit(
            event="OPEN_WORKSPACE",
            decision="DENIED",
            reason="EXTERNAL_WORKSPACE_ACCESS_FORBIDDEN",
            target_path=str(resolved),
        )
        raise SecurityAuthorizationError(
            f"UNAUTHORIZED_WORKSPACE: Server process is bound to '{bound_root}'. "
            f"Opening external workspace '{resolved}' is strictly FORBIDDEN.",
            caller_path=str(resolved),
            violation_type="UNAUTHORIZED_WORKSPACE",
        )

    records = await projects.list()
    resolved_key = str(resolved).casefold()

    for project in records:
        if str(Path(project.workspace_path).expanduser().resolve()).casefold() == resolved_key:
            resumed = await resume_project(project.project_id)
            return {
                "status": "resumed",
                "registered": False,
                "project": _project_payload(project),
                "resume": resumed,
            }

    base_id = resolved.name.strip() or "project"
    existing_ids = {project.project_id for project in records}
    project_id = base_id

    if project_id in existing_ids:
        import hashlib

        suffix = hashlib.sha1(resolved_key.encode("utf-8")).hexdigest()[:8]
        project_id = f"{base_id}-{suffix}"

    project = ProjectRecord(
        project_id=project_id,
        name=resolved.name or project_id,
        workspace_path=str(resolved),
    )
    await projects.create(project)

    return {
        "status": "registered",
        "registered": True,
        "project": _project_payload(project),
        "checkpoint": None,
        "conversation": None,
        "history": [],
    }

@mcp.tool()
async def save_checkpoint(
    summary: str,
    next_action: str | None = None,
    conversation_id: str | None = None,
    files_changed: list[str] | None = None,
    tests: list[str] | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, object]:
    """Save a persistent checkpoint for the active or specified MyAgent project."""
    await _initialize()

    project = await _resolve_project(project_id)

    if project is None:
        return {
            "status": "no_active_project",
            "checkpoint": None,
        }

    # Determine effective task_id:
    # - If task_id is explicitly provided and non-empty: validate it belongs to this project
    # - If task_id == "": explicitly project-level (no task)
    # - If task_id is None: default to project.active_task_id
    effective_task_id: str | None = None
    if task_id is not None:
        trimmed_task = task_id.strip()
        if trimmed_task:
            task = await tasks.get(trimmed_task)
            if task is None or task.project_id != project.project_id:
                raise SecurityAuthorizationError(
                    f"TASK_IDOR_VIOLATION: Task '{trimmed_task}' does not exist or does not belong "
                    f"to authorized project '{project.project_id}'.",
                    violation_type="TASK_IDOR_VIOLATION",
                )
            effective_task_id = trimmed_task
        else:
            effective_task_id = None
    else:
        effective_task_id = project.active_task_id

    checkpoint = CheckpointRecord(
        checkpoint_id=f"cp-{uuid4().hex}",
        project_id=project.project_id,
        summary=summary,
        next_action=next_action,
        conversation_id=conversation_id,
        task_id=effective_task_id,
        files_changed=tuple(files_changed or ()),
        tests=tuple(tests or ()),
    )
    await checkpoints.save(checkpoint)

    # Maintain ProjectRecord consistency: update last_checkpoint_id
    project.last_checkpoint_id = checkpoint.checkpoint_id
    await projects.update(project)

    return {
        "status": "saved",
        "checkpoint": {
            "checkpoint_id": checkpoint.checkpoint_id,
            "project_id": checkpoint.project_id,
            "task_id": checkpoint.task_id,
            "summary": checkpoint.summary,
            "next_action": checkpoint.next_action,
            "conversation_id": checkpoint.conversation_id,
            "files_changed": list(checkpoint.files_changed),
            "tests": list(checkpoint.tests),
            "created_at": checkpoint.created_at.isoformat(),
        },
    }



def _node_payload(node) -> dict[str, object]:
    return {
        "node_id": node.node_id,
        "kind": node.kind.value,
        "name": node.name,
        "path": node.path,
        "qualified_name": node.qualified_name,
        "language": node.language,
        "line_start": node.line_start,
        "line_end": node.line_end,
        "metadata": node.metadata,
    }





async def _graph_read_guard(project_id: str) -> dict[str, object] | None:
    status = await code_graph.get_graph_status(project_id)
    if status["status"] == "ready":
        return None
    return {
        "status": status["status"],
        "project_id": project_id,
        "graph_status": status,
    }


@mcp.tool()
async def sync_code_graph(project_id: str | None = None) -> dict[str, object]:
    """Explicitly build or incrementally refresh the Code Graph for a project."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project"}
    workspace = Path(project.workspace_path).expanduser().resolve()
    changes = await run_code_graph_sync(project.project_id, workspace, code_graph)
    graph_status = await code_graph.get_graph_status(project.project_id)
    return {
        "status": graph_status["status"],
        "project_id": project.project_id,
        "workspace_path": str(workspace),
        "changes": {
            "added": list(changes.added),
            "modified": list(changes.modified),
            "deleted": list(changes.deleted),
        },
        "graph": graph_status,
    }


@mcp.tool()
async def code_graph_status(project_id: str | None = None) -> dict[str, object]:
    """Return Code Graph indexing status for the active or specified project."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project"}
    status = await code_graph.get_graph_status(project.project_id)
    return {"project_id": project.project_id, **status}



@mcp.tool()
async def find_code_symbol(
    query: str,
    project_id: str | None = None,
    limit: int = 20,
) -> dict[str, object]:
    """Find Code Graph symbols by name or qualified name."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project", "nodes": []}
    guard = await _graph_read_guard(project.project_id)
    if guard is not None:
        return {**guard, "nodes": []}
    nodes = await code_graph.find_nodes(project.project_id, query, limit=limit)
    return {
        "status": "ok",
        "project_id": project.project_id,
        "nodes": [_node_payload(node) for node in nodes],
    }


@mcp.tool()
async def code_dependencies(
    node_id: str,
    project_id: str | None = None,
    depth: int = 1,
) -> dict[str, object]:
    """Return dependencies reachable from a Code Graph node."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project", "nodes": []}
    guard = await _graph_read_guard(project.project_id)
    if guard is not None:
        return {**guard, "nodes": []}
    nodes = await code_graph.dependencies(project.project_id, node_id, depth=depth)
    return {
        "status": "ok",
        "project_id": project.project_id,
        "node_id": node_id,
        "depth": depth,
        "nodes": [_node_payload(node) for node in nodes],
    }


@mcp.tool()
async def code_dependents(
    node_id: str,
    project_id: str | None = None,
    depth: int = 1,
) -> dict[str, object]:
    """Return reverse dependencies reachable from a Code Graph node."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project", "nodes": []}
    guard = await _graph_read_guard(project.project_id)
    if guard is not None:
        return {**guard, "nodes": []}
    nodes = await code_graph.dependents(project.project_id, node_id, depth=depth)
    return {
        "status": "ok",
        "project_id": project.project_id,
        "node_id": node_id,
        "depth": depth,
        "nodes": [_node_payload(node) for node in nodes],
    }


@mcp.tool()
async def impact_analysis(
    node_id: str,
    project_id: str | None = None,
    max_depth: int = 5,
) -> dict[str, object]:
    """Return direct/transitive dependents, related tests, and entrypoints for a symbol."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project"}
    guard = await _graph_read_guard(project.project_id)
    if guard is not None:
        return guard
    impact = await code_graph.impact(project.project_id, node_id, max_depth=max_depth)
    return {
        "status": "ok",
        "project_id": project.project_id,
        "root_node_id": impact.root_node_id,
        "direct_dependents": [_node_payload(node) for node in impact.direct_dependents],
        "transitive_dependents": [_node_payload(node) for node in impact.transitive_dependents],
        "related_tests": [_node_payload(node) for node in impact.related_tests],
        "entrypoints": [_node_payload(node) for node in impact.entrypoints],
    }


@mcp.tool()
async def code_context(node_id: str, project_id: str | None = None, context_lines: int = 0, max_chars: int = 12000, tier: str = "symbol") -> dict[str, object]:
    """Return token-minimal source context for one Code Graph symbol."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {"status": "no_active_project"}
    guard = await _graph_read_guard(project.project_id)
    if guard is not None:
        return guard
    node = await code_graph.get_node(project.project_id, node_id)
    if node is None:
        return {"status": "node_not_found", "node_id": node_id}
    if not node.path or node.line_start is None or node.line_end is None:
        return {"status": "no_source_range", "node": _node_payload(node)}
    root = Path(project.workspace_path).expanduser().resolve()
    path = (root / node.path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Code Graph node path escapes project workspace")
    lines = path.read_text(encoding="utf-8").splitlines()
    pad = max(0, min(context_lines, 20))
    start = max(1, node.line_start - pad)
    end = min(len(lines), node.line_end + pad)
    source = "\n".join(lines[start - 1:end])
    limit = max(256, min(max_chars, 50000))
    truncated = len(source) > limit
    if truncated:
        source = source[:limit]
    if tier not in {"symbol", "neighbors"}:
        raise ValueError("tier must be symbol or neighbors")
    dependencies = ()
    dependents = ()
    if tier == "neighbors":
        dependencies = await code_graph.semantic_dependencies(project.project_id, node.node_id, depth=1)
        dependents = await code_graph.semantic_dependents(project.project_id, node.node_id, depth=1)
    return {"status": "ok", "project_id": project.project_id, "tier": tier, "node": _node_payload(node), "source": source, "start_line": start, "end_line": end, "truncated": truncated, "dependencies": [_node_payload(n) for n in dependencies], "dependents": [_node_payload(n) for n in dependents]}


@mcp.tool()
async def assemble_context(
    step_title: str,
    project_id: str | None = None,
    target_role: str = "backend_coder",
    step_instruction: str = "",
    target_files: list[str] | None = None,
    token_budget: int = 4000,
) -> dict[str, object]:
    """Assembles a unified, bounded context combining CodeGraph AST symbols, L2/L3 Durable Memory,
    UserModel preferences, and UI Design contracts into an optimized specialist prompt section.
    """
    project = await _resolve_project(project_id)
    if project:
        resolved_project_id = project.project_id
    else:
        return {"error": "project_id is mandatory and no active project is selected"}

    try:
        role = AgentRole(target_role.strip().lower())
    except ValueError:
        role = AgentRole.BACKEND_CODER

    ctx = await _unified_context_builder.build_context(
        project_id=resolved_project_id,
        step_title=step_title,
        step_instruction=step_instruction,
        target_role=role,
        target_files=target_files,
        token_budget=token_budget,
    )
    return ctx.to_dict()


def _require_external_coder_runtime() -> None:
    if (
        settings.coder_runtime_mode
        is not CoderRuntimeMode.EXTERNAL
    ):
        raise RuntimeError(
            "operation is only available in external coder runtime mode"
        )


# ─── Phase 3-6 Production MCP Tools ────────────────────────────────────────


@mcp.tool()
async def inspect_prompt(text: str) -> dict[str, object]:
    """Inspect a prompt or user input for prompt injection / jailbreak attempts.

    Returns is_safe, risk_level, detected_patterns, and the sanitized text.
    """
    result = _prompt_defense.inspect(text)
    return {
        "is_safe": result.is_safe,
        "risk_level": result.risk_level,
        "detected_patterns": list(result.detected_patterns),
        "sanitized_text": result.sanitized_text,
    }


@mcp.tool()
async def rewrite_prompt(
    raw_prompt: str,
    target_ai: str = "general",
    context: str = "",
    language: str = "vi",
) -> dict[str, object]:
    """Rewrite and optimize a raw user prompt into a production-grade, load-bearing prompt.

    Supported target_ai values: 'claude', 'gpt', 'reasoning_o3', 'gemini', 'coder_agent', 'general'.
    """
    res = _prompt_rewriter.rewrite(
        raw_prompt=raw_prompt,
        target_ai=target_ai,
        context=context,
        language=language,
    )
    return res.to_dict()


@mcp.tool()
async def scan_security(
    workspace_path: str,
    step_id: str = "manual",
) -> dict[str, object]:
    """Run SAST + secret detection on a workspace directory.

    Returns security findings, severity summary, and whether the security gate passed.
    """
    await _initialize()
    project = await _resolve_project(None)
    if project is None:
        return {
            "step_id": step_id,
            "passed_gate": False,
            "summary": "UNAUTHORIZED_WORKSPACE: No authorized project matching server workspace.",
            "findings": [],
            "total_findings": 0,
        }
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    result = _security_scanner_registry.scan_workspace(root, step_id)
    return {
        "step_id": step_id,
        "passed_gate": result.passed_gate,
        "summary": result.summary,
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "file_path": f.file_path,
                "line_number": f.line_number,
                "description": f.description,
            }
            for f in result.findings
        ],
        "total_findings": len(result.findings),
    }


@mcp.tool()
async def run_red_team_assessment(
    target_id: str = "my_agent_defenses",
    category: str | None = None,
    pass_threshold: float = 5.0,
) -> dict[str, object]:
    """Run adversarial red-team assessment against AI defenses (OWASP LLM Top 10).

    Tests prompt injection, insecure output handling, data exfiltration, DoS, and system prompt leakage.
    If category is provided (e.g. 'LLM01', 'LLM02', 'LLM06'), only runs attacks for that category.
    Returns structured report with findings, CVSS scores, risk score, and pass/fail gate.
    """
    orchestrator = RedTeamOrchestrator(pass_threshold=pass_threshold)

    def default_target(payload: str) -> str:
        insp = _prompt_defense.inspect(payload)
        if not insp.is_safe:
            return f"[REDACTED_PROMPT_INJECTION] Request blocked by security policy. Detected: {', '.join(insp.detected_patterns)}"
        return f"Safely handled: {insp.sanitized_text}"

    if category:
        try:
            owasp_cat = OWASPLLMCategory(category.upper())
        except ValueError:
            return {
                "error": f"Invalid OWASP category '{category}'. Valid: {[c.value for c in OWASPLLMCategory]}",
                "passed": False,
            }
        findings = orchestrator.run_category(default_target, owasp_cat)
        return {
            "target_id": target_id,
            "category": owasp_cat.value,
            "total_findings": len(findings),
            "findings": [f.to_dict() for f in findings],
            "passed": len(findings) == 0,
        }

    report = orchestrator.run_full_assessment(default_target, target_id=target_id)
    return report.to_dict()


@mcp.tool()
async def generate_ui_design_profile(
    project_description: str,
    sector: str = "saas_b2b",
    audience: str = "general",
    mood_keywords: list[str] | None = None,
    surface_mode: str = "earn",
    layout_paradigm: str | None = None,
    workspace_path: str | None = None,
) -> dict[str, object]:
    """Generate an opinionated, creative UI design style profile and persist DESIGN contract.

    Supports Surface Modes (earn, persuade, operate, read) and Spatial Layout Paradigms
    (bento_asymmetric, master_detail, split_hero, feed_stream).

    Enforces the Zero-Pollution Policy: The contract file is strictly stored inside MyAgent's
    internal repository (data/designs/), never polluting the user's project workspace.
    """
    ctx = _ui_design_engine.analyze_context(
        description=project_description,
        sector=sector,
        audience=audience,
        mood_keywords=mood_keywords or [],
        surface_mode=surface_mode,
        layout_paradigm=layout_paradigm,
    )
    profile = _ui_design_engine.generate_style_profile(ctx)

    # Strictly isolate inside MyAgent's data directory with workspace authorization
    project = await _resolve_project(None)
    if project is None:
        raise SecurityAuthorizationError(
            "WORKSPACE_REQUIRED: generate_ui_design_profile requires an authorized workspace/project context. "
            "Shared fallback to unverified directories or default_project is strictly disabled."
        )
    if workspace_path:
        raw_path = Path(workspace_path)
        target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
        _auth_manager.validate_path_containment(target, project)
    project_identifier = project.project_id
    out_file = _design_md_generator.write_to_agent_repo(
        profile=profile,
        project_id=project_identifier,
        project_name=ctx.project_description[:30],
    )
    design_md_path = str(out_file)

    return {
        "visual_language": profile.visual_language,
        "design_rationale": profile.design_rationale,
        "profile": profile.to_dict(),
        "design_md_path": design_md_path,
        "llm_directive": profile.to_llm_design_directive(),
    }


@mcp.tool()
async def audit_ui_design(
    code: str,
) -> dict[str, object]:
    """Audit UI code against 35 anti-pattern & mechanical rules to detect generic AI aesthetic signatures and robotic formulaic layouts.

    Returns originality score (0.0-1.0), verdict (ORIGINAL, ACCEPTABLE, GENERIC, AI_SLOP),
    penalty total, and detected anti-pattern violations with code snippets.
    """
    score = _ui_anti_pattern_detector.score_originality(code)
    return score.to_dict()


@mcp.tool()
async def generate_ui_remediation_prompt(
    code: str,
    sector: str = "edtech_ai",
    surface_mode: str = "earn",
    layout_paradigm: str | None = None,
    dial: str = "diversify",
    project_name: str = "Project",
    target_file: str | None = None,
    mood_keywords: list[str] | None = None,
) -> dict[str, object]:
    """Audit UI code and generate an authoritative, load-bearing LLM prompt to refactor it.

    Detects 35 anti-patterns (WCAG accessibility, touch targets, AI-slop signatures,
    symmetrical card clones, missing living states), binds to target sector archetype
    and surface mode (earn/persuade/operate/read), and applies design dials (diversify/harden/polish/bolder/quieter).
    """
    result = _ui_remediation_generator.generate(
        code=code,
        sector=sector,
        surface_mode=surface_mode,
        layout_paradigm=layout_paradigm,
        dial=dial,
        project_name=project_name,
        target_file=target_file,
        mood_keywords=mood_keywords or [],
    )
    return result.to_dict()


@mcp.tool()
async def verify_step(
    step_id: str,
    workspace_path: str,
    plan_id: str | None = None,
) -> dict[str, object]:
    """Run the full verification gate for a plan step:
    test runner detection + security scan → VerificationGateCoordinator decision.

    Returns APPROVED, REQUEST_REWORK, or REJECTED with reasons.
    """
    await _initialize()
    step, plan, task, project = await _authorize_step(step_id)
    if plan_id and plan.plan_id != plan_id:
        raise SecurityAuthorizationError(
            f"Plan ID mismatch: step '{step_id}' belongs to plan '{plan.plan_id}', not '{plan_id}'",
            violation_type="CROSS_PROJECT_IDOR",
        )
    root = Path(workspace_path).expanduser().resolve()
    _auth_manager.validate_path_containment(root, project)

    from core.handoff_contracts import ImplementationResult

    imp_result = ImplementationResult(
        step_id=step_id,
        summary=f"Verification requested for step {step_id}",
        success=True,
    )
    test_result = await _test_runner_registry.run_tests(root, step_id)
    sec_result = _security_scanner_registry.scan_workspace(root, step_id)
    review = _verification_gate_coordinator.evaluate(
        step_id=step_id,
        implementation_result=imp_result,
        test_result=test_result,
        security_result=sec_result,
    )
    return review.to_dict()


@mcp.tool()
async def run_whole_plan(
    plan_id: str,
    workspace_path: str,
) -> dict[str, object]:
    """Execute an entire plan end-to-end through the WholePlanCoordinator:
    each step is assigned to its specialist role, then passed through
    test gate + security gate + verification gate → APPROVED / repair loop.
    """
    await _initialize()
    plan_rec = await plans.get_plan(plan_id)
    if plan_rec is None:
        return {
            "error": f"plan not found: {plan_id}",
            "plan_completed": False,
            "steps_completed": 0,
            "steps_failed": 0,
            "steps_skipped": 0,
            "review_history": [],
        }
    plan, task, project = await _authorize_plan(plan_id)
    root = Path(workspace_path).expanduser().resolve()
    _auth_manager.validate_path_containment(root, project)

    svc = _plan_service()
    task_svc = _task_service()
    project_id = project.project_id
    task_id = task.task_id

    # Wire real coder runtime step executor
    stack = await _get_coder_stack()
    real_executor = CoderRuntimeStepExecutor(
        coder_stack=stack,
        workspace_path=workspace_path,
        project_id=project_id,
        task_id=task_id,
        plan_id=plan_id,
        agent_run_service=_agent_run_service,
        worker_task_launcher=_ensure_in_process_coder_worker,
        context_builder=_unified_context_builder,
    )

    coordinator = WholePlanCoordinator(
        task_service=task_svc,
        plan_service=svc,
        plan_store=plans,
        step_executor=real_executor,
        test_runner_registry=_test_runner_registry,
        security_scanner_registry=_security_scanner_registry,
        verification_coordinator=_verification_gate_coordinator,
        decision_service=_decision_service,
        max_auto_repair_attempts=3,
    )
    summary = await coordinator.execute_whole_plan(plan_id, Path(workspace_path))
    return {
        "task_id": summary.task_id,
        "plan_id": summary.plan_id,
        "plan_completed": summary.plan_completed,
        "steps_completed": summary.steps_completed,
        "steps_failed": summary.steps_failed,
        "steps_skipped": summary.steps_skipped,
        "steps_auto_repaired": summary.steps_auto_repaired,
        "rollbacks_executed": summary.rollbacks_executed,
        "review_history": summary.review_history,
        "step_telemetry": summary.step_telemetry,
    }


@mcp.tool()
async def run_benchmark(
    workspace_path: str,
    step_id: str = "manual",
    max_latency_ms: float = 100.0,
) -> dict[str, object]:
    """Discover and execute benchmark suites or performance tests in the workspace.

    Verifies function and module latency against an SLO threshold (default 100ms).
    """
    await _initialize()
    project = await _resolve_project(None)
    if project is None:
        return {"step_id": step_id, "error": "UNAUTHORIZED_WORKSPACE: No authorized project matching server workspace."}
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    from core.performance_profiler import BenchmarkRunnerAdapter
    runner = BenchmarkRunnerAdapter()
    result = await runner.run_benchmarks(root, step_id=step_id, max_latency_ms=max_latency_ms)
    return result.to_dict()


@mcp.tool()
async def profile_code(
    code_snippet: str,
    iterations: int = 100,
    max_latency_ms: float = 50.0,
) -> dict[str, object]:
    """Profile a Python code snippet to measure execution latency, memory delta, and ops/sec."""
    from core.performance_profiler import FunctionProfiler
    metric = FunctionProfiler.profile_code(
        code_snippet,
        iterations=iterations,
        slo_threshold_ms=max_latency_ms,
    )
    return metric.to_dict()


@mcp.tool()
async def verify_coverage(
    workspace_path: str,
    step_id: str = "manual",
    min_coverage: float = 80.0,
) -> dict[str, object]:
    """Execute workspace tests and verify code coverage meets the required threshold."""
    await _initialize()
    project = await _resolve_project(None)
    if project is None:
        return {"step_id": step_id, "error": "UNAUTHORIZED_WORKSPACE: No authorized project matching server workspace."}
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    from core.coverage_gate import CoverageGateCoordinator
    test_result = await _test_runner_registry.run_tests(root, step_id)
    coordinator = CoverageGateCoordinator(default_threshold=min_coverage)
    cov_result = coordinator.evaluate_coverage(test_result, min_coverage=min_coverage)
    return cov_result.to_dict()


@mcp.tool()
async def compare_visual_diff(
    baseline_image_path: str,
    current_image_path: str,
    tolerance_percent: float = 0.5,
) -> dict[str, object]:
    """Compare baseline screenshot vs current screenshot to detect visual regressions (pixel-level)."""
    from core.visual_diff import VisualDiffValidator
    result = VisualDiffValidator.compare_images(
        baseline_path=baseline_image_path,
        current_path=current_image_path,
        tolerance_percentage=tolerance_percent,
    )
    return result.to_dict()


@mcp.tool()
async def get_repo_map(
    workspace_path: str,
    max_tokens: int = 1500,
) -> dict[str, object]:
    """Generate a token-budgeted architecture outline (Repo Map) of classes, methods, and functions.

    Helps AI understand the entire codebase architecture without context overflow.
    """
    await _initialize()
    project = await _resolve_project(None)
    if project is None:
        return {"error": "UNAUTHORIZED_WORKSPACE: No authorized project matching server workspace."}
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    from core.repo_map import RepoMapCompactor
    summary = RepoMapCompactor.generate_repo_map(str(root), max_tokens=max_tokens)
    return summary.to_dict()


@mcp.tool()
async def check_goal_drift(
    step_id: str,
    modified_files: list[str],
    allowed_paths: list[str] | None = None,
    original_goal: str = "",
) -> dict[str, object]:
    """Verify that modified files stay strictly within approved scope and do not violate invariant rules."""
    from core.goal_drift_monitor import GoalDriftMonitor
    res = GoalDriftMonitor.evaluate_drift(
        step_id=step_id,
        modified_files=modified_files,
        allowed_paths=allowed_paths,
        original_goal=original_goal,
    )
    return res.to_dict()


@mcp.tool()
async def detect_circular_dependencies(
    workspace_path: str,
) -> dict[str, object]:
    """Scan the workspace for circular import dependency cycles (A -> B -> A)."""
    await _initialize()
    project = await _resolve_project(None)
    if project is None:
        return {"error": "UNAUTHORIZED_WORKSPACE: No authorized project matching server workspace."}
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    from core.code_hygiene import CircularDependencyDetector
    cycles = CircularDependencyDetector.detect_cycles(str(root))
    cycle_strs = [" -> ".join(c) for c in cycles]
    return {
        "passed": len(cycles) == 0,
        "cycles_count": len(cycles),
        "cycles": cycle_strs,
    }


@mcp.tool()
async def detect_dead_code(
    workspace_path: str,
) -> dict[str, object]:
    """Scan the workspace for unused (dead) functions, methods, and classes."""
    await _initialize()
    project = await _resolve_project(None)
    if project is None:
        return {"error": "UNAUTHORIZED_WORKSPACE: No authorized project matching server workspace."}
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    from core.code_hygiene import DeadCodeDetector
    dead_symbols = DeadCodeDetector.detect_dead_code(str(root))
    return {
        "dead_symbols_count": len(dead_symbols),
        "dead_symbols": [{"file": f, "symbol": s, "line": l} for f, s, l in dead_symbols],
    }


@mcp.tool()
async def sanitize_pii(
    text: str,
    mask_only: bool = False,
) -> dict[str, object]:
    """Scan and redact Personally Identifiable Information (PII) such as Credit Cards, Emails, Phones, and National IDs."""
    from core.pii_sanitizer import PiiSanitizer
    result = PiiSanitizer.sanitize(text, mask_only=mask_only)
    return result.to_dict()


@mcp.tool()
async def consolidate_memory(
    session_id: str,
    project_id: str | None = None,
    messages: Sequence[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Consolidate short-term session messages (L0) into L1 working memory.

    Call after a coder session ends to persist session knowledge.
    Persists consolidated canonical records durably into SQLite via DurableMemoryService.
    """
    await _initialize()
    if project_id == "__global__":
        raise ValueError(
            "RESERVED_SENTINEL: project_id='__global__' is a reserved legacy sentinel "
            "and cannot be used for new durable writes. Pass project_id=None or omit it "
            "to use the active project context."
        )
    target_project_id = project_id
    if not target_project_id:
        project = await _resolve_project(None)
        if project and project.project_id:
            target_project_id = project.project_id
        else:
            raise ValueError(
                "PROJECT_CONTEXT_REQUIRED: consolidate_memory requires an explicit project_id "
                "or an active registered project. Fabricating project_id from session_id is forbidden."
            )
    else:
        project = await _resolve_project(target_project_id)
        if project is None:
            raise SecurityAuthorizationError(
                f"UNAUTHORIZED_PROJECT: Access to project '{target_project_id}' is denied.",
                requested_project_id=target_project_id,
            )
        target_project_id = project.project_id

    context = ExecutionContext(
        workspace_id=session_id,
        session_id=session_id,
        project_id=target_project_id,
    )

    resolved_messages: list[dict[str, str]] = list(messages) if messages is not None else []
    if not resolved_messages:
        conversation = await conversations.get(target_project_id, session_id)
        if conversation is not None:
            conv_messages = await conversations.history(target_project_id, session_id, limit=200)
            resolved_messages = [
                {"role": m.role.value, "content": m.content}
                for m in conv_messages
            ]

    # Explicit durable persistence path: consolidate canonical records and persist
    persisted_ids = await _memory_service.consolidate_and_persist(
        _memory_consolidator, context, resolved_messages
    )

    # Legacy entries for compatibility
    entries = _memory_consolidator.consolidate_session(context, resolved_messages)
    promoted = _memory_consolidator.promote_memories(entries, min_recall_for_promotion=3)

    return {
        "session_id": session_id,
        "project_id": target_project_id,
        "memories_created": len(persisted_ids),
        "persisted_ids": list(persisted_ids),
        "memories_promoted": len(promoted),
        "entries": [m.to_dict() for m in entries],
    }


@mcp.tool()
async def search_docs(
    query: str,
    project_id: str | None = None,
    limit: int = 10,
) -> dict[str, object]:
    """Search project documentation (README, AGENTS.md, docs/*.md) using progressive retrieval.

    Enforces strict project authorization and per-request workspace binding.
    """
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {
            "error": "UNAUTHORIZED_OR_NO_PROJECT: Project access is not authorized for caller workspace.",
            "query": query,
            "docs_indexed": 0,
            "results": [],
        }

    docs_knowledge = DocsKnowledgeBackend(workspace_root=Path(project.workspace_path))
    context = ExecutionContext(
        workspace_id=project.project_id,
        project_id=project.project_id,
    )
    results = await docs_knowledge.search(context, query, limit=limit)
    count = docs_knowledge.index_workspace_docs()

    return {
        "query": query,
        "docs_indexed": count,
        "results": list(results),
        "project_id": project.project_id,
    }


# ─── UserModel & Preference Specialization Tools (Phase 05D) ─────────────────


@mcp.tool()
async def get_user_profile(
    project_id: str | None = None,
    user_id: str = "default_user",
    exclude_unverified: bool = True,
) -> dict[str, object]:
    """Retrieve effective user profile and active preferences with provenance verification."""
    await _initialize()
    project = await _require_bound_project(project_id)
    target_project_id = project.project_id

    try:
        profile = await _user_model_engine.get_effective_profile(
            user_id=user_id,
            project_id=target_project_id,
            exclude_unverified=exclude_unverified,
        )
        return {
            "status": "ok",
            "profile": {
                "user_id": profile.user_id,
                "project_id": profile.project_id,
                "active_preferences_count": len(profile.active_preferences),
                "active_preferences": [
                    {
                        "preference_id": item.preference_id,
                        "logical_key": item.logical_key,
                        "category": item.category.value,
                        "content": item.content,
                        "project_id": item.project_id,
                        "scope": item.scope,
                        "source_type": item.source_type.value,
                        "confidence": item.confidence,
                        "importance": item.importance,
                        "provenance_status": item.provenance_status.value,
                        "status": item.status.value,
                        "created_at": item.created_at.isoformat(),
                    }
                    for item in profile.active_preferences
                ],
                "categorized_preferences": {
                    cat: [
                        {
                            "preference_id": item.preference_id,
                            "logical_key": item.logical_key,
                            "content": item.content,
                            "scope": item.scope,
                            "importance": item.importance,
                        }
                        for item in items
                    ]
                    for cat, items in profile.categorized_preferences.items()
                },
                "resolution_reasons": dict(profile.resolution_reasons),
                "as_of": datetime.now(UTC).isoformat(),
            },
        }
    except TruncatedRetrievalError as ex:
        return {
            "status": "error",
            "error_type": "TruncatedRetrievalError",
            "message": str(ex),
        }
    except Exception as ex:
        return {
            "status": "error",
            "error_type": type(ex).__name__,
            "message": str(ex),
        }


@mcp.tool()
async def list_user_preferences(
    project_id: str | None = None,
    category: str | None = None,
    status: str = "active",
    source_type: str | None = None,
    include_global: bool = True,
    limit: int = 100,
) -> dict[str, object]:
    """List stored preferences matching criteria across active project and global scope."""
    await _initialize()
    project = await _require_bound_project(project_id)
    target_project_id = project.project_id

    cat_enum = None
    if category:
        try:
            cat_enum = normalize_preference_category(category)
        except ValueError as ex:
            return {"status": "error", "message": f"Invalid category: {ex}"}

    src_enum = None
    if source_type:
        try:
            src_enum = normalize_preference_source_type(source_type)
        except ValueError as ex:
            return {"status": "error", "message": f"Invalid source_type: {ex}"}

    status_enum = None
    if status and status.lower() != "all":
        try:
            status_enum = MemoryStatus(status.lower())
        except ValueError:
            return {"status": "error", "message": f"Invalid status: {status}"}

    try:
        items = await _user_model_engine.list_preferences(
            project_id=target_project_id,
            category=cat_enum,
            status=status_enum,
            source_type=src_enum,
            include_global=include_global,
            limit=limit,
        )
        return {
            "status": "ok",
            "count": len(items),
            "preferences": [
                {
                    "preference_id": item.preference_id,
                    "logical_key": item.logical_key,
                    "category": item.category.value,
                    "content": item.content,
                    "project_id": item.project_id,
                    "scope": item.scope,
                    "source_type": item.source_type.value,
                    "confidence": item.confidence,
                    "importance": item.importance,
                    "provenance_status": item.provenance_status.value,
                    "status": item.status.value,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
        }
    except Exception as ex:
        return {
            "status": "error",
            "error_type": type(ex).__name__,
            "message": str(ex),
        }


@mcp.tool()
async def set_user_preference(
    logical_key: str,
    content: str,
    category: str = "unclassified",
    project_id: str | None = None,
    scope: str = "project",
    confidence: float = 1.0,
    importance: float = 0.5,
) -> dict[str, object]:
    """Store an explicit user preference with atomic Trusted Provenance attestation."""
    await _initialize()
    if scope == "global":
        _auth_manager.validate_global_memory_payload(content)
        target_project_id = None
    else:
        project = await _require_bound_project(project_id)
        target_project_id = project.project_id

        # Semantic Project Alignment Validation:
        # Detect if content explicitly declares association with a different project
        m = re.search(
            r"(?:gắn với dự án|thuộc dự án|dự án|for project|project\s*:)\s*([a-zA-Z0-9_\-]+)",
            content,
            re.IGNORECASE,
        )
        if m:
            detected_project = m.group(1).strip().lower()
            if detected_project != target_project_id.lower() and detected_project not in ("này", "hiện tại", "this", "active"):
                return {
                    "status": "error",
                    "error_type": "ProjectSemanticMismatchError",
                    "message": (
                        f"PROJECT_SEMANTIC_MISMATCH: Preference content explicitly declares association with project "
                        f"'{detected_project}', but current authorized project is '{target_project_id}'. "
                        f"Cross-project preference storage is rejected to prevent credential and profile contamination."
                    ),
                }

    try:
        cat_enum = normalize_preference_category(category)
    except ValueError as ex:
        return {"status": "error", "message": f"Invalid category: {ex}"}

    try:
        token = TrustedProvenanceAuthority.issue_token(issuer="houhou_front_agent")
        spec = PreferenceSpec(
            logical_key=logical_key,
            content=content,
            category=cat_enum,
            project_id=target_project_id,
            scope=scope,
            confidence=confidence,
            importance=importance,
            source_type=PreferenceSourceType.EXPLICIT_USER,
        )
        item = await _user_model_engine.set_explicit_preference(
            spec=spec,
            boundary_token=token,
        )
        return {
            "status": "ok",
            "preference": {
                "preference_id": item.preference_id,
                "logical_key": item.logical_key,
                "category": item.category.value,
                "content": item.content,
                "project_id": item.project_id,
                "scope": item.scope,
                "source_type": item.source_type.value,
                "provenance_status": item.provenance_status.value,
                "status": item.status.value,
            },
        }
    except (SecurityPolicyViolationError, InvalidPreferenceSourceError, UntrustedProvenanceError) as ex:
        return {
            "status": "error",
            "error_type": type(ex).__name__,
            "message": str(ex),
        }
    except Exception as ex:
        return {
            "status": "error",
            "error_type": type(ex).__name__,
            "message": str(ex),
        }


@mcp.tool()
async def retract_user_preference(
    preference_id: str,
    project_id: str | None = None,
) -> dict[str, object]:
    """Retract a preference by transitioning it to ARCHIVED status (soft tombstone)."""
    await _initialize()
    project = await _require_bound_project(project_id)
    target_project_id = project.project_id

    try:
        item = await _user_model_engine.retract_preference(
            preference_id=preference_id,
            project_id=target_project_id,
        )
        return {
            "status": "ok",
            "retracted_preference_id": item.preference_id,
            "logical_key": item.logical_key,
            "preference_status": item.status.value,
        }
    except (KeyError, PermissionError, ValueError) as ex:
        return {
            "status": "error",
            "error_type": type(ex).__name__,
            "message": str(ex),
        }
    except Exception as ex:
        return {
            "status": "error",
            "error_type": type(ex).__name__,
            "message": str(ex),
        }


@mcp.tool()
async def evaluate_execution(
    task_id: str,
    model_id: str = "unknown",
    duration_seconds: float = 0.0,
    cost_estimate_usd: float = 0.0,
) -> dict[str, object]:
    """Evaluate a completed task execution: compute quality score based on
    test pass rate, security findings, duration, and cost.

    Returns EvaluationJob with quality_score (0–100).
    """
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    resolved_task_id = task.task_id if task else task_id
    job = _evaluation_harness.evaluate_execution(
        model_id=model_id,
        task_id=resolved_task_id,
        duration_seconds=duration_seconds,
        cost_estimate_usd=cost_estimate_usd,
    )
    return job.to_dict()


@mcp.tool()
async def browser_navigate(
    url: str,
    allowed_domains: list[str] | None = None,
    allow_private_network: bool = False,
    capture_screenshots: bool = True,
) -> dict[str, object]:
    """Open a browser session and navigate to a URL.

    Enforces BrowserPolicy (domain whitelist, private network isolation).
    Returns action result with screenshot path if capture is enabled.
    """
    policy = BrowserPolicy(
        allowed_domains=tuple(allowed_domains or []),
        allow_private_network=allow_private_network,
        capture_screenshots=capture_screenshots,
    )
    session = await _playwright_browser.create_session(policy)
    try:
        result = await session.navigate(url)
    finally:
        await session.close()

    return result.to_dict()


# ─── Phase 7 Reliability & Release Governance MCP Tools ─────────────────────


@mcp.tool()
async def get_task_budget(task_id: str) -> dict[str, object]:
    """Get the current execution budget status for a task (cost spent, tokens used, limits)."""
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    resolved_task_id = task.task_id if task else task_id
    budget = _budget_tracker.get_or_create_task_budget(resolved_task_id)
    return budget.to_dict()


@mcp.tool()
async def set_task_budget(
    task_id: str,
    max_cost_usd: float,
    max_tokens: int = 500_000,
    max_duration_seconds: float = 600.0,
) -> dict[str, object]:
    """Set custom execution budget limits for a task."""
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    resolved_task_id = task.task_id if task else task_id
    budget = _budget_tracker.set_task_budget(
        task_id=resolved_task_id,
        max_cost_usd=max_cost_usd,
        max_tokens=max_tokens,
        max_duration_seconds=max_duration_seconds,
    )
    return budget.to_dict()


@mcp.tool()
async def get_provider_health_status() -> dict[str, object]:
    """Return circuit breaker health states and failure stats for all model providers."""
    return _fallback_routing_engine.get_all_health_statuses()


@mcp.tool()
async def generate_sbom(
    workspace_path: str,
    project_name: str | None = None,
) -> dict[str, object]:
    """Generate a Software Bill of Materials (SBOM) in CycloneDX format from project dependencies."""
    await _initialize()
    project = await _require_bound_project()
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    name = project_name or root.name
    manifest = _sbom_generator.generate(root, project_name=name)
    return manifest.to_dict()


@mcp.tool()
async def check_release_readiness(
    workspace_path: str,
    task_id: str,
) -> dict[str, object]:
    """Check release readiness gates for a workspace:
    verifies clean test suite pass, zero critical security findings, and SBOM availability.
    """
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    resolved_task_id = task.task_id if task else task_id
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    return await _release_approval_coordinator.check_release_readiness(
        workspace_path=root,
        task_id=resolved_task_id,
    )


@mcp.tool()
async def create_release_attestation(
    workspace_path: str,
    task_id: str,
    version: str = "v1.0.0",
    approved_by: str = "my-agent-control-plane",
) -> dict[str, object]:
    """Generate a signed cryptographic attestation manifest containing SHA-256 hashes of workspace & SBOM."""
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    resolved_task_id = task.task_id if task else task_id
    raw_path = Path(workspace_path)
    target = raw_path if raw_path.is_absolute() else Path(project.workspace_path) / raw_path
    root = _auth_manager.validate_path_containment(target, project)
    attestation = _release_approval_coordinator.generate_attestation(
        workspace_path=root,
        task_id=resolved_task_id,
        version=version,
        approved_by=approved_by,
    )
    return attestation.to_dict()


# ─── Explicit Handoff Coordinator MCP Tools ─────────────────────────────────


@mcp.tool()
async def request_agent_handoff(
    source_role: str,
    target_role: str,
    task_id: str,
    step_id: str | None = None,
    payload: dict[str, object] | None = None,
    reason: str = "",
) -> dict[str, object]:
    """Perform an explicit coordinator-controlled handoff from one specialist agent role to another.

    Enforces Role Transition Matrix, schema validation, secret sanitization, and prompt safety.
    """
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    resolved_task_id = task.task_id if task else task_id
    if step_id:
        await _authorize_step(step_id, require_exists=False)
    decision = _handoff_coordinator.request_handoff(
        source_role=source_role,
        target_role=target_role,
        task_id=resolved_task_id,
        step_id=step_id,
        payload=payload,
        reason=reason,
    )
    return decision.to_dict()


@mcp.tool()
async def get_allowed_handoff_targets(current_role: str) -> list[str]:
    """Return the list of allowed destination agent roles that the current role can handoff to."""
    return _handoff_coordinator.get_allowed_targets(current_role)


@mcp.tool()
async def get_handoff_history(task_id: str | None = None) -> list[dict[str, object]]:
    """Return the audit trail of agent handoffs, optionally filtered by task_id."""
    return _handoff_coordinator.get_handoff_history(task_id=task_id)





async def _get_coder_stack() -> CoderAgentStack:
    global _coder_stack

    if _coder_stack is None:
        _coder_stack = await build_coder_agent_stack(DB_PATH, approval_store=pending_approvals)

    return _coder_stack


def _on_coder_session_task_done(
    session_id: str,
    task: asyncio.Task[None],
) -> None:
    current = _coder_tasks.get(session_id)

    # Always retrieve an exception so asyncio does not report
    # "Task exception was never retrieved".
    if task.cancelled():
        error = None
    else:
        error = task.exception()

    # A newer consumer may already own this session.
    if current is not task:
        return

    _coder_tasks.pop(session_id, None)

    if error is None:
        _coder_task_failures.pop(session_id, None)
        return

    _coder_task_failures[session_id] = {
        "error_type": type(error).__name__,
        "message": str(error),
    }


def _track_coder_session_task(
    session_id: str,
    task: asyncio.Task[None],
) -> asyncio.Task[None]:
    if not session_id.strip():
        raise ValueError("session_id must not be empty")

    _coder_task_failures.pop(session_id, None)
    _coder_tasks[session_id] = task

    task.add_done_callback(
        lambda completed, sid=session_id: (
            _on_coder_session_task_done(
                sid,
                completed,
            )
        )
    )

    return task

def _on_coder_worker_done(
    session_id: str,
    task: asyncio.Task[None],
) -> None:
    current = _coder_worker_tasks.get(session_id)

    # Always retrieve the exception so asyncio never reports an
    # unobserved task exception, even if this is an obsolete worker.
    if task.cancelled():
        error = None
    else:
        error = task.exception()

    # A newer worker may already own this session. Never let an old
    # callback mutate the new worker's lifecycle state.
    if current is not task:
        return

    _coder_worker_tasks.pop(session_id, None)

    if error is None:
        _coder_worker_failures.pop(session_id, None)
        return

    _coder_worker_failures[session_id] = {
        "error_type": type(error).__name__,
        "message": str(error),
    }


async def _ensure_in_process_coder_worker(
    session_id: str,
) -> asyncio.Task[None] | None:
    if (
        settings.coder_runtime_mode
        is not CoderRuntimeMode.IN_PROCESS
    ):
        return None

    if not session_id.strip():
        raise ValueError("session_id must not be empty")

    async with _coder_worker_lock:
        existing = _coder_worker_tasks.get(session_id)

        if existing is not None and not existing.done():
            return existing

        composition = await _get_model_composition()

        task = asyncio.create_task(
            composition.coder_worker.run_session(session_id)
        )

        _coder_worker_failures.pop(session_id, None)
        _coder_worker_tasks[session_id] = task

        task.add_done_callback(
            lambda completed, sid=session_id: (
                _on_coder_worker_done(
                    sid,
                    completed,
                )
            )
        )

        return task


async def _get_model_composition() -> ModelAgentComposition:
    global _model_composition

    if _model_composition is None:
        stack = await _get_coder_stack()

        _model_composition = await build_model_agent_composition(
            provider=_model_provider,
            runtime=stack.runtime,
            tools=stack.tool_registry,
        )

    return _model_composition


async def _run_coder_session(
    stack: CoderAgentStack,
    session_id: str,
    message: str | None,
    context: ExecutionContext,
) -> None:
    event_stream = (
        stack.agent.run_session(
            session_id,
            message,
            context,
        )
        if message is not None
        else stack.agent.attach_session(
            session_id,
            context,
        )
    )

    async for event in event_stream:
        if context.project_id is None:
            continue

        role: MessageRole | None = None
        content: str | None = None
        metadata: dict[str, object] = {
            "session_id": session_id,
            "source": "my-agent",
            "event_type": event.type.value,
        }

        if event.type is EventType.TOOL_USE:
            tool_call_id = event.payload.get("tool_call_id")
            name = event.payload.get("name")
            arguments = event.payload.get("arguments", {})

            role = MessageRole.ASSISTANT
            content = json.dumps(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "arguments": arguments,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            metadata.update(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                }
            )

        elif event.type is EventType.TOOL_RESULT:
            tool_call_id = event.payload.get("tool_call_id")
            name = event.payload.get("name")
            result = event.payload.get("result", {})

            role = MessageRole.TOOL
            content = json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
            )
            metadata.update(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                }
            )

        elif event.type is EventType.TEXT:
            value = event.payload.get("text")
            if isinstance(value, str) and value.strip():
                role = MessageRole.ASSISTANT
                content = value

        if role is None or content is None:
            continue

        await conversations.add_message(
            MessageRecord(
                message_id=f"msg-{uuid4().hex}",
                project_id=context.project_id,
                conversation_id=session_id,
                role=role,
                content=content,
                metadata=metadata,
            )
        )


async def _start_coder_session_for_project(
    *,
    project: ProjectRecord,
    message: str,
    task_id: str | None,
) -> str:
    stack = await _get_coder_stack()

    context = ExecutionContext(
        workspace_id=project.workspace_path,
        agent_id="coder",
        task_id=task_id,
        project_id=project.project_id,
    )

    session_id = await stack.orchestrator.start_session(
        context
    )

    try:
        conversation = ConversationRecord(
            conversation_id=session_id,
            project_id=project.project_id,
            title=message.strip()[:120] or None,
        )
        await conversations.create(conversation)

        await conversations.add_message(
            MessageRecord(
                message_id=f"msg-{uuid4().hex}",
                project_id=project.project_id,
                conversation_id=session_id,
                role=MessageRole.USER,
                content=message,
                metadata={
                    "session_id": session_id,
                    "source": "my-agent",
                },
            )
        )

        await _ensure_in_process_coder_worker(
            session_id
        )

        task = asyncio.create_task(
            _run_coder_session(
                stack,
                session_id,
                message,
                context,
            )
        )

        _track_coder_session_task(
            session_id,
            task,
        )

    except Exception:
        # start_session() already created durable RUNNING state.
        # Never leave a partially initialized session resumable.
        #
        # Preserve the original setup exception. Orchestrator.cancel()
        # itself persists FAILED if runtime cancellation fails.
        try:
            await stack.orchestrator.cancel(
                session_id
            )
        except Exception:
            pass

        raise

    return session_id


@mcp.tool()
async def start_coder_session(
    message: str,
    project_id: str | None = None,
) -> dict[str, object]:
    """Start a Coder Agent runtime session for a project."""
    await _initialize()

    # Prompt injection defense — check message before execution
    inspection = _prompt_defense.inspect(message)
    if not inspection.is_safe:
        return {
            "status": "rejected_prompt_injection",
            "risk_level": inspection.risk_level,
            "detected_patterns": list(inspection.detected_patterns),
            "session_id": None,
            "message": "Input was flagged as a potential prompt injection attempt and rejected.",
        }

    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "session_id": None,
        }

    session_id = await _start_coder_session_for_project(
        project=project,
        message=message,
        task_id=project.active_task_id,
    )

    return {
        "status": "started",
        "session_id": session_id,
        "project_id": project.project_id,
        "workspace_path": project.workspace_path,
    }


@mcp.tool()
async def get_latest_resumable_coder_session(
    project_id: str | None = None,
) -> dict[str, object]:
    """Return the most recently updated resumable Coder Agent session."""
    await _initialize()

    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "project_id": project_id,
            "session_id": None,
        }

    stack = await _get_coder_stack()
    session = await stack.session_store.get_latest_resumable(
        project.project_id,
        task_id=project.active_task_id,
    )

    if session is None:
        return {
            "status": "not_found",
            "project_id": project.project_id,
            "session_id": None,
        }

    return {
        "status": "ok",
        "project_id": project.project_id,
        "session_id": session.session_id,
        "runtime_id": session.runtime_id,
        "model_id": session.model_id,
        "state": session.state.value,
    }


@mcp.tool()
async def cancel_coder_session(
    session_id: str,
    project_id: str | None = None,
) -> dict[str, object]:
    """Cancel a durable Coder Agent session."""
    await _initialize()

    if not session_id.strip():
        raise ValueError("session_id must not be empty")

    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "session_id": session_id,
            "project_id": project_id,
        }

    stack = await _get_coder_stack()
    durable = await stack.session_store.get(session_id)

    if durable is None:
        return {
            "status": "session_not_found",
            "session_id": session_id,
            "project_id": project.project_id,
        }

    if durable.project_id != project.project_id:
        return {
            "status": "project_mismatch",
            "session_id": session_id,
            "project_id": project.project_id,
            "session_project_id": durable.project_id,
        }

    if durable.terminal:
        return {
            "status": "terminal_session",
            "session_id": session_id,
            "project_id": project.project_id,
            "state": durable.state.value,
        }

    await stack.orchestrator.cancel(session_id)

    stopped = await stack.session_store.get(session_id)

    return {
        "status": "cancelled",
        "session_id": session_id,
        "project_id": project.project_id,
        "state": (
            stopped.state.value
            if stopped is not None
            else SessionState.STOPPED.value
        ),
    }


@mcp.tool()
async def handoff_coder_session(
    project_id: str | None = None,
) -> dict[str, object]:
    """Discover and resume the latest durable Coder Agent session."""
    latest = await get_latest_resumable_coder_session(
        project_id=project_id,
    )

    if latest.get("status") != "ok":
        return latest

    session_id = latest.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return {
            "status": "not_found",
            "project_id": latest.get("project_id"),
            "session_id": None,
        }

    return await resume_coder_session(
        session_id=session_id,
        project_id=project_id,
    )


@mcp.tool()
async def resume_coder_session(
    session_id: str,
    project_id: str | None = None,
) -> dict[str, object]:
    """Resume a durable Coder Agent session after runtime/MCP restart."""
    await _initialize()

    if not session_id.strip():
        raise ValueError("session_id must not be empty")

    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "session_id": session_id,
            "project_id": project_id,
        }

    stack = await _get_coder_stack()
    durable = await stack.session_store.get(session_id)

    if durable is None:
        return {
            "status": "session_not_found",
            "session_id": session_id,
            "project_id": project.project_id,
        }

    if durable.project_id != project.project_id:
        return {
            "status": "project_mismatch",
            "session_id": session_id,
            "project_id": project.project_id,
            "session_project_id": durable.project_id,
        }

    if durable.terminal:
        return {
            "status": "terminal_session",
            "session_id": session_id,
            "project_id": project.project_id,
            "state": durable.state.value,
            "runtime_id": durable.runtime_id,
            "model_id": durable.model_id,
        }

    conversation = await conversations.get(
        project.project_id,
        session_id,
    )

    durable_history = await _resume_assembler.assemble_resume_history(
        project_id=project.project_id,
        session_id=session_id,
        task_id=durable.context.task_id if durable.context else None,
        conversation_store=conversations,
        checkpoint_store=checkpoints,
        task_store=tasks,
    )

    await stack.orchestrator.resume_session(
        session_id,
        durable.context,
    )

    await stack.orchestrator.set_execution_target(
        session_id,
        runtime_id=durable.runtime_id,
        model_id=durable.model_id,
        history=durable_history,
    )

    if durable.state is SessionState.WAITING_APPROVAL:
        stack.agent.restore_pending_approvals(
            session_id,
            durable_history,
            durable.context,
        )

    await _ensure_in_process_coder_worker(
        session_id
    )

    task = asyncio.create_task(
        _run_coder_session(
            stack,
            session_id,
            None,
            durable.context,
        )
    )
    _track_coder_session_task(
        session_id,
        task,
    )

    restored = stack.orchestrator.get_session(session_id)

    return {
        "status": "resumed",
        "session_id": session_id,
        "project_id": project.project_id,
        "runtime_id": restored.runtime_id,
        "model_id": restored.model_id,
    }


@mcp.tool()
async def set_coder_execution_target(
    session_id: str,
    runtime_id: str | None = None,
    model_id: str | None = None,
) -> dict[str, object]:
    """Switch the active runtime/model for an existing Coder Agent session."""
    stack = await _get_coder_stack()

    session = stack.orchestrator.get_session(session_id)

    task_id = session.context.task_id if (session and session.context) else None
    durable_history = await _resume_assembler.assemble_resume_history(
        project_id=session.project_id,
        session_id=session_id,
        task_id=task_id,
        conversation_store=conversations,
        checkpoint_store=checkpoints,
        task_store=tasks,
    )

    await stack.orchestrator.set_execution_target(
        session_id,
        runtime_id=runtime_id,
        model_id=model_id,
        history=durable_history,
    )

    session = stack.orchestrator.get_session(session_id)

    return {
        "status": "switched",
        "session_id": session_id,
        "runtime_id": session.runtime_id,
        "model_id": session.model_id,
    }


@mcp.tool()
async def get_coder_history(
    session_id: str,
    project_id: str | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Return durable MyAgent history for an existing Coder Agent session."""
    await _initialize()

    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")

    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "session_id": session_id,
            "project_id": project_id,
            "history": [],
        }

    conversation = await conversations.get(
        project.project_id,
        session_id,
    )
    if conversation is None:
        return {
            "status": "session_not_found",
            "session_id": session_id,
            "project_id": project.project_id,
            "history": [],
        }

    records = await conversations.history(
        project.project_id,
        session_id,
        limit=limit,
    )

    return {
        "status": "ok",
        "session_id": session_id,
        "project_id": project.project_id,
        "history": [
            {
                "message_id": record.message_id,
                "role": record.role.value,
                "content": record.content,
                "metadata": dict(record.metadata),
                "created_at": record.created_at.isoformat(),
            }
            for record in records
        ],
    }


@mcp.tool()
async def publish_coder_event(
    session_id: str,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Publish a runtime event into an active Coder Agent session."""
    _require_external_coder_runtime()

    stack = await _get_coder_stack()

    try:
        resolved_type = EventType(event_type)
    except ValueError as exc:
        raise ValueError(f"unknown coder event type: {event_type}") from exc

    await stack.runtime.publish_event(
        AgentEvent(
            type=resolved_type,
            session_id=session_id,
            payload=payload or {},
            agent_id="coder",
        )
    )

    return {
        "status": "published",
        "session_id": session_id,
        "event_type": resolved_type.value,
    }


@mcp.tool()
async def next_coder_command(
    session_id: str,
    timeout_seconds: float = 1.0,
) -> dict[str, object]:
    """Poll for the next Coder Agent runtime command without blocking forever."""
    _require_external_coder_runtime()

    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("timeout_seconds must be > 0 and <= 30")

    stack = await _get_coder_stack()

    try:
        command = await asyncio.wait_for(
            stack.runtime.next_command(session_id),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return {
            "status": "idle",
            "session_id": session_id,
            "payload": {},
        }

    return {
        "status": "command",
        "type": command.type.value,
        "session_id": command.session_id,
        "payload": dict(command.payload),
    }


@mcp.tool()
async def reconcile_coder_tool_result(
    session_id: str,
    tool_call_id: str,
    result: dict[str, object],
    project_id: str | None = None,
) -> dict[str, object]:
    """Submit a verified result for an interrupted non-approval tool call."""
    await _initialize()

    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    if not tool_call_id.strip():
        raise ValueError("tool_call_id must not be empty")

    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "session_id": session_id,
            "project_id": project_id,
            "tool_call_id": tool_call_id,
        }

    stack = await _get_coder_stack()

    try:
        session = stack.orchestrator.get_session(session_id)
    except KeyError as exc:
        raise ValueError(
            f"coder session must be resumed before reconciliation: {session_id}"
        ) from exc

    if session.project_id != project.project_id:
        raise ValueError(
            f"coder session belongs to project {session.project_id}, "
            f"not {project.project_id}"
        )

    if session.terminal:
        raise ValueError(
            f"cannot reconcile tool result for terminal session: {session_id}"
        )

    # Approval-required calls must remain on the explicit approval lifecycle.
    if session.state is SessionState.WAITING_APPROVAL:
        raise ValueError(
            "cannot reconcile a tool result while session is waiting for "
            "approval; use resolve_coder_approval"
        )

    conversation = await conversations.get(
        project.project_id,
        session_id,
    )
    if conversation is None:
        raise KeyError(f"conversation not found for session: {session_id}")

    history = await conversations.history(
        project.project_id,
        session_id,
    )

    tool_name: str | None = None
    found_tool_use = False
    found_tool_result = False

    for record in history:
        metadata = record.metadata

        if metadata.get("tool_call_id") != tool_call_id:
            continue

        event_type = metadata.get("event_type")

        if event_type == EventType.TOOL_USE.value:
            if found_tool_use:
                raise ValueError(
                    f"duplicate durable tool_use for tool call: {tool_call_id}"
                )

            found_tool_use = True
            name = metadata.get("name")

            if isinstance(name, str) and name.strip():
                tool_name = name
            else:
                try:
                    payload = json.loads(record.content)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, dict):
                    candidate = payload.get("name")
                    if isinstance(candidate, str) and candidate.strip():
                        tool_name = candidate

        elif event_type == EventType.TOOL_RESULT.value:
            found_tool_result = True

    if not found_tool_use:
        raise KeyError(
            f"no durable tool_use for tool call: {tool_call_id}"
        )

    if found_tool_result:
        raise ValueError(
            f"tool call already has a durable result: {tool_call_id}"
        )

    if tool_name is None:
        raise ValueError(
            f"cannot determine tool name for tool call: {tool_call_id}"
        )

    tool = stack.tool_registry.get(tool_name)

    # Never allow this reconciliation path to bypass explicit approval.
    if ToolPermission.DESTRUCTIVE in tool.permissions:
        raise ValueError(
            "approval-required tool calls cannot be reconciled directly; "
            "use resolve_coder_approval"
        )

    await stack.orchestrator.submit_tool_result(
        session_id,
        tool_call_id,
        result,
        session.context,
    )

    await conversations.add_message(
        MessageRecord(
            message_id=f"msg-{uuid4().hex}",
            project_id=project.project_id,
            conversation_id=session_id,
            role=MessageRole.TOOL,
            content=json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
            ),
            metadata={
                "session_id": session_id,
                "source": "my-agent",
                "event_type": EventType.TOOL_RESULT.value,
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "reconciled": True,
            },
        )
    )

    return {
        "status": "reconciled",
        "session_id": session_id,
        "project_id": project.project_id,
        "tool_call_id": tool_call_id,
        "tool": tool_name,
        "result": dict(result),
    }


@mcp.tool()
async def resolve_coder_approval(
    session_id: str,
    tool_call_id: str,
    approved: bool,
) -> dict[str, object]:
    """Approve or reject a pending Coder Agent tool call."""
    stack = await _get_coder_stack()
    event = await stack.agent.resolve_tool_approval(
        session_id,
        tool_call_id,
        approved=approved,
        submit_result=False,
    )

    session = stack.orchestrator.get_session(session_id)
    result = event.payload.get("result", {})
    name = event.payload.get("name")

    if session.project_id is not None:
        conversation = await conversations.get(
            session.project_id,
            session_id,
        )
        if conversation is not None:
            await conversations.add_message(
                MessageRecord(
                    message_id=f"msg-{uuid4().hex}",
                    project_id=session.project_id,
                    conversation_id=session_id,
                    role=MessageRole.TOOL,
                    content=json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    metadata={
                        "session_id": session_id,
                        "source": "my-agent",
                        "event_type": EventType.TOOL_RESULT.value,
                        "tool_call_id": tool_call_id,
                        "name": name,
                    },
                )
            )

    # Release the model continuation only after the durable
    # TOOL_RESULT has been written. This preserves causal
    # conversation ordering in IN_PROCESS mode.
    await stack.orchestrator.submit_tool_result(
        session_id,
        tool_call_id,
        result,
        session.context,
    )

    return {
        "status": "approved" if approved else "rejected",
        "session_id": session_id,
        "tool_call_id": tool_call_id,
        "result": dict(result),
    }


# ─── 3-Layer Architecture & Decision Subsystem MCP Tools ─────────────────────


@mcp.tool()
async def create_decision(
    task_id: str,
    prompt: str,
    severity: str,
    options: list[dict[str, Any]],
    step_id: str | None = None,
) -> dict[str, object]:
    """Create a durable open decision for a task requiring user input."""
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=False)
    if step_id:
        await _authorize_step(step_id)

    sev = DecisionSeverity.MEDIUM
    try:
        sev = DecisionSeverity(severity.lower())
    except Exception:
        sev = DecisionSeverity.MEDIUM

    decision_options = [DecisionOption.from_dict(opt) for opt in options]
    rec = await _decision_service.create_decision(
        task_id=task.task_id if task else task_id,
        prompt=prompt,
        severity=sev,
        options=decision_options,
        step_id=step_id,
    )
    return {
        "status": "created",
        "decision": rec.to_dict(),
    }


@mcp.tool()
async def get_decision(decision_id: str) -> dict[str, object]:
    """Retrieve details and options for a specific decision record."""
    await _initialize()
    rec, task, project = await _authorize_decision(decision_id)
    return {
        "status": "ok",
        "decision": rec.to_dict(),
    }


@mcp.tool()
async def list_open_decisions(task_id: str | None = None) -> dict[str, object]:
    """List all currently OPEN decisions awaiting user choice."""
    await _initialize()
    if task_id:
        task, project = await _authorize_task(task_id, require_exists=False)
        effective_task_id = task.task_id if task else task_id
        decs = await _decision_service.list_open_decisions(task_id=effective_task_id)
    else:
        bound_project = await _require_bound_project()
        all_open = await _decision_service.list_open_decisions()
        project_tasks = await _task_service().list_tasks(bound_project.project_id)
        project_task_ids = {t.task_id for t in project_tasks}
        decs = [d for d in all_open if d.task_id in project_task_ids]

    return {
        "status": "ok",
        "decisions": [d.to_dict() for d in decs],
    }


@mcp.tool()
async def resolve_decision(
    decision_id: str,
    selected_option_id: str,
    rationale: str | None = None,
) -> dict[str, object]:
    """Resolve an open decision with the selected option ID."""
    await _initialize()
    rec, task, project = await _authorize_decision(decision_id)
    try:
        resolved = await _decision_service.resolve_decision(
            decision_id,
            selected_option_id=selected_option_id,
            rationale=rationale,
        )
        return {
            "status": "resolved",
            "decision": resolved.to_dict(),
        }
    except Exception as e:
        return {
            "status": "error",
            "decision_id": decision_id,
            "error": str(e),
        }


@mcp.tool()
async def cancel_decision(
    decision_id: str,
    rationale: str | None = None,
) -> dict[str, object]:
    """Cancel an open decision."""
    await _initialize()
    rec, task, project = await _authorize_decision(decision_id)
    try:
        cancelled = await _decision_service.cancel_decision(
            decision_id,
            rationale=rationale,
        )
        return {
            "status": "cancelled",
            "decision": cancelled.to_dict(),
        }
    except Exception as e:
        return {
            "status": "error",
            "decision_id": decision_id,
            "error": str(e),
        }


@mcp.tool()
async def front_agent_request(
    user_input: str,
    project_id: str | None = None,
    task_id: str | None = None,
    level: str = "normal",
    selected_decision: dict[str, str] | None = None,
    approval_response: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Layer 1 User-Facing Front Agent entry point for MyAgent."""
    await _initialize()
    project = await _resolve_project(project_id)
    if project is None:
        return {
            "status": "no_active_project",
            "message": "Không tìm thấy active project nào.",
        }

    pres_level = PresentationLevel.NORMAL
    try:
        pres_level = PresentationLevel(level.lower())
    except Exception:
        pres_level = PresentationLevel.NORMAL

    control_plane = MyAgentControlPlane(
        task_store=tasks,
        task_service=_task_service(),
        plan_store=plans,
        plan_service=PlanService(plan_store=plans, task_store=tasks),
        decision_service=_decision_service,
        agent_run_service=_agent_run_service,
        approval_store=pending_approvals,
        user_model_engine=_user_model_engine,
    )
    front_agent = FrontAgent(
        control_plane=control_plane,
        user_model_engine=_user_model_engine,
    )

    req = FrontAgentRequest(
        user_input=user_input,
        project_id=project.project_id,
        task_id=task_id,
        level=pres_level,
        selected_decision=selected_decision,
        approval_response=approval_response,
    )

    res = await front_agent.handle_user_request(req)
    return {
        "status": "ok",
        "response": res.to_dict(),
        "display": res.format_user_display(),
    }


# ─── Cross-Agent Durable Execution Ledger & Handoff MCP Tools ────────────────


@mcp.tool()
async def start_agent_run(
    task_id: str,
    agent_id: str,
    execution_role: str,
    project_id: str | None = None,
    plan_id: str | None = None,
    step_id: str | None = None,
    model_id: str | None = None,
    runtime_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Start and persist a new AgentRunRecord in the durable execution ledger."""
    await _initialize()
    task, project = await _authorize_task(task_id, require_exists=True)
    if plan_id:
        await _authorize_plan(plan_id)
    if step_id:
        await _authorize_step(step_id)

    try:
        role = AgentRole(execution_role.lower())
    except ValueError:
        role = AgentRole.BACKEND_CODER

    run = await _agent_run_service.create_run(
        project_id=project.project_id,
        task_id=task.task_id,
        agent_id=agent_id,
        execution_role=role,
        plan_id=plan_id,
        step_id=step_id,
        model_id=model_id,
        runtime_id=runtime_id,
        session_id=session_id,
        run_id=run_id,
    )
    running = await _agent_run_service.start_run(
        run.run_id,
        session_id=session_id,
        runtime_id=runtime_id,
        model_id=model_id,
    )
    return {
        "status": "started",
        "run": running.to_dict(),
    }


@mcp.tool()
async def complete_agent_run(
    run_id: str,
    result: dict[str, Any],
) -> dict[str, object]:
    """Record terminal completion of an AgentRun with machine-readable AgentWorkResult."""
    await _initialize()
    run, project = await _authorize_agent_run(run_id)
    try:
        # Mark client/external-supplied execution evidence as untrusted
        # so external MCP callers cannot forge SYSTEM_VERIFIED_EXECUTION
        cleaned_result = dict(result)
        meta = dict(cleaned_result.get("metadata", {})) if isinstance(cleaned_result.get("metadata"), dict) else {}
        ev = dict(meta.get("execution_evidence", {})) if isinstance(meta.get("execution_evidence"), dict) else {}
        ev["untrusted_client_submission"] = True
        meta["execution_evidence"] = ev
        cleaned_result["metadata"] = meta

        work_res = AgentWorkResult.from_dict(cleaned_result)
        completed = await _agent_run_service.complete_run(run.run_id, work_res)

        # Also emit a conversation handoff projection message for visibility
        msg = AgentHandoffMessage.from_run_record(completed)
        if completed.session_id:
            await conversations.add_message(
                MessageRecord(
                    message_id=f"msg-{uuid4().hex[:8]}",
                    project_id=completed.project_id,
                    conversation_id=completed.session_id,
                    role=MessageRole.SYSTEM,
                    content=msg.render_markdown(),
                    metadata=msg.to_conversation_metadata(),
                )
            )

        return {
            "status": "completed",
            "run": completed.to_dict(),
            "handoff_summary": msg.render_markdown(),
        }
    except Exception as e:
        return {
            "status": "error",
            "run_id": run_id,
            "error": str(e),
        }


@mcp.tool()
async def fail_agent_run(
    run_id: str,
    reason: str = "",
    result: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Record failure of an AgentRun."""
    await _initialize()
    run, project = await _authorize_agent_run(run_id)
    try:
        work_res = AgentWorkResult.from_dict(result) if result else None
        failed = await _agent_run_service.fail_run(run.run_id, result=work_res, reason=reason)
        return {
            "status": "failed",
            "run": failed.to_dict(),
        }
    except Exception as e:
        return {
            "status": "error",
            "run_id": run_id,
            "error": str(e),
        }


@mcp.tool()
async def get_agent_run(run_id: str) -> dict[str, object]:
    """Retrieve an AgentRunRecord by run_id."""
    await _initialize()
    run, project = await _authorize_agent_run(run_id)
    return {
        "status": "ok",
        "run": run.to_dict(),
    }


@mcp.tool()
async def list_agent_runs(
    task_id: str | None = None,
    step_id: str | None = None,
    plan_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """List historical AgentRun records from the durable execution ledger."""
    await _initialize()
    runs: tuple[AgentRunRecord, ...] = ()
    if step_id:
        step, plan, task, project = await _authorize_step(step_id)
        runs = await agent_runs.list_for_step(step.step_id)
    elif plan_id:
        plan, task, project = await _authorize_plan(plan_id)
        runs = await agent_runs.list_for_plan(plan.plan_id)
    elif task_id:
        task, project = await _authorize_task(task_id, require_exists=True)
        runs = await agent_runs.list_for_task(task.task_id)
    else:
        project = await _require_bound_project(project_id)
        runs = await agent_runs.list_recent_for_project(project.project_id)

    return {
        "status": "ok",
        "runs": [r.to_dict() for r in runs],
    }


@mcp.tool()
async def get_agent_handoff_context(
    task_id: str,
    target_role: str = "backend_coder",
    plan_id: str | None = None,
    step_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Retrieve prioritized, bounded handoff context from previous agents for the incoming agent."""
    await _initialize()
    try:
        task, project = await _authorize_task(task_id, require_exists=True)
        if plan_id:
            await _authorize_plan(plan_id)
        if step_id:
            await _authorize_step(step_id)
    except (SecurityAuthorizationError, KeyError):
        return {"status": "error", "error": "Task not found in active project scope."}

    try:
        role = AgentRole(target_role.lower())
    except ValueError:
        role = AgentRole.BACKEND_CODER

    try:
        ctx = await _handoff_context_builder.build_context(
            project_id=project.project_id,
            task_id=task.task_id,
            target_role=role,
            plan_id=plan_id,
            step_id=step_id,
        )
    except ValueError:
        return {"status": "error", "error": "Project scope validation failed."}

    return {
        "status": "ok",
        "handoff_context": ctx.to_dict(),
        "prompt_brief": ctx.format_prompt_context(),
    }


async def _resolve_repo(repo: str | None = None, project_id: str | None = None) -> tuple[str, str]:
    if repo and "/" in repo:
        parts = repo.strip().split("/", 1)
        return parts[0].strip(), parts[1].strip()

    project = await _resolve_project(project_id)
    ws_path = Path(project.workspace_path) if project and project.workspace_path else Path.cwd()
    detected = _git_review_coordinator.detect_repo_from_git(ws_path)
    if detected:
        return detected
    raise ValueError(
        "Could not automatically detect GitHub repository from workspace. Please provide 'repo' as 'owner/repo'."
    )


@mcp.tool()
async def fetch_pr_review_threads(
    pull_number: int,
    repo: str | None = None,
    project_id: str | None = None,
    unresolved_only: bool = False,
) -> dict[str, object]:
    """Fetch inline code review comment threads and timeline comments for a Pull Request."""
    await _initialize()
    owner, repo_name = await _resolve_repo(repo, project_id)
    pr_info = await _git_review_coordinator.get_pull_request(owner, repo_name, pull_number)
    threads = await _git_review_coordinator.fetch_pr_threads(owner, repo_name, pull_number)
    issue_comments = await _git_review_coordinator.fetch_issue_comments(owner, repo_name, pull_number)

    if unresolved_only:
        threads = [t for t in threads if not t.resolved]

    formatted = _git_review_coordinator.format_pr_overview_markdown(
        pr_info=pr_info,
        threads=threads,
        owner=owner,
        repo=repo_name,
    )

    return {
        "status": "ok",
        "repo": f"{owner}/{repo_name}",
        "pull_number": pull_number,
        "title": pr_info.get("title", ""),
        "state": pr_info.get("state", ""),
        "author": pr_info.get("user", {}).get("login", ""),
        "threads_count": len(threads),
        "formatted_presentation": formatted,
        "review_threads": [t.to_dict() for t in threads],
        "timeline_comments_count": len(issue_comments),
        "timeline_comments": issue_comments,
    }


@mcp.tool()
async def draft_review_reply(
    solution_summary: str,
    code_changes: str | None = None,
    commit_hash: str | None = None,
    next_steps: str | None = None,
) -> dict[str, object]:
    """Compose a structured, professional markdown reply following engineering review etiquette."""
    reply_text = _git_review_coordinator.draft_reply(
        solution_summary=solution_summary,
        code_changes=code_changes,
        commit_hash=commit_hash,
        next_steps=next_steps,
    )
    return {
        "status": "ok",
        "draft_reply": reply_text,
        "note": "Review this draft and call post_review_reply once approved.",
    }


@mcp.tool()
async def post_review_reply(
    pull_number: int,
    comment_id: int,
    reply_text: str,
    repo: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Post an approved reply to a specific review comment thread on GitHub."""
    await _initialize()
    owner, repo_name = await _resolve_repo(repo, project_id)
    result = await _git_review_coordinator.post_thread_reply(
        owner=owner,
        repo=repo_name,
        pull_number=pull_number,
        comment_id=comment_id,
        body=reply_text,
    )
    return {
        "status": "ok",
        "message": f"Successfully replied to comment {comment_id} on PR #{pull_number}.",
        "reply_id": result.get("id"),
        "html_url": result.get("html_url"),
    }


@mcp.tool()
async def post_pr_review_summary(
    pull_number: int,
    summary_text: str,
    repo: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Post a general summary comment on the Pull Request conversation timeline."""
    await _initialize()
    owner, repo_name = await _resolve_repo(repo, project_id)
    result = await _git_review_coordinator.post_general_comment(
        owner=owner,
        repo=repo_name,
        pull_number=pull_number,
        body=summary_text,
    )
    return {
        "status": "ok",
        "message": f"Successfully posted summary comment to PR #{pull_number}.",
        "comment_id": result.get("id"),
        "html_url": result.get("html_url"),
    }


@mcp.tool()
async def post_batch_review_replies(
    pull_number: int,
    replies: list[dict[str, object]],
    summary_text: str | None = None,
    repo: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Post replies to multiple review comment threads in a single batch call with single confirmation."""
    await _initialize()
    owner, repo_name = await _resolve_repo(repo, project_id)
    result = await _git_review_coordinator.post_batch_thread_replies(
        owner=owner,
        repo=repo_name,
        pull_number=pull_number,
        replies=replies,
        summary_text=summary_text,
    )
    return {
        "status": result.get("status", "ok"),
        "repo": f"{owner}/{repo_name}",
        "pull_number": pull_number,
        "total_replies": result.get("total_replies", 0),
        "success_count": result.get("success_count", 0),
        "failed_count": result.get("failed_count", 0),
        "successful_replies": result.get("successful_replies", []),
        "failed_replies": result.get("failed_replies", []),
        "summary_comment": result.get("summary_comment"),
        "message": f"Batch replied to {result.get('success_count', 0)}/{result.get('total_replies', 0)} comments on PR #{pull_number}.",
    }


@mcp.tool()
async def submit_pr_review(
    pull_number: int,
    body: str,
    event: str = "COMMENT",
    comments: list[dict[str, object]] | None = None,
    commit_id: str | None = None,
    repo: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Submit a complete Pull Request review with review verdict and inline comments in one atomic action."""
    await _initialize()
    owner, repo_name = await _resolve_repo(repo, project_id)
    result = await _git_review_coordinator.submit_pull_request_review(
        owner=owner,
        repo=repo_name,
        pull_number=pull_number,
        body=body,
        event=event,
        comments=comments,
        commit_id=commit_id,
    )
    return {
        "status": "ok",
        "repo": f"{owner}/{repo_name}",
        "pull_number": pull_number,
        "review_id": result.get("id"),
        "state": result.get("state"),
        "html_url": result.get("html_url"),
        "message": f"Successfully submitted {event.upper()} review on PR #{pull_number}.",
    }


@mcp.tool()
async def package_targeted_error(
    file_path: str,
    symbol_name: str | None = None,
    line_number: int | None = None,
    error_description: str = "",
    context_lines: int = 40,
) -> dict[str, object]:
    """Surgically package minimal, safe error context for a specific file, function or line to paste into Gemini Pro Web."""
    await _initialize()
    from core.context_disclosure import BridgeError
    from tools.error_context_packager import package_targeted_error as _pkg_targeted

    project = await _require_bound_project()
    ws_root = Path(project.workspace_path).resolve()
    target_path = Path(file_path).expanduser()
    if not target_path.is_absolute():
        target_path = ws_root / target_path
    _auth_manager.validate_path_containment(target_path, project)

    try:
        md = _pkg_targeted(
            file_path=str(target_path),
            symbol_name=symbol_name,
            line_number=line_number,
            error_description=error_description,
            context_lines=context_lines,
            workspace_root=ws_root,
        )
        return {
            "status": "ok",
            "file_path": file_path,
            "char_count": len(md),
            "error_context_markdown": md,
        }
    except (ValueError, BridgeError, OSError, RuntimeError, FileNotFoundError) as ex:
        return {
            "status": "error",
            "message": str(ex),
        }


@mcp.tool()
async def package_last_test_failure(
    test_output: str | None = None,
    log_file: str | None = None,
    context_lines: int = 40,
) -> dict[str, object]:
    """Parse the most recent test failure and surgically package its context to paste into Gemini Pro Web."""
    await _initialize()
    from core.context_disclosure import BridgeError
    from tools.error_context_packager import package_last_test_failure as _pkg_test

    project = await _require_bound_project()
    ws_root = Path(project.workspace_path).resolve()
    if log_file:
        log_path = Path(log_file).expanduser()
        if not log_path.is_absolute():
            log_path = ws_root / log_path
        _auth_manager.validate_path_containment(log_path, project)

    try:
        md = _pkg_test(
            test_output=test_output,
            log_file=log_file,
            context_lines=context_lines,
            workspace_root=ws_root,
        )
        return {
            "status": "ok",
            "char_count": len(md),
            "error_context_markdown": md,
        }
    except (ValueError, BridgeError, OSError, RuntimeError, FileNotFoundError) as ex:
        return {
            "status": "error",
            "message": str(ex),
        }


# ─── MCP Prompt Templates (Registered for Slash Commands in IDE) ─────────────


@mcp.prompt(name="menu", description="Bảng điều khiển trung tâm và danh mục kỹ năng MyAgent")
def prompt_menu() -> str:
    """Hiển thị bảng điều khiển menu trung tâm của MyAgent."""
    return "Hiển thị bảng menu điều khiển trung tâm MyAgent và danh sách các tác vụ có thể thực hiện."


@mcp.prompt(name="review", description="Đọc và phản hồi các review comments trên Pull Request")
def prompt_review(pull_number: str = "") -> str:
    """Kích hoạt Review Specialist để đọc và trả lời review comments."""
    pr_text = f" cho PR #{pull_number}" if pull_number else ""
    return f"Đọc toàn bộ review threads{pr_text}, phân tích code liên quan và hỗ trợ soạn thảo phản hồi phê duyệt."


@mcp.prompt(name="ui_design", description="Thiết kế UI component, giao diện web chuẩn Accessibility (a11y)")
def prompt_ui_design(requirement: str = "") -> str:
    """Kích hoạt UI Specialist để thiết kế giao diện."""
    req_text = f": {requirement}" if requirement else ""
    return f"Kích hoạt UI Coder Specialist Agent để thiết kế giao diện/component tuân thủ design tokens và chuẩn a11y{req_text}."


@mcp.prompt(name="plan", description="Lập kế hoạch đa bước và tự động điều phối multi-agent thực thi")
def prompt_plan(objective: str = "") -> str:
    """Kích hoạt Planner Agent lập và chạy plan."""
    obj_text = f": {objective}" if objective else ""
    return f"Kích hoạt Planner Agent để phân tích yêu cầu, tạo PlanSteps phân quyền chuyên môn và chạy WholePlanCoordinator{obj_text}."


@mcp.prompt(name="code_graph", description="Quét AST và tra cứu Symbol, Dependencies trong Code Graph")
def prompt_code_graph(query: str = "") -> str:
    """Quét Code Graph và tra cứu AST."""
    q_text = f" cho '{query}'" if query else ""
    return f"Đồng bộ Code Graph và tra cứu AST symbols/dependencies/impact analysis{q_text}."


@mcp.prompt(name="security_scan", description="Quét lỗ hổng tĩnh SAST và phát hiện rò rỉ API Keys / Secrets")
def prompt_security_scan() -> str:
    """Quét bảo mật SAST và secret."""
    return "Chạy quét bảo mật SAST và kiểm tra phát hiện leak secrets/API keys trên workspace hiện tại."


@mcp.prompt(name="test_verify", description="Chạy kiểm thử tự động và kích hoạt Verification Gate")
def prompt_test_verify() -> str:
    """Chạy kiểm thử và nghiệm thu."""
    return "Chạy toàn bộ test runner của dự án và kích hoạt VerificationGateCoordinator để đánh giá nghiệm thu."


@mcp.prompt(name="handoff", description="Bàn giao nhiệm vụ giữa 2 AI chuyên môn có Guardrail kiểm duyệt")
def prompt_handoff(from_role: str = "backend_coder", to_role: str = "tester") -> str:
    """Bàn giao công việc giữa các AI."""
    return f"Thực hiện bàn giao nhiệm vụ từ vai trò {from_role} sang vai trò {to_role} qua HandoffCoordinator."


@mcp.prompt(name="release_sbom", description="Kiểm tra điều kiện phát hành, xuất SBOM CycloneDX và ký SHA-256")
def prompt_release_sbom(version: str = "v1.0.0") -> str:
    """Kiểm tra release và sinh SBOM."""
    return f"Kiểm tra cổng phát hành release readiness, sinh SBOM CycloneDX và tạo chữ ký attestation cho phiên bản {version}."


@mcp.prompt(name="status_budget", description="Xem trạng thái hệ thống, chi phí token USD và circuit breakers")
def prompt_status_budget() -> str:
    """Xem trạng thái hệ thống và budget."""
    return "Xem trạng thái hoạt động của MyAgent, sức khỏe của các AI provider và ngân sách chi phí token của task."


if __name__ == '__main__':
    mcp.run()
